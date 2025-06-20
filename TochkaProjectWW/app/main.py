from fastapi import FastAPI, Depends
from .database import engine, Base
from .routers import users, instruments, balances, orders, public_transactions
from .dependencies import check_auth_headers
from .db_start import start_db


app = FastAPI(
    title="WW Биржа",
    version="0.1.0",
)

app.openapi_components = {
    "securitySchemes": {
        "ApiKeyAuth": {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": "Введите ваш API-ключ, полученный при регистрации. Например: 'TOKEN your-api-key'"
        }
    }
}

app.openapi_tags = [
    {
        "name": "public",
        "description": "Публичные эндпоинты, доступные без авторизации"
    },
    {
        "name": "admin",
        "description": "Административные эндпоинты, требующие роли ADMIN"
    },
    {
        "name": "user",
        "description": "Эндпоинты для управления пользователями"
    },
    {
        "name": "balance",
        "description": "Эндпоинты для работы с балансами"
    },
    {
        "name": "order",
        "description": "Эндпоинты для работы с ордерами"
    }
]


@app.get("/debug/headers")
async def debug_headers(headers_info: dict = Depends(check_auth_headers)):
    """
    Отладочный эндпоинт для проверки заголовков запроса.
    Используйте этот эндпоинт, чтобы убедиться, что заголовок авторизации правильно передается.
    """
    return headers_info


Base.metadata.create_all(bind=engine)

start_db()
app.include_router(users.router)
app.include_router(users.protected_router)
app.include_router(users.admin_router)
app.include_router(instruments.router)
app.include_router(instruments.admin_router)
app.include_router(balances.router)
app.include_router(balances.admin_router)
app.include_router(orders.router)
app.include_router(orders.protected_router)
app.include_router(public_transactions.router)
