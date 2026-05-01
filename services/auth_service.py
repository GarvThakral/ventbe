import time
from fastapi import Depends, Header, HTTPException, status
from supabase import Client

from db.supabase_client import (
    get_settings,
    get_supabase_admin_client,
    get_supabase_auth_client,
)
from models.schemas import AuthResponse, LoginRequest, SignupRequest, UserResponse
from services.logging import get_logger

logger = get_logger("vent.auth")


def _auth_exception(detail: str = "Unauthorized") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _normalize_user(user: object, is_premium: bool = False) -> UserResponse:
    user_id = getattr(user, "id", None)
    email = getattr(user, "email", None)
    if not user_id:
        raise _auth_exception("Invalid user payload returned by Supabase")
    return UserResponse(id=user_id, email=email, is_premium=is_premium)


def _normalize_auth_response(response: object, is_premium: bool = False) -> AuthResponse:
    user = getattr(response, "user", None)
    session = getattr(response, "session", None)
    if user is None:
        raise HTTPException(status_code=400, detail="Supabase did not return a user")

    return AuthResponse(
        access_token=getattr(session, "access_token", None) if session else None,
        refresh_token=getattr(session, "refresh_token", None) if session else None,
        user=_normalize_user(user, is_premium=is_premium),
        needs_email_confirmation=session is None,
    )


def signup(payload: SignupRequest) -> AuthResponse:
    auth_client = get_supabase_auth_client()
    admin_client = get_supabase_admin_client()

    logger.info("Signup requested for email=%s", payload.email)
    response = auth_client.auth.sign_up(
        {
            "email": payload.email,
            "password": payload.password,
        }
    )
    user = getattr(response, "user", None)
    if user is None:
        raise HTTPException(status_code=400, detail="Unable to create user")

    # Retry profile creation in case of foreign key race condition
    max_retries = 3
    for attempt in range(max_retries):
        try:
            admin_client.table("profiles").upsert(
                {
                    "id": getattr(user, "id"),
                    "email": getattr(user, "email", payload.email),
                }
            ).execute()
            break
        except Exception as e:
            if attempt == max_retries - 1:
                logger.error("Failed to create profile after %d attempts: %s", max_retries, str(e))
                raise
            logger.warning("Profile creation attempt %d failed, retrying... error: %s", attempt + 1, str(e))
            time.sleep(1)

    return _normalize_auth_response(response)


def login(payload: LoginRequest) -> AuthResponse:
    auth_client = get_supabase_auth_client()
    logger.info("Login requested for email=%s", payload.email)
    response = auth_client.auth.sign_in_with_password(
        {
            "email": payload.email,
            "password": payload.password,
        }
    )
    user = getattr(response, "user", None)
    is_premium = False
    if user:
        admin_client = get_supabase_admin_client()
        profile_response = admin_client.table("profiles").select("is_premium").eq("id", user.id).single().execute()
        profile_data = getattr(profile_response, "data", {}) or {}
        is_premium = profile_data.get("is_premium", False)

    return _normalize_auth_response(response, is_premium=is_premium)


def logout(access_token: str | None, refresh_token: str | None) -> dict[str, str]:
    if not access_token or not refresh_token:
        logger.info("Logout without tokens; local sign-out only")
        return {"message": "Signed out locally. Access tokens remain valid until expiry."}

    auth_client = get_supabase_auth_client()
    logger.info("Logout requested with token pair")
    auth_client.auth.set_session(access_token, refresh_token)
    auth_client.auth.sign_out()
    return {"message": "Signed out successfully"}


def delete_current_user(user_id: str) -> dict[str, str]:
    admin_client = get_supabase_admin_client()
    logger.warning("Deleting user profile and auth user id=%s", user_id)
    admin_client.table("profiles").delete().eq("id", user_id).execute()
    admin_client.auth.admin.delete_user(user_id)
    return {"message": "Account deleted"}


def get_current_user(
    authorization: str | None = Header(default=None),
) -> UserResponse:
    if not authorization or not authorization.lower().startswith("bearer "):
        logger.warning("Missing bearer token on protected request")
        raise _auth_exception("Missing bearer token")

    token = authorization.split(" ", 1)[1].strip()
    if not token:
        logger.warning("Empty bearer token on protected request")
        raise _auth_exception("Missing bearer token")

    auth_client: Client = get_supabase_auth_client()
    user_response = auth_client.auth.get_user(token)
    user = getattr(user_response, "user", None)
    if user is None:
        logger.warning("Supabase token validation failed")
        raise _auth_exception("Invalid or expired token")

    user_id = getattr(user, "id")
    admin_client = get_supabase_admin_client()
    profile_response = admin_client.table("profiles").select("is_premium").eq("id", user_id).single().execute()
    profile_data = getattr(profile_response, "data", {}) or {}
    is_premium = profile_data.get("is_premium", False)

    return UserResponse(
        id=user_id,
        email=getattr(user, "email", None),
        is_premium=is_premium
    )


def require_current_user(user: UserResponse = Depends(get_current_user)) -> UserResponse:
    return user
