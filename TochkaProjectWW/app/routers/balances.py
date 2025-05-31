from fastapi import APIRouter, Depends, HTTPException, status, Security
from sqlalchemy.orm import Session
from typing import List, Dict
from decimal import Decimal
from .. import schemas, models
from ..dependencies import get_db, get_current_user, get_current_admin

router = APIRouter(
    prefix="/api/v1",
    tags=["balance"]
)


@router.get("/balance", response_model=Dict[str, int])
def get_balances(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить все балансы текущего пользователя.
    
    Возвращает словарь, где ключи - тикеры инструментов, значения - количество на счете.
    Требует авторизации с API-ключом.
    """
    balances = db.query(models.Balance).filter(models.Balance.user_id == current_user.id).all()
    
    result = {}
    for balance in balances:
        result[balance.ticker] = int(balance.amount)
    
    return result


admin_router = APIRouter(
    prefix="/api/v1/admin/balance",
    tags=["admin", "balance"]
)


@admin_router.post("/deposit", response_model=schemas.Ok)
def deposit(
    balance_op: schemas.BalanceOperation,
    current_user: models.User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """
    Пополнить баланс указанного пользователя (только для администраторов).
    
    Требует авторизации с API-ключом пользователя, имеющего роль ADMIN.
    """
    user = db.query(models.User).filter(models.User.id == balance_op.user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Пользователь с ID {balance_op.user_id} не найден"
        )
    
    instrument = db.query(models.Instrument).filter(models.Instrument.ticker == balance_op.ticker).first()
    if not instrument:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Инструмент с тикером {balance_op.ticker} не найден"
        )
    
    balance = db.query(models.Balance).filter(
        models.Balance.user_id == balance_op.user_id,
        models.Balance.ticker == balance_op.ticker
    ).first()
    
    if not balance:
        balance = models.Balance(
            user_id=balance_op.user_id,
            ticker=balance_op.ticker,
            amount=balance_op.amount
        )
        db.add(balance)
    else:
        balance.amount += balance_op.amount
    
    db.commit()
    
    return {"success": True}


@admin_router.post("/withdraw", response_model=schemas.Ok)
def withdraw(
    balance_op: schemas.BalanceOperation,
    current_user: models.User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """
    Снять средства с баланса указанного пользователя (только для администраторов).
    
    Требует авторизации с API-ключом пользователя, имеющего роль ADMIN.
    """
    user = db.query(models.User).filter(models.User.id == balance_op.user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Пользователь с ID {balance_op.user_id} не найден"
        )
    
    instrument = db.query(models.Instrument).filter(models.Instrument.ticker == balance_op.ticker).first()
    if not instrument:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Инструмент с тикером {balance_op.ticker} не найден"
        )
    
    balance = db.query(models.Balance).filter(
        models.Balance.user_id == balance_op.user_id,
        models.Balance.ticker == balance_op.ticker
    ).first()
    
    if not balance:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"У пользователя нет средств по инструменту {balance_op.ticker}"
        )
    
    if balance.amount < balance_op.amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Недостаточно средств. Доступно: {balance.amount}, запрошено: {balance_op.amount}"
        )
    
    balance.amount -= balance_op.amount
    
    db.commit()
    
    return {"success": True}
