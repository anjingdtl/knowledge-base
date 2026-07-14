"""MCP HTTP auth helpers (Phase-3)."""
from __future__ import annotations

import os
import secrets

from fastmcp.server.auth import AccessToken, TokenVerifier

from src.utils.config import Config


class StaticTokenVerifier(TokenVerifier):
    """Verify a configured local bearer token for HTTP MCP transports."""

    def __init__(self, expected_token: str):
        super().__init__()
        self._expected_token = expected_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not secrets.compare_digest(token, self._expected_token):
            return None
        return AccessToken(
            token=token,
            client_id="shinehe-mcp",
            scopes=[],
        )


def build_auth_provider() -> TokenVerifier | None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    policy = str(Config.get("mcp.write_policy", "")).lower()
    if transport not in {"streamable-http", "sse"} or policy != "token_required":
        return None

    token = str(Config.get("mcp.auth_token", "") or "")
    if not token:
        raise RuntimeError(
            "mcp.write_policy=token_required 时必须配置 mcp.auth_token"
        )
    return StaticTokenVerifier(token)


# Back-compat aliases
_StaticTokenVerifier = StaticTokenVerifier
_build_auth_provider = build_auth_provider
