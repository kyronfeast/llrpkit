"""Edge and error-path tests for the hand-written codec layer."""

from __future__ import annotations

import pytest

from llrpkit.exceptions import MessageDecodeError, MessageEncodeError
from llrpkit.protocol import BitStr, UnknownParameter, codec, messages, params

# --- BitStr ----------------------------------------------------------------


def test_bitstr_rejects_negative_length() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        BitStr(-1, b"")


def test_bitstr_rejects_mismatched_data() -> None:
    with pytest.raises(ValueError, match="bit_len"):
        BitStr(9, b"\x00")  # 9 bits need 2 bytes


# --- ByteWriter ------------------------------------------------------------


def test_writer_rejects_out_of_range_bits() -> None:
    w = codec.ByteWriter()
    with pytest.raises(MessageEncodeError):
        w.bits(4, 2)  # 4 does not fit in 2 bits
    with pytest.raises(MessageEncodeError):
        w.bits(1, 0)  # zero-width field


def test_writer_rejects_out_of_range_integers() -> None:
    w = codec.ByteWriter()
    with pytest.raises(MessageEncodeError):
        w.u8(256)
    with pytest.raises(MessageEncodeError):
        w.s16(-40000)


def test_writer_rejects_unaligned_raw_and_getvalue() -> None:
    w = codec.ByteWriter()
    w.bits(1, 1)
    with pytest.raises(MessageEncodeError):
        w.raw(b"\x00")
    with pytest.raises(MessageEncodeError):
        w.getvalue()


def test_writer_unaligned_integer_goes_through_bit_path() -> None:
    w = codec.ByteWriter()
    w.bits(0b1010, 4)
    w.u16(0xABCD)  # written via the bit accumulator
    w.bits(0xF, 4)
    assert w.getvalue() == bytes.fromhex("aabcdf")


def test_writer_u96_requires_twelve_bytes() -> None:
    w = codec.ByteWriter()
    with pytest.raises(MessageEncodeError):
        w.u96(b"\x00" * 11)


def test_writer_s64_u64_roundtrip_via_reader() -> None:
    w = codec.ByteWriter()
    w.s64(-(2**40))
    w.u64(2**40)
    r = codec.ByteReader(w.getvalue())
    assert r.s64() == -(2**40)
    assert r.u64() == 2**40


# --- ByteReader ------------------------------------------------------------


def test_reader_unaligned_integer_and_alignment_guards() -> None:
    r = codec.ByteReader(bytes.fromhex("aabcdf"))
    assert r.bits(4) == 0b1010
    assert r.u16() == 0xABCD  # read through the bit path
    with pytest.raises(MessageDecodeError):
        r.raw(1)  # still mid-byte
    assert r.bits(4) == 0xF


def test_reader_truncation_errors() -> None:
    r = codec.ByteReader(b"\x01")
    with pytest.raises(MessageDecodeError):
        r.u32()
    with pytest.raises(MessageDecodeError):
        codec.ByteReader(b"").peek_u8()
    with pytest.raises(MessageDecodeError):
        codec.ByteReader(b"").bits(3)
    with pytest.raises(MessageDecodeError):
        codec.ByteReader(b"\x00").raw(2)


def test_reader_read_to_rejects_backwards_end() -> None:
    r = codec.ByteReader(b"\x00\x01\x02")
    r.raw(2)
    with pytest.raises(MessageDecodeError):
        r.read_to(1)


def test_reader_s8_sign_extension() -> None:
    r = codec.ByteReader(b"\xd6\x2a")
    assert r.s8() == -42
    assert r.s8() == 42


# --- registries ------------------------------------------------------------


def test_duplicate_registrations_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate parameter"):
        codec.register_parameter(type("Dup", (codec.LLRPParameter,), {"PARAM_TYPE": 287}))
    with pytest.raises(ValueError, match="duplicate message"):
        codec.register_message(type("Dup", (codec.LLRPMessage,), {"MESSAGE_TYPE": 62}))
    with pytest.raises(ValueError, match="duplicate custom parameter"):
        codec.register_parameter(
            type("Dup", (codec.CustomParameter,), {"VENDOR_ID": 25882, "SUBTYPE": 23})
        )
    with pytest.raises(ValueError, match="duplicate custom message"):
        codec.register_message(
            type("Dup", (codec.CustomMessage,), {"VENDOR_ID": 25882, "SUBTYPE": 21})
        )


def test_unregistered_bases_rejected() -> None:
    with pytest.raises(ValueError, match="no PARAM_TYPE"):
        codec.register_parameter(type("NoType", (codec.LLRPParameter,), {}))
    with pytest.raises(ValueError, match="no MESSAGE_TYPE"):
        codec.register_message(type("NoType", (codec.LLRPMessage,), {}))


# --- encode guards ---------------------------------------------------------


def test_encode_rejects_oversized_tlv() -> None:
    big = UnknownParameter(param_type=500, payload=b"\x00" * 0xFFFC)
    with pytest.raises(MessageEncodeError):
        big.encode()


def test_encode_rejects_bad_message_id_and_version() -> None:
    msg = messages.KEEPALIVE()
    with pytest.raises(MessageEncodeError):
        msg.to_bytes(message_id=2**32)
    with pytest.raises(MessageEncodeError):
        msg.to_bytes(message_id=1, version=8)


def test_encode_rejects_missing_required_subparam() -> None:
    # ADD_ROSPEC built without its required ROSpec (bypassing the constructor).
    msg = messages.ADD_ROSPEC.__new__(messages.ADD_ROSPEC)
    object.__setattr__(msg, "ro_spec", None)
    with pytest.raises(MessageEncodeError, match="required"):
        msg.to_bytes()


def test_encode_rejects_below_min_count_list() -> None:
    # AISpec requires at least one InventoryParameterSpec on the wire.
    spec = params.AISpec(
        antenna_ids=[1],
        ai_spec_stop_trigger=params.AISpecStopTrigger(),
        inventory_parameter_specs=[],
    )
    with pytest.raises(MessageEncodeError, match="at least 1"):
        spec.encode()
