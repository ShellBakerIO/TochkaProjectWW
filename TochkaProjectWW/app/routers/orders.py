from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc
from typing import List, Optional
from decimal import Decimal
from .. import models, schemas
from ..dependencies import get_db, get_current_user, get_current_admin
from uuid import UUID
import datetime


router = APIRouter(prefix="/api/v1/public", tags=["public"])


@router.get("/orderbook/{ticker}", response_model=schemas.OrderBookOut)
def get_orderbook(
    ticker: str, 
    limit: int = 10,
    db: Session = Depends(get_db)
):
    """
    Получить текущий биржевой стакан (книгу заявок) для указанного инструмента.
    
    Возвращает списки активных заявок на покупку (bids) и продажу (asks),
    отсортированные по наиболее выгодной цене.
    """
    instrument = db.query(models.Instrument).filter(models.Instrument.ticker == ticker).first()
    if not instrument:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Инструмент с тикером {ticker} не найден"
        )
    
    bids = db.query(models.Order).filter(
        models.Order.ticker == ticker,
        models.Order.side == models.OrderSide.BUY,
        models.Order.status == models.OrderStatus.OPEN
    ).order_by(desc(models.Order.price)).limit(limit).all()
    
    asks = db.query(models.Order).filter(
        models.Order.ticker == ticker,
        models.Order.side == models.OrderSide.SELL,
        models.Order.status == models.OrderStatus.OPEN
    ).order_by(asc(models.Order.price)).limit(limit).all()
    
    bid_levels = {}
    for bid in bids:
        price = bid.price
        if price in bid_levels:
            bid_levels[price] += bid.quantity - bid.filled_quantity
        else:
            bid_levels[price] = bid.quantity - bid.filled_quantity

    ask_levels = {}
    for ask in asks:
        price = ask.price
        if price in ask_levels:
            ask_levels[price] += ask.quantity - ask.filled_quantity
        else:
            ask_levels[price] = ask.quantity - ask.filled_quantity

    result = {
        "bids": [{"price": price, "quantity": qty} for price, qty in sorted(bid_levels.items(), key=lambda x: x[0], reverse=True)],
        "asks": [{"price": price, "quantity": qty} for price, qty in sorted(ask_levels.items(), key=lambda x: x[0])]
    }
    
    return result


protected_router = APIRouter(prefix="/api/v1/order", tags=["order"])


