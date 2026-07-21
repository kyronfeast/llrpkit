"""Exception hierarchy for llrpkit.

Everything raised on purpose by this library derives from :class:`LLRPError`,
so callers can catch a single type at the boundary of their application.
"""

from __future__ import annotations


class LLRPError(Exception):
    """Base class for all llrpkit errors."""


class LLRPConnectionError(LLRPError):
    """Raised when the reader connection cannot be established or is lost.

    Covers TCP-level failures as well as LLRP-level rejection — for example,
    a reader that already has an active client refusing the connection
    attempt in its ``ConnectionAttemptEvent``.
    """


class LLRPTimeoutError(LLRPError):
    """Raised when the reader does not answer a request within the deadline."""


class MessageDecodeError(LLRPError):
    """Raised when bytes off the wire cannot be decoded as valid LLRP."""


class MessageEncodeError(LLRPError):
    """Raised when a message tree cannot be encoded, e.g. a field out of range."""


class LLRPStatusError(LLRPError):
    """Raised when the reader answers with a non-success ``LLRPStatus``.

    Attributes:
        status_code: Numeric status code from the reader's response.
        error_description: Human-readable description supplied by the reader.
    """

    def __init__(self, status_code: int, error_description: str = "") -> None:
        self.status_code = status_code
        self.error_description = error_description
        detail = f" ({error_description})" if error_description else ""
        super().__init__(f"reader returned status {status_code}{detail}")


class CapabilityError(LLRPError):
    """Raised when a request is valid LLRP but unsupported by this reader.

    llrpkit validates configuration against the capabilities the connected
    reader reports (antenna count, RF mode table, transmit power table)
    before anything hits the wire, so mistakes fail fast and locally with a
    clear message instead of as an opaque reader error.
    """
