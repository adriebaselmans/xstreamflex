"""Typed provider failures.

The UI layer decides what to show based on the exception type, never by matching
on message text.
"""
from __future__ import annotations


class ProviderError(Exception):
    """Base class. Carries a user-facing message and optional technical detail."""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def __str__(self) -> str:
        return self.message


class AuthError(ProviderError):
    """Credentials rejected, account expired, or the request was refused outright."""


class EndpointDisabledError(ProviderError):
    """The panel has switched this endpoint off.

    Seen as HTTP 885 on ``get.php`` at providers that still serve ``player_api.php``
    normally. Not retryable and not a configuration mistake on our side.
    """


class ConnectionLimitError(ProviderError):
    """Refused because the account's concurrent connection limit is in use."""


class TransientError(ProviderError):
    """Timeout, connection reset, or 5xx that survived the retry policy."""


class ParseError(ProviderError):
    """A 200 response whose body does not match the documented API contract."""


class NotSupportedError(ProviderError):
    """The active provider kind cannot do this (e.g. VOD on a plain M3U source)."""
