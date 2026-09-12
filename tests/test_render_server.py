from __future__ import annotations

import os
from unittest.mock import Mock, patch

import pytest
from cryptography.fernet import Fernet

from alpaca_mcp_server.render_server import _required_env, build_render_server


RENDER_ENV = {
    "ALPACA_PAPER_TRADE": "true",
    "MCP_BASE_URL": "https://example.onrender.com/",
    "MCP_ALLOWED_GITHUB_LOGIN": "paper-owner",
    "REDIS_URL": "redis://redis.internal:6379",
    "STORAGE_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    "JWT_SIGNING_KEY": "fixed-high-entropy-signing-key-for-tests",
    "GITHUB_CLIENT_ID": "client-id",
    "GITHUB_CLIENT_SECRET": "client-secret",
}


def test_required_env_fails_closed_for_missing_value() -> None:
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(RuntimeError, match="MISSING"):
            _required_env("MISSING")


def test_render_server_refuses_live_trading() -> None:
    with patch.dict(os.environ, {**RENDER_ENV, "ALPACA_PAPER_TRADE": "false"}, clear=True):
        with pytest.raises(RuntimeError, match="ALPACA_PAPER_TRADE"):
            build_render_server()


def test_render_server_uses_fixed_keys_and_redis_storage() -> None:
    fake_server = Mock()
    with (
        patch.dict(os.environ, RENDER_ENV, clear=True),
        patch("alpaca_mcp_server.render_server.RedisStore") as redis_store,
        patch("alpaca_mcp_server.render_server.FernetEncryptionWrapper") as wrapper,
        patch("alpaca_mcp_server.render_server.GitHubProvider") as provider,
        patch("alpaca_mcp_server.render_server.build_server", return_value=fake_server),
    ):
        result = build_render_server()

    assert result is fake_server
    redis_store.assert_called_once_with(url=RENDER_ENV["REDIS_URL"])
    assert wrapper.call_args.kwargs["key_value"] is redis_store.return_value
    assert provider.call_args.kwargs["jwt_signing_key"] == RENDER_ENV["JWT_SIGNING_KEY"]
    assert provider.call_args.kwargs["client_storage"] is wrapper.return_value
    assert fake_server.auth is provider.return_value
    fake_server.add_middleware.assert_called_once()
