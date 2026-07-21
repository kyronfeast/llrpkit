"""Binary codec core for LLRP.

This module is the hand-written foundation the generated protocol classes sit
on: bit-accurate buffer reader/writer, message framing, TLV/TV parameter
encoding, custom (vendor) extension handling, unknown-data fallbacks, and the
type registries used for decode dispatch.

Wire format summary (LLRP 1.0.1):

* Message header (10 bytes): 3 reserved bits, 3-bit protocol version, 10-bit
  message type, u32 total message length, u32 message ID.
* TLV parameter: 6 reserved bits + 10-bit type, u16 length (including the
  4-byte header), then fields and nested sub-parameters.
* TV parameter: 1 byte (high bit set, 7-bit type), then fixed-size fields
  with no length — the layout is known from the definition.
* Custom parameter: TLV type 1023 with u32 vendor PEN + u32 subtype prefix.
  Custom message: type 1023 with u32 vendor PEN + u8 subtype prefix.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, ClassVar, Final, NamedTuple, TypeVar

from llrpkit.constants import MESSAGE_HEADER_LEN
from llrpkit.exceptions import MessageDecodeError, MessageEncodeError

#: LLRP type number shared by custom parameters and custom messages.
TYPE_CUSTOM: Final = 1023


@dataclass(frozen=True)
class BitStr:
    """A bit string of explicit length, as used by LLRP ``u1v`` fields.

    LLRP bit vectors carry a bit count and packed bytes (MSB-first, zero
    padded). Most real-world values are whole bytes — use
    :meth:`from_bytes` for those.
    """

    bit_len: int = 0
    data: bytes = b""

    def __post_init__(self) -> None:
        if self.bit_len < 0:
            raise ValueError("bit_len must be non-negative")
        if len(self.data) != (self.bit_len + 7) // 8:
            raise ValueError(
                f"BitStr data is {len(self.data)} bytes but bit_len {self.bit_len} "
                f"requires {(self.bit_len + 7) // 8}"
            )

    @classmethod
    def from_bytes(cls, data: bytes) -> BitStr:
        """Build a whole-byte bit string (bit_len == len(data) * 8)."""
        return cls(len(data) * 8, data)


class ByteWriter:
    """Big-endian, bit-accurate output buffer.

    Bits accumulate MSB-first; multi-byte writes require byte alignment,
    which the LLRP definitions guarantee via explicit reserved bits.
    """

    __slots__ = ("_acc", "_bits", "_buf")

    def __init__(self) -> None:
        self._buf = bytearray()
        self._acc = 0
        self._bits = 0

    @property
    def bit_aligned(self) -> bool:
        return self._bits == 0

    def _require_aligned(self) -> None:
        if self._bits:
            raise MessageEncodeError("write is not byte-aligned (definition bug)")

    def bits(self, value: int, nbits: int) -> None:
        value = int(value)
        if nbits <= 0:
            raise MessageEncodeError("bit count must be positive")
        if value < 0 or value >= (1 << nbits):
            raise MessageEncodeError(f"value {value} does not fit in {nbits} bit(s)")
        self._acc = (self._acc << nbits) | value
        self._bits += nbits
        while self._bits >= 8:
            self._bits -= 8
            self._buf.append((self._acc >> self._bits) & 0xFF)
        self._acc &= (1 << self._bits) - 1

    def _uint(self, value: int, nbytes: int) -> None:
        value = int(value)
        if value < 0 or value >= 1 << (8 * nbytes):
            raise MessageEncodeError(f"value {value} does not fit in {nbytes} byte(s)")
        if self._bits:
            self.bits(value, 8 * nbytes)
        else:
            self._buf += value.to_bytes(nbytes, "big")

    def u8(self, value: int) -> None:
        self._uint(value, 1)

    def u16(self, value: int) -> None:
        self._uint(value, 2)

    def u32(self, value: int) -> None:
        self._uint(value, 4)

    def u64(self, value: int) -> None:
        self._uint(value, 8)

    def _sint(self, value: int, nbytes: int) -> None:
        value = int(value)
        limit = 1 << (8 * nbytes - 1)
        if not -limit <= value < limit:
            raise MessageEncodeError(f"value {value} does not fit in signed {nbytes} byte(s)")
        self._uint(value & ((1 << (8 * nbytes)) - 1), nbytes)

    def s8(self, value: int) -> None:
        self._sint(value, 1)

    def s16(self, value: int) -> None:
        self._sint(value, 2)

    def s32(self, value: int) -> None:
        self._sint(value, 4)

    def s64(self, value: int) -> None:
        self._sint(value, 8)

    def raw(self, data: bytes) -> None:
        self._require_aligned()
        self._buf += data

    def u96(self, data: bytes) -> None:
        if len(data) != 12:
            raise MessageEncodeError(f"u96 field requires exactly 12 bytes, got {len(data)}")
        self.raw(data)

    def u1v(self, value: BitStr) -> None:
        self.u16(value.bit_len)
        self.raw(value.data)

    def u8v(self, value: bytes) -> None:
        self.u16(len(value))
        self.raw(value)

    def u16v(self, values: Sequence[int]) -> None:
        self.u16(len(values))
        for v in values:
            self.u16(v)

    def u32v(self, values: Sequence[int]) -> None:
        self.u16(len(values))
        for v in values:
            self.u32(v)

    def utf8v(self, value: str) -> None:
        encoded = value.encode("utf-8")
        self.u16(len(encoded))
        self.raw(encoded)

    def getvalue(self) -> bytes:
        self._require_aligned()
        return bytes(self._buf)


class ByteReader:
    """Big-endian, bit-accurate input cursor with strict bounds checking.

    Every read raises :class:`MessageDecodeError` (never ``IndexError`` or
    ``struct.error``) when data runs out, so the decoder is safe to point at
    arbitrary bytes.
    """

    __slots__ = ("_bit", "_data", "_pos")

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0
        self._bit = 0

    @property
    def size(self) -> int:
        return len(self._data)

    @property
    def byte_pos(self) -> int:
        return self._pos

    @property
    def bit_aligned(self) -> bool:
        return self._bit == 0

    def _require_aligned(self) -> None:
        if self._bit:
            raise MessageDecodeError("read is not byte-aligned (definition bug)")

    def bits(self, nbits: int) -> int:
        result = 0
        remaining = nbits
        while remaining > 0:
            if self._pos >= len(self._data):
                raise MessageDecodeError("truncated data while reading bit field")
            avail = 8 - self._bit
            take = min(avail, remaining)
            shift = avail - take
            chunk = (self._data[self._pos] >> shift) & ((1 << take) - 1)
            result = (result << take) | chunk
            self._bit += take
            if self._bit == 8:
                self._bit = 0
                self._pos += 1
            remaining -= take
        return result

    def _uint(self, nbytes: int) -> int:
        if self._bit:
            return self.bits(8 * nbytes)
        if self._pos + nbytes > len(self._data):
            raise MessageDecodeError(f"truncated data while reading {nbytes}-byte integer")
        value = int.from_bytes(self._data[self._pos : self._pos + nbytes], "big")
        self._pos += nbytes
        return value

    def u8(self) -> int:
        return self._uint(1)

    def u16(self) -> int:
        return self._uint(2)

    def u32(self) -> int:
        return self._uint(4)

    def u64(self) -> int:
        return self._uint(8)

    def _sint(self, nbytes: int) -> int:
        value = self._uint(nbytes)
        sign = 1 << (8 * nbytes - 1)
        return value - (1 << (8 * nbytes)) if value & sign else value

    def s8(self) -> int:
        return self._sint(1)

    def s16(self) -> int:
        return self._sint(2)

    def s32(self) -> int:
        return self._sint(4)

    def s64(self) -> int:
        return self._sint(8)

    def peek_u8(self) -> int:
        self._require_aligned()
        if self._pos >= len(self._data):
            raise MessageDecodeError("truncated data while reading parameter")
        return self._data[self._pos]

    def raw(self, nbytes: int) -> bytes:
        self._require_aligned()
        if nbytes < 0 or self._pos + nbytes > len(self._data):
            raise MessageDecodeError(f"truncated data while reading {nbytes} raw byte(s)")
        out = self._data[self._pos : self._pos + nbytes]
        self._pos += nbytes
        return out

    def u96(self) -> bytes:
        return self.raw(12)

    def u1v(self) -> BitStr:
        bit_len = self.u16()
        return BitStr(bit_len, self.raw((bit_len + 7) // 8))

    def u8v(self) -> bytes:
        return self.raw(self.u16())

    def u16v(self) -> list[int]:
        count = self.u16()
        return [self.u16() for _ in range(count)]

    def u32v(self) -> list[int]:
        count = self.u16()
        return [self.u32() for _ in range(count)]

    def utf8v(self) -> str:
        data = self.raw(self.u16())
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MessageDecodeError(f"invalid UTF-8 in string field: {exc}") from exc

    def read_to(self, end: int) -> bytes:
        if end < self._pos:
            raise MessageDecodeError("parameter length points before current position")
        return self.raw(end - self._pos)


class ParamSlot(NamedTuple):
    """One sub-parameter slot of a container, from the LLRP definitions."""

    attr: str
    types: tuple[type[LLRPParameter], ...]
    min_count: int
    is_list: bool


class LLRPStruct:
    """Shared machinery for parameters and messages (fields + sub-parameters)."""

    _SLOTS: ClassVar[tuple[ParamSlot, ...]] = ()
    _unbound_params: list[LLRPParameter] | None = None

    @property
    def unbound_params(self) -> list[LLRPParameter]:
        """Decoded sub-parameters that matched no slot (kept for round-trip)."""
        if self._unbound_params is None:
            self._unbound_params = []
        return self._unbound_params

    def _encode_prefix(self, w: ByteWriter) -> None:
        return

    def _encode_fields(self, w: ByteWriter) -> None:
        return

    @classmethod
    def _decode_fields(cls, r: ByteReader, end: int) -> dict[str, Any]:
        return {}

    def _encode_subparams(self, w: ByteWriter) -> None:
        for slot in self._SLOTS:
            value = getattr(self, slot.attr)
            if slot.is_list:
                if len(value) < slot.min_count:
                    raise MessageEncodeError(
                        f"{type(self).__name__}.{slot.attr} requires at least "
                        f"{slot.min_count} parameter(s)"
                    )
                for p in value:
                    w.raw(p.encode())
            elif value is None:
                if slot.min_count:
                    raise MessageEncodeError(f"{type(self).__name__}.{slot.attr} is required")
            else:
                w.raw(value.encode())
        if self._unbound_params:
            for p in self._unbound_params:
                w.raw(p.encode())


class LLRPParameter(LLRPStruct):
    """Base class for all LLRP parameters (TLV and TV)."""

    PARAM_TYPE: ClassVar[int] = -1
    IS_TV: ClassVar[bool] = False

    def encode(self) -> bytes:
        w = ByteWriter()
        self._encode_prefix(w)
        self._encode_fields(w)
        self._encode_subparams(w)
        body = w.getvalue()
        if self.IS_TV:
            return bytes((0x80 | self.PARAM_TYPE,)) + body
        length = 4 + len(body)
        if length > 0xFFFF:
            raise MessageEncodeError(f"{type(self).__name__} exceeds the 65535-byte TLV limit")
        return self.PARAM_TYPE.to_bytes(2, "big") + length.to_bytes(2, "big") + body


class CustomParameter(LLRPParameter):
    """Base class for vendor extension parameters (TLV type 1023)."""

    PARAM_TYPE: ClassVar[int] = TYPE_CUSTOM
    VENDOR_ID: ClassVar[int] = 0
    SUBTYPE: ClassVar[int] = 0

    def _encode_prefix(self, w: ByteWriter) -> None:
        w.u32(self.VENDOR_ID)
        w.u32(self.SUBTYPE)


class LLRPMessage(LLRPStruct):
    """Base class for all LLRP messages."""

    MESSAGE_TYPE: ClassVar[int] = -1
    message_id: int = 0
    protocol_version: int = 1

    def to_bytes(self, message_id: int | None = None, version: int | None = None) -> bytes:
        """Encode the complete framed message, header included."""
        w = ByteWriter()
        self._encode_prefix(w)
        self._encode_fields(w)
        self._encode_subparams(w)
        body = w.getvalue()
        mid = self.message_id if message_id is None else message_id
        ver = self.protocol_version if version is None else version
        if not 0 <= mid <= 0xFFFFFFFF:
            raise MessageEncodeError(f"message ID {mid} out of range")
        if not 0 <= ver <= 7:
            raise MessageEncodeError(f"protocol version {ver} out of range")
        header = (ver << 10) | (self.MESSAGE_TYPE & 0x3FF)
        total = MESSAGE_HEADER_LEN + len(body)
        return header.to_bytes(2, "big") + total.to_bytes(4, "big") + mid.to_bytes(4, "big") + body


class CustomMessage(LLRPMessage):
    """Base class for vendor extension messages (message type 1023)."""

    MESSAGE_TYPE: ClassVar[int] = TYPE_CUSTOM
    VENDOR_ID: ClassVar[int] = 0
    SUBTYPE: ClassVar[int] = 0

    def _encode_prefix(self, w: ByteWriter) -> None:
        w.u32(self.VENDOR_ID)
        w.u8(self.SUBTYPE)


@dataclass(kw_only=True)
class UnknownParameter(LLRPParameter):
    """A TLV parameter this build has no definition for; payload preserved."""

    param_type: int
    payload: bytes = b""

    def encode(self) -> bytes:
        length = 4 + len(self.payload)
        if length > 0xFFFF:
            raise MessageEncodeError("UnknownParameter exceeds the 65535-byte TLV limit")
        return (
            (self.param_type & 0x3FF).to_bytes(2, "big") + length.to_bytes(2, "big") + self.payload
        )


@dataclass(kw_only=True)
class UnknownCustomParameter(CustomParameter):
    """A vendor parameter this build has no definition for; payload preserved."""

    vendor_id: int
    subtype: int
    payload: bytes = b""

    def _encode_prefix(self, w: ByteWriter) -> None:
        w.u32(self.vendor_id)
        w.u32(self.subtype)

    def _encode_fields(self, w: ByteWriter) -> None:
        w.raw(self.payload)


@dataclass(kw_only=True)
class UnknownMessage(LLRPMessage):
    """A message this build has no definition for; payload preserved."""

    msg_type: int
    payload: bytes = b""

    def to_bytes(self, message_id: int | None = None, version: int | None = None) -> bytes:
        mid = self.message_id if message_id is None else message_id
        ver = self.protocol_version if version is None else version
        header = (ver << 10) | (self.msg_type & 0x3FF)
        total = MESSAGE_HEADER_LEN + len(self.payload)
        return (
            header.to_bytes(2, "big")
            + total.to_bytes(4, "big")
            + mid.to_bytes(4, "big")
            + self.payload
        )


@dataclass(kw_only=True)
class UnknownCustomMessage(CustomMessage):
    """A vendor message this build has no definition for; payload preserved."""

    vendor_id: int
    subtype: int
    payload: bytes = b""

    def _encode_prefix(self, w: ByteWriter) -> None:
        w.u32(self.vendor_id)
        w.u8(self.subtype)

    def _encode_fields(self, w: ByteWriter) -> None:
        w.raw(self.payload)


PARAMETER_REGISTRY: Final[dict[int, type[LLRPParameter]]] = {}
CUSTOM_PARAMETER_REGISTRY: Final[dict[tuple[int, int], type[CustomParameter]]] = {}
MESSAGE_REGISTRY: Final[dict[int, type[LLRPMessage]]] = {}
CUSTOM_MESSAGE_REGISTRY: Final[dict[tuple[int, int], type[CustomMessage]]] = {}

_S = TypeVar("_S", bound=LLRPStruct)
_P = TypeVar("_P", bound=LLRPParameter)
_M = TypeVar("_M", bound=LLRPMessage)


def register_parameter(cls: type[_P]) -> type[_P]:
    """Class decorator adding a parameter class to the decode registries."""
    if issubclass(cls, CustomParameter):
        key = (cls.VENDOR_ID, cls.SUBTYPE)
        if key in CUSTOM_PARAMETER_REGISTRY:
            raise ValueError(f"duplicate custom parameter registration {key}")
        CUSTOM_PARAMETER_REGISTRY[key] = cls
    else:
        if cls.PARAM_TYPE < 0:
            raise ValueError(f"{cls.__name__} has no PARAM_TYPE")
        if cls.PARAM_TYPE in PARAMETER_REGISTRY:
            raise ValueError(f"duplicate parameter type {cls.PARAM_TYPE}")
        PARAMETER_REGISTRY[cls.PARAM_TYPE] = cls
    return cls


def register_message(cls: type[_M]) -> type[_M]:
    """Class decorator adding a message class to the decode registries."""
    if issubclass(cls, CustomMessage):
        key = (cls.VENDOR_ID, cls.SUBTYPE)
        if key in CUSTOM_MESSAGE_REGISTRY:
            raise ValueError(f"duplicate custom message registration {key}")
        CUSTOM_MESSAGE_REGISTRY[key] = cls
    else:
        if cls.MESSAGE_TYPE < 0:
            raise ValueError(f"{cls.__name__} has no MESSAGE_TYPE")
        if cls.MESSAGE_TYPE in MESSAGE_REGISTRY:
            raise ValueError(f"duplicate message type {cls.MESSAGE_TYPE}")
        MESSAGE_REGISTRY[cls.MESSAGE_TYPE] = cls
    return cls


def maybe_enum(value: int, enum_cls: type[IntEnum]) -> int:
    """Convert to the enum member when defined; keep the raw int otherwise."""
    try:
        return enum_cls(value)
    except ValueError:
        return value


def bind_params(
    cls: type[LLRPStruct],
    kwargs: dict[str, Any],
    params: list[LLRPParameter],
) -> list[LLRPParameter]:
    """Assign decoded sub-parameters to a container's slots, in slot order.

    Returns the parameters that matched no slot (the caller preserves them on
    the instance so unknown extensions survive a decode/encode round-trip).
    """
    for slot in cls._SLOTS:
        if slot.is_list:
            kwargs.setdefault(slot.attr, [])
    leftovers: list[LLRPParameter] = []
    for p in params:
        for slot in cls._SLOTS:
            if isinstance(p, slot.types):
                if slot.is_list:
                    kwargs[slot.attr].append(p)
                    break
                if slot.attr not in kwargs:
                    kwargs[slot.attr] = p
                    break
        else:
            leftovers.append(p)
    for slot in cls._SLOTS:
        if slot.min_count:
            if slot.is_list:
                if len(kwargs[slot.attr]) < slot.min_count:
                    raise MessageDecodeError(
                        f"{cls.__name__} requires at least {slot.min_count} "
                        f"'{slot.attr}' parameter(s)"
                    )
            elif slot.attr not in kwargs:
                raise MessageDecodeError(f"{cls.__name__} missing required parameter '{slot.attr}'")
    return leftovers


def decode_struct(cls: type[_S], r: ByteReader, end: int) -> _S:
    """Decode fields and sub-parameters of a container into an instance."""
    kwargs = cls._decode_fields(r, end)
    if not r.bit_aligned:
        raise MessageDecodeError(f"{cls.__name__} fields did not end byte-aligned")
    subparams: list[LLRPParameter] = []
    while r.byte_pos < end:
        subparams.append(decode_parameter(r))
    leftovers = bind_params(cls, kwargs, subparams)
    obj = cls(**kwargs)
    if leftovers:
        obj.unbound_params.extend(leftovers)
    return obj


def decode_parameter(r: ByteReader) -> LLRPParameter:
    """Decode one parameter (TV or TLV, standard or custom) at the cursor."""
    start = r.byte_pos
    first = r.peek_u8()
    if first & 0x80:
        tv_type = first & 0x7F
        tv_cls = PARAMETER_REGISTRY.get(tv_type)
        if tv_cls is None or not tv_cls.IS_TV:
            raise MessageDecodeError(f"unknown TV parameter type {tv_type}")
        r.u8()
        return tv_cls(**tv_cls._decode_fields(r, r.size))
    ptype = r.u16() & 0x3FF
    length = r.u16()
    if length < 4:
        raise MessageDecodeError(f"TLV parameter length {length} is below the 4-byte minimum")
    end = start + length
    if end > r.size:
        raise MessageDecodeError(f"TLV parameter length {length} overruns the buffer")
    obj: LLRPParameter
    if ptype == TYPE_CUSTOM:
        if end - r.byte_pos < 8:
            raise MessageDecodeError("custom parameter too short for vendor/subtype prefix")
        vendor = r.u32()
        subtype = r.u32()
        custom_cls = CUSTOM_PARAMETER_REGISTRY.get((vendor, subtype))
        if custom_cls is None:
            return UnknownCustomParameter(vendor_id=vendor, subtype=subtype, payload=r.read_to(end))
        obj = decode_struct(custom_cls, r, end)
    else:
        cls = PARAMETER_REGISTRY.get(ptype)
        if cls is None or cls.IS_TV:
            return UnknownParameter(param_type=ptype, payload=r.read_to(end))
        obj = decode_struct(cls, r, end)
    if r.byte_pos != end:
        raise MessageDecodeError(
            f"{type(obj).__name__} decoded {r.byte_pos - start} byte(s) "
            f"but its length field says {length}"
        )
    return obj


def decode_message(data: bytes) -> LLRPMessage:
    """Decode one complete framed LLRP message from ``data``.

    The buffer must contain exactly one message (header length == buffer
    size); connection-level framing splits the TCP stream upstream of this.
    """
    if len(data) < MESSAGE_HEADER_LEN:
        raise MessageDecodeError(f"message truncated: {len(data)} byte(s), header needs 10")
    r = ByteReader(data)
    header = r.u16()
    version = (header >> 10) & 0x7
    msg_type = header & 0x3FF
    length = r.u32()
    message_id = r.u32()
    if length != len(data):
        raise MessageDecodeError(
            f"message length field says {length} byte(s) but buffer holds {len(data)}"
        )
    end = len(data)
    obj: LLRPMessage
    if msg_type == TYPE_CUSTOM:
        if end - r.byte_pos < 5:
            raise MessageDecodeError("custom message too short for vendor/subtype prefix")
        vendor = r.u32()
        subtype = r.u8()
        custom_cls = CUSTOM_MESSAGE_REGISTRY.get((vendor, subtype))
        if custom_cls is None:
            obj = UnknownCustomMessage(vendor_id=vendor, subtype=subtype, payload=r.read_to(end))
        else:
            obj = decode_struct(custom_cls, r, end)
    else:
        msg_cls = MESSAGE_REGISTRY.get(msg_type)
        if msg_cls is None:
            obj = UnknownMessage(msg_type=msg_type, payload=r.read_to(end))
        else:
            obj = decode_struct(msg_cls, r, end)
    if r.byte_pos != end:
        raise MessageDecodeError("message body ended before the length field said it would")
    obj.message_id = message_id
    obj.protocol_version = version
    return obj
