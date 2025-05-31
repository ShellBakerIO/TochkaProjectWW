from sqlalchemy.orm import Session
from .database import SessionLocal
from .models import Instrument


def start_db():
    db = SessionLocal()
    try:
        rub = db.query(Instrument).filter(Instrument.ticker == "RUB").first()

        if not rub:
            rub = Instrument(
                ticker="RUB",
                name="Российский рубль",
                instrument_type="currency",
                commission_rate=0.0,
                initial_price=1.0,
                available_quantity=1000000000,
                is_listed=True
            )
            db.add(rub)
            db.commit()
            print("Базовая валюта RUB успешно создана")
        else:
            print("Базовая валюта RUB уже существует")
    finally:
        db.close()


if __name__ == "__main__":
    initialize_db()
