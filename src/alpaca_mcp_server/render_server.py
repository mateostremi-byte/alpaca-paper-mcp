"""Production entry point for the Render-hosted paper-trading MCP server."""

from __future__ import annotations

import os

from cryptography.fernet import Fernet
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.middleware import AuthMiddleware
from key_value.aio.stores.redis import RedisStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from .server import build_server


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def build_render_server():
    """Build a fail-closed, restart-safe, GitHub-authenticated MCP server."""
    if os.environ.get("ALPACA_PAPER_TRADE", "true").lower() not in {"true", "1", "yes"}:
        raise RuntimeError("Render server must run with ALPACA_PAPER_TRADE=true")

    base_url = _required_env("MCP_BASE_URL").rstrip("/")
    allowed_login = _required_env("MCP_ALLOWED_GITHUB_LOGIN")
    storage = FernetEncryptionWrapper(
        key_value=RedisStore(url=_required_env("REDIS_URL")),
        fernet=Fernet(_required_env("STORAGE_ENCRYPTION_KEY")),
    )
    auth = GitHubProvider(
        client_id=_required_env("GITHUB_CLIENT_ID"),
        client_secret=_required_env("GITHUB_CLIENT_SECRET"),
        base_url=base_url,
        jwt_signing_key=_required_env("JWT_SIGNING_KEY"),
        client_storage=storage,
    )

    server = build_server()
    server.auth = auth
    server.add_middleware(
        AuthMiddleware(
            auth=lambda context: (
                context.token is not None
                and context.token.claims.get("login") == allowed_login
            )
        )
    )
    return server


def main() -> None:
    server = build_render_server()
    server.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "10000")),
    )


if __name__ == "__main__":
    main()