@protected_router.post("", response_model=schemas.OrderOut)
def create_order(
    order: schemas.OrderCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Создать новую заявку на покупку или продажу.
    
    Тип ордера определяется автоматически по наличию цены:
    - Если цена указана (не NULL), создается лимитный ордер
    - Если цена не указана (NULL), создается рыночный ордер, цена будет определена при исполнении
    """
    instrument = db.query(models.Instrument).filter(models.Instrument.ticker == order.ticker).first()
    if not instrument:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Инструмент с тикером {order.ticker} не найден"
        )
    
    order_type = schemas.OrderType.LIMIT if order.price is not None else schemas.OrderType.MARKET
    
    rub_instrument = db.query(models.Instrument).filter(models.Instrument.ticker == "RUB").first()
    if not rub_instrument:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Базовая валюта RUB не найдена в системе"
        )
    
    if order.side == schemas.OrderSide.BUY:
        # Находим рублевый баланс пользователя
        rub_balance = db.query(models.Balance).filter(
            models.Balance.user_id == current_user.id,
            models.Balance.ticker == "RUB"
        ).first()
        
        if not rub_balance:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="У вас нет баланса в RUB"
            )
        
        if order_type == schemas.OrderType.LIMIT:
            required_amount = order.price * order.quantity
            if rub_balance.amount < required_amount:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Недостаточно средств. Требуется: {required_amount} RUB, доступно: {rub_balance.amount} RUB"
                )
        else:
            if rub_balance.amount <= 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Недостаточно средств для рыночной покупки"
                )
    else:
        asset_balance = db.query(models.Balance).filter(
            models.Balance.user_id == current_user.id,
            models.Balance.ticker == order.ticker
        ).first()
        
        if not asset_balance or asset_balance.amount < order.quantity:
            available = asset_balance.amount if asset_balance else 0
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Недостаточно {order.ticker}. Требуется: {order.quantity}, доступно: {available}"
            )
    
    new_order = models.Order(
        user_id=current_user.id,
        instrument_id=instrument.id,
        ticker=order.ticker,
        order_type=order_type,
        side=order.side,
        quantity=order.quantity,
        price=order.price,
        filled_quantity=0,
        status=models.OrderStatus.OPEN
    )
    
    db.add(new_order)
    db.commit()
    db.refresh(new_order)
    
    if order.side == schemas.OrderSide.BUY and order_type == schemas.OrderType.LIMIT:
        rub_balance.amount -= order.price * order.quantity
        db.commit()
    elif order.side == schemas.OrderSide.SELL:
        asset_balance.amount -= order.quantity
        db.commit()
    
    try:
        execute_matching(db, new_order.id)
    except Exception as e:
        cancel_order_and_return_funds(db, new_order.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при выполнении матчинга: {str(e)}"
        )
    
    db.refresh(new_order)
    
    return new_order


@protected_router.get("", response_model=List[schemas.OrderOut])
def list_orders(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить список всех заявок текущего пользователя.
    """
    orders = db.query(models.Order).filter(models.Order.user_id == current_user.id).all()
    return orders


@protected_router.get("/{order_id}", response_model=schemas.OrderOut)
def get_order(
    order_id: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить информацию о конкретной заявке текущего пользователя.
    """
    order = db.query(models.Order).filter(
        models.Order.id == order_id,
        models.Order.user_id == current_user.id
    ).first()
    
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Заявка с ID {order_id} не найдена или не принадлежит текущему пользователю"
        )
    
    return order


@protected_router.delete("/{order_id}", response_model=schemas.Ok)
def cancel_order(
    order_id: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Отменить открытую заявку.
    
    Возвращает зарезервированные средства на баланс пользователя.
    """
    order = db.query(models.Order).filter(
        models.Order.id == order_id,
        models.Order.user_id == current_user.id
    ).first()
    
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Заявка с ID {order_id} не найдена или не принадлежит текущему пользователю"
        )
    
    if order.status != models.OrderStatus.OPEN and order.status != models.OrderStatus.PARTIALLY_FILLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Невозможно отменить заявку в статусе {order.status}"
        )
    
    remaining_quantity = order.quantity - order.filled_quantity
    
    if remaining_quantity > 0:
        if order.side == models.OrderSide.BUY and order.order_type == models.OrderType.LIMIT:
            # Возвращаем рубли
            rub_balance = db.query(models.Balance).filter(
                models.Balance.user_id == current_user.id,
                models.Balance.ticker == "RUB"
            ).first()
            
            if rub_balance:
                rub_balance.amount += remaining_quantity * order.price
        elif order.side == models.OrderSide.SELL:
            # Возвращаем актив
            asset_balance = db.query(models.Balance).filter(
                models.Balance.user_id == current_user.id,
                models.Balance.ticker == order.ticker
            ).first()
            
            if asset_balance:
                asset_balance.amount += remaining_quantity
    
    order.status = models.OrderStatus.CANCELLED
    order.updated_at = datetime.datetime.utcnow()
    
    db.commit()
    
    return {"success": True}


def execute_matching(db: Session, order_id: str):
    """
    Выполняет матчинг ордера с имеющимися встречными заявками.
    """
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise ValueError(f"Ордер с ID {order_id} не найден")
    
    if order.status != models.OrderStatus.OPEN:
        return
    
    if order.side == models.OrderSide.BUY:
        if order.order_type == models.OrderType.LIMIT:
            counter_orders = db.query(models.Order).filter(
                models.Order.ticker == order.ticker,
                models.Order.side == models.OrderSide.SELL,
                models.Order.status.in_([models.OrderStatus.OPEN, models.OrderStatus.PARTIALLY_FILLED]),
                models.Order.price <= order.price
            ).order_by(asc(models.Order.price)).all()
        else:
            counter_orders = db.query(models.Order).filter(
                models.Order.ticker == order.ticker,
                models.Order.side == models.OrderSide.SELL,
                models.Order.status.in_([models.OrderStatus.OPEN, models.OrderStatus.PARTIALLY_FILLED])
            ).order_by(asc(models.Order.price)).all()
    else:
        if order.order_type == models.OrderType.LIMIT:
            counter_orders = db.query(models.Order).filter(
                models.Order.ticker == order.ticker,
                models.Order.side == models.OrderSide.BUY,
                models.Order.status.in_([models.OrderStatus.OPEN, models.OrderStatus.PARTIALLY_FILLED]),
                models.Order.price >= order.price
            ).order_by(desc(models.Order.price)).all()
        else:
            counter_orders = db.query(models.Order).filter(
                models.Order.ticker == order.ticker,
                models.Order.side == models.OrderSide.BUY,
                models.Order.status.in_([models.OrderStatus.OPEN, models.OrderStatus.PARTIALLY_FILLED])
            ).order_by(desc(models.Order.price)).all()
    
    for counter_order in counter_orders:
        if order.filled_quantity >= order.quantity:
            break
        
        if counter_order.user_id == order.user_id:
            continue
        
        order_remaining = order.quantity - order.filled_quantity
        counter_remaining = counter_order.quantity - counter_order.filled_quantity
        deal_quantity = min(order_remaining, counter_remaining)
        
        deal_price = counter_order.price
        
        execute_deal(db, order, counter_order, deal_quantity, deal_price)
    
    order_remaining = order.quantity - order.filled_quantity
    
    if order_remaining == 0:
        order.status = models.OrderStatus.FILLED
    elif order.filled_quantity > 0:
        order.status = models.OrderStatus.PARTIALLY_FILLED
    
    if order.order_type == models.OrderType.MARKET and order_remaining > 0:
        order.status = models.OrderStatus.CANCELLED if order.filled_quantity == 0 else models.OrderStatus.PARTIALLY_FILLED
    
    db.commit()


