from fastapi import APIRouter, Depends, Header

from db.supabase_client import get_supabase_admin_client
from models.schemas import AuthResponse, LoginRequest, LogoutRequest, SignupRequest, UserResponse
from services.auth_service import (
    delete_current_user,
    login,
    logout,
    require_current_user,
    signup,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/signup", response_model=AuthResponse)
def signup_route(payload: SignupRequest) -> AuthResponse:
    return signup(payload)


@router.post("/login", response_model=AuthResponse)
def login_route(payload: LoginRequest) -> AuthResponse:
    return login(payload)


@router.post("/logout")
def logout_route(
    payload: LogoutRequest,
    authorization: str | None = Header(default=None),
    user: UserResponse = Depends(require_current_user),
) -> dict[str, str]:
    access_token = None
    if authorization and authorization.lower().startswith("bearer "):
        access_token = authorization.split(" ", 1)[1].strip()
    return logout(access_token, payload.refresh_token)


@router.get("/me", response_model=UserResponse)
def me_route(user: UserResponse = Depends(require_current_user)) -> UserResponse:
    return user


@router.delete("/me")
def delete_me_route(user: UserResponse = Depends(require_current_user)) -> dict[str, str]:
    return delete_current_user(user.id)
