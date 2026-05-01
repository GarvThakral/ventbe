import os
import inspect
from functools import lru_cache

from dotenv import load_dotenv
import httpx


def _patch_httpx_proxy_compat() -> None:
    """Allow older httpx builds to accept the newer `proxy` kwarg used by gotrue."""
    try:
        signature = inspect.signature(httpx.Client.__init__)
    except (TypeError, ValueError):
        return

    if "proxy" in signature.parameters:
        return

    original_init = httpx.Client.__init__

    def _compat_init(self, *args, proxy=None, **kwargs):
        if proxy is not None and "proxies" not in kwargs:
            kwargs["proxies"] = proxy
        return original_init(self, *args, **kwargs)

    httpx.Client.__init__ = _compat_init  # type: ignore[assignment]


_patch_httpx_proxy_compat()

from supabase import Client, create_client

load_dotenv()


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


class Settings:
    def __init__(self) -> None:
        self.supabase_url = _required_env("SUPABASE_URL")
        self.supabase_anon_key = _required_env("SUPABASE_ANON_KEY")
        self.supabase_service_role_key = _required_env("SUPABASE_SERVICE_ROLE_KEY")
        self.openrouter_api_key = _required_env("OPENROUTER_API_KEY")
        self.openrouter_base_url = os.getenv(
            "OPENROUTER_BASE_URL",
            "https://openrouter.ai/api/v1",
        ).rstrip("/")
        self.app_name = os.getenv("APP_NAME", "Vent")
        self.allowed_origins = [
            origin.strip()
            for origin in os.getenv(
                "ALLOWED_ORIGINS",
                "https://your-frontend.vercel.app,http://localhost:3000,http://localhost:3001",
            ).split(",")
            if origin.strip()
        ]
        self.primary_model = os.getenv(
            "OPENROUTER_PRIMARY_MODEL",
            "openrouter/free",
        )
        self.fallback_model = os.getenv(
            "OPENROUTER_FALLBACK_MODEL",
            "meta-llama/llama-3.3-70b-instruct:free",
        )
        self.embedding_model = os.getenv(
            "OPENROUTER_EMBEDDING_MODEL",
            "sentence-transformers/all-minilm-l6-v2",
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_supabase_auth_client() -> Client:
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_anon_key)


@lru_cache
def get_supabase_admin_client() -> Client:
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_service_role_key)
