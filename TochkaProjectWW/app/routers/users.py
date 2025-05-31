from fastapi import APIRouter, Depends, HTTPException, status, Security
from sqlalchemy.orm import Session
from .. import schemas, models, auth
from ..dependencies import get_db, get_current_user, get_current_admin
import uuid


router = APIRouter(prefix="/api/v1/public", tags=["public"])


@router.post("/register", response_model=schemas.UserOut, summary="Register")
def register_user(new_user: schemas.NewUser, db: Session = Depends(get_db)):
    """
    Регистрация нового пользователя в системе.
    
    Возвращает информацию о созданном пользователе, включая API-ключ для авторизации.
    """
    random_email = f"{uuid.uuid4()}@example.com"
    random_password = str(uuid.uuid4())
    
    hashed_password = auth.get_password_hash(random_password)
    api_key = auth.generate_api_key()
    user = models.User(
        name=new_user.name,
        email=random_email,
        hashed_password=hashed_password,
        role="USER",
        is_active=True,
        api_key=api_key
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    
    user_dict = {
        "id": str(user.id),
        "name": user.name,
        "role": user.role,
        "api_key": user.api_key
    }
    return user_dict


@router.post("/register-admin", response_model=schemas.UserOut)
def register_admin(new_user: schemas.NewUser, db: Session = Depends(get_db)):
    """
    Регистрация пользователя с ролью ADMIN (только для тестирования).
    В реальном приложении этот эндпоинт должен быть защищен.
    """
    random_email = f"{uuid.uuid4()}@example.com"
    random_password = str(uuid.uuid4())
    
    hashed_password = auth.get_password_hash(random_password)
    api_key = auth.generate_api_key()
    user = models.User(
        name=new_user.name,
        email=random_email,
        hashed_password=hashed_password,
        role="ADMIN",
        is_active=True,
        api_key=api_key
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    
    user_dict = {
        "id": str(user.id),
        "name": user.name,
        "role": user.role,
        "api_key": user.api_key
    }
    return user_dict


protected_router = APIRouter(
    prefix="/api/v1", 
    tags=["user"],
    dependencies=[Security(get_current_user)]
)


@protected_router.get("/users/me", response_model=schemas.UserOut)
def get_my_profile(current_user: models.User = Depends(get_current_user)):
    """
    Получить информацию о текущем пользователе.
    
    Для доступа нужно авторизоваться с помощью API-ключа.
    """
    user_dict = {
        "id": str(current_user.id),
        "name": current_user.name,
        "role": current_user.role,
        "api_key": current_user.api_key
    }
    return user_dict


admin_router = APIRouter(
    prefix="/api/v1/admin", 
    tags=["admin", "user"],
    dependencies=[Security(get_current_admin)]
)


@admin_router.delete("/user/{user_id}", response_model=schemas.UserOut, summary="Delete User")
def delete_user(
    user_id: str,
    db: Session = Depends(get_db)
):
    """
    Удаление пользователя администратором.
    
    Требуется авторизация с API-ключом пользователя, имеющего роль ADMIN.
    """
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    user_data = {
        "id": str(user.id),
        "name": user.name,
        "role": user.role,
        "api_key": user.api_key
    }
    
    db.delete(user)
    db.commit()
    
    return user_data
