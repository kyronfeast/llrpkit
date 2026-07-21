"""LLRP wire protocol: codec, generated messages/parameters, Impinj extensions.

Importing this package loads the generated protocol classes and registers
them for decode dispatch. Typical entry points::

    from llrpkit.protocol import decode_message, messages, params

    frame = messages.KEEPALIVE_ACK().to_bytes(message_id=7)
    msg = decode_message(frame)
"""

from llrpkit.protocol import codec, enums, impinj, messages, params
from llrpkit.protocol.codec import (
    BitStr,
    ByteReader,
    ByteWriter,
    CustomMessage,
    CustomParameter,
    LLRPMessage,
    LLRPParameter,
    UnknownCustomMessage,
    UnknownCustomParameter,
    UnknownMessage,
    UnknownParameter,
    decode_message,
    decode_parameter,
)

__all__ = [
    "BitStr",
    "ByteReader",
    "ByteWriter",
    "CustomMessage",
    "CustomParameter",
    "LLRPMessage",
    "LLRPParameter",
    "UnknownCustomMessage",
    "UnknownCustomParameter",
    "UnknownMessage",
    "UnknownParameter",
    "codec",
    "decode_message",
    "decode_parameter",
    "enums",
    "impinj",
    "messages",
    "params",
]
