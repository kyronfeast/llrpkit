"""llrpkit — a modern asyncio LLRP toolkit for Impinj RAIN RFID readers.

llrpkit speaks the EPCglobal/GS1 Low Level Reader Protocol (LLRP) to fixed
RAIN RFID readers, with first-class support for the Impinj R700 and Speedway
families and their Octane LLRP extensions. It ships a typed asyncio client,
a reader emulator (so everything can be developed, tested, and demoed with
zero hardware), a CLI, and a web dashboard.

This package is under active pre-1.0 development; the modules present here
(exceptions, constants, CLI) are the stable seed the protocol engine grows
around.
"""

from llrpkit.__about__ import __version__
from llrpkit.client import LLRPClient
from llrpkit.constants import (
    IMPINJ_PEN,
    LLRP_PORT,
    LLRP_TLS_PORT,
    MESSAGE_HEADER_LEN,
    LLRPVersion,
)
from llrpkit.epc import DecodedEPC, decode_epc
from llrpkit.exceptions import (
    CapabilityError,
    LLRPConnectionError,
    LLRPError,
    LLRPStatusError,
    LLRPTimeoutError,
    MessageDecodeError,
    MessageEncodeError,
)
from llrpkit.health import HealthAlert, HealthMonitor
from llrpkit.inventory import TagReport
from llrpkit.modes import AnnotatedMode, ModeGuidance, annotate_modes, suggest_mode
from llrpkit.presence import PresenceEvent, PresenceTracker, ticked_stream
from llrpkit.profiles import InventoryProfile
from llrpkit.reader import (
    AccessResult,
    GPIOState,
    Reader,
    ReaderCapabilities,
    RFMode,
)
from llrpkit.survey import SweepPoint, sweep

__all__ = [
    "IMPINJ_PEN",
    "LLRP_PORT",
    "LLRP_TLS_PORT",
    "MESSAGE_HEADER_LEN",
    "AccessResult",
    "AnnotatedMode",
    "CapabilityError",
    "DecodedEPC",
    "GPIOState",
    "HealthAlert",
    "HealthMonitor",
    "InventoryProfile",
    "LLRPClient",
    "LLRPConnectionError",
    "LLRPError",
    "LLRPStatusError",
    "LLRPTimeoutError",
    "LLRPVersion",
    "MessageDecodeError",
    "MessageEncodeError",
    "ModeGuidance",
    "PresenceEvent",
    "PresenceTracker",
    "RFMode",
    "Reader",
    "ReaderCapabilities",
    "SweepPoint",
    "TagReport",
    "TagWriter",
    "__version__",
    "annotate_modes",
    "decode_epc",
    "suggest_mode",
    "sweep",
    "ticked_stream",
]