def execute_deal(db: Session, order: models.Order, counter_order: models.Order, quantity: Decimal, price: Decimal):
    """
    Выполняет сделку между двумя ордерами.
    """
    if order.side == models.OrderSide.BUY:
        buyer_id = order.user_id
        seller_id = counter_order.user_id
        buyer_order = order
        seller_order = counter_order
    else:
        buyer_id = counter_order.user_id
        seller_id = order.user_id
        buyer_order = counter_order
        seller_order = order
    
    deal_amount = quantity * price
    
    buyer_asset_balance = db.query(models.Balance).filter(
        models.Balance.user_id == buyer_id,
        models.Balance.ticker == order.ticker
    ).first()
    
    if buyer_asset_balance:
        buyer_asset_balance.amount += quantity
    else:
        buyer_asset_balance = models.Balance(
            user_id=buyer_id,
            ticker=order.ticker,
            amount=quantity
        )
        db.add(buyer_asset_balance)
    
    seller_rub_balance = db.query(models.Balance).filter(
        models.Balance.user_id == seller_id,
        models.Balance.ticker == "RUB"
    ).first()
    
    if seller_rub_balance:
        seller_rub_balance.amount += deal_amount
    else:
        seller_rub_balance = models.Balance(
            user_id=seller_id,
            ticker="RUB",
            amount=deal_amount
        )
        db.add(seller_rub_balance)
    
    if buyer_order.order_type == models.OrderType.LIMIT:
        reserved_amount = quantity * buyer_order.price
        refund_amount = reserved_amount - deal_amount
        
        if refund_amount > 0:
            buyer_rub_balance = db.query(models.Balance).filter(
                models.Balance.user_id == buyer_id,
                models.Balance.ticker == "RUB"
            ).first()
            
            if buyer_rub_balance:
                buyer_rub_balance.amount += refund_amount
    
    order.filled_quantity += quantity
    counter_order.filled_quantity += quantity
    
    if counter_order.filled_quantity >= counter_order.quantity:
        counter_order.status = models.OrderStatus.FILLED
    else:
        counter_order.status = models.OrderStatus.PARTIALLY_FILLED
    
    counter_order.updated_at = datetime.datetime.utcnow()
    order.updated_at = datetime.datetime.utcnow()
    
    db.commit()


def cancel_order_and_return_funds(db: Session, order_id: str):
    """
    Отменяет ордер и возвращает зарезервированные средства.
    """
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        return
    
    remaining_quantity = order.quantity - order.filled_quantity
    
    if remaining_quantity > 0:
        if order.side == models.OrderSide.BUY and order.order_type == models.OrderType.LIMIT:
            rub_balance = db.query(models.Balance).filter(
                models.Balance.user_id == order.user_id,
                models.Balance.ticker == "RUB"
            ).first()
            
            if rub_balance:
                rub_balance.amount += remaining_quantity * order.price
        elif order.side == models.OrderSide.SELL:
            asset_balance = db.query(models.Balance).filter(
                models.Balance.user_id == order.user_id,
                models.Balance.ticker == order.ticker
            ).first()
            
            if asset_balance:
                asset_balance.amount += remaining_quantity
    
    order.status = models.OrderStatus.CANCELLED
    order.updated_at = datetime.datetime.utcnow()
    
    db.commit()
