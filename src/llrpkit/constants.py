"""Protocol-level constants shared across llrpkit."""

from __future__ import annotations

from enum import IntEnum
from typing import Final

#: IANA-assigned TCP port for LLRP.
LLRP_PORT: Final = 5084

#: IANA-assigned TCP port for LLRP over TLS (supported by the Impinj R700).
LLRP_TLS_PORT: Final = 5085

#: Length in bytes of the fixed LLRP message header:
#: reserved/version/message-type (2 bytes), message length (u32), message ID (u32).
MESSAGE_HEADER_LEN: Final = 10

#: Impinj's IANA Private Enterprise Number — the vendor identifier carried in
#: LLRP ``CUSTOM_MESSAGE`` and custom parameters (the Octane extensions).
IMPINJ_PEN: Final = 25882


class LLRPVersion(IntEnum):
    """LLRP protocol versions as encoded in the message header."""

    V1_0_1 = 1
    V1_1 = 2
