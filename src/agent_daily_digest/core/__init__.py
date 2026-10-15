"""Business logic independent from CLI, Web and MCP transports."""

from .service import Digest, DigestError

__all__ = ["Digest", "DigestError"]
