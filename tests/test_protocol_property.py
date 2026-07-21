"""Property-based and exhaustive-sweep tests for the protocol layer."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from llrpkit.exceptions import MessageDecodeError, MessageEncodeError
from llrpkit.protocol import BitStr, LLRPMessage, codec, decode_message, impinj, messages, params

# --- exhaustive default-construction sweep over every generated class ------

ALL_PARAM_CLASSES = sorted(
    set(codec.PARAMETER_REGISTRY.values()) | set(codec.CUSTOM_PARAMETER_REGISTRY.values()),
    key=lambda c: c.__name__,
)
ALL_MESSAGE_CLASSES = sorted(
    set(codec.MESSAGE_REGISTRY.values()) | set(codec.CUSTOM_MESSAGE_REGISTRY.values()),
    key=lambda c: c.__name__,
)


@pytest.mark.parametrize("cls", ALL_PARAM_CLASSES, ids=lambda c: c.__name__)
def test_every_parameter_default_roundtrip(cls: type[codec.LLRPParameter]) -> None:
    try:
        obj = cls()
    except TypeError:
        pytest.skip("has required constructor arguments")
    try:
        encoded = obj.encode()
    except MessageEncodeError:
        pytest.skip("default instance violates a min-count constraint")
    r = codec.ByteReader(encoded)
    decoded = codec.decode_parameter(r)
    assert r.byte_pos == len(encoded)
    assert decoded == obj
    assert decoded.encode() == encoded


@pytest.mark.parametrize("cls", ALL_MESSAGE_CLASSES, ids=lambda c: c.__name__)
def test_every_message_default_roundtrip(cls: type[codec.LLRPMessage]) -> None:
    try:
        obj = cls()
    except TypeError:
        pytest.skip("has required constructor arguments")
    try:
        frame = obj.to_bytes(message_id=3)
    except MessageEncodeError:
        pytest.skip("default instance violates a min-count constraint")
    decoded = decode_message(frame)
    assert decoded == obj
    assert decoded.to_bytes(message_id=3) == frame


# --- hypothesis round-trips ------------------------------------------------


@given(code=st.integers(0, 0xFFFF), desc=st.text(max_size=64))
def test_llrp_status_roundtrip(code: int, desc: str) -> None:
    obj = params.LLRPStatus(status_code=code, error_description=desc)
    r = codec.ByteReader(obj.encode())
    assert codec.decode_parameter(r) == obj


_ai_spec = st.builds(
    params.AISpec,
    antenna_ids=st.lists(st.integers(0, 0xFFFF), max_size=4),
    ai_spec_stop_trigger=st.builds(
        params.AISpecStopTrigger,
        ai_spec_stop_trigger_type=st.integers(0, 2),
        duration_trigger=st.integers(0, 0xFFFFFFFF),
    ),
    inventory_parameter_specs=st.lists(
        st.builds(
            params.InventoryParameterSpec,
            inventory_parameter_spec_id=st.integers(0, 0xFFFF),
            protocol_id=st.integers(0, 0xFF),
        ),
        min_size=1,
        max_size=2,
    ),
)

_ro_spec = st.builds(
    params.ROSpec,
    ro_spec_id=st.integers(0, 0xFFFFFFFF),
    priority=st.integers(0, 7),
    current_state=st.integers(0, 2),
    ro_boundary_spec=st.builds(
        params.ROBoundarySpec,
        ro_spec_start_trigger=st.builds(
            params.ROSpecStartTrigger, ro_spec_start_trigger_type=st.integers(0, 3)
        ),
        ro_spec_stop_trigger=st.builds(
            params.ROSpecStopTrigger,
            ro_spec_stop_trigger_type=st.integers(0, 1),
            duration_trigger_value=st.integers(0, 0xFFFFFFFF),
        ),
    ),
    spec_parameters=st.lists(_ai_spec, min_size=1, max_size=2),
)


@given(rospec=_ro_spec, mid=st.integers(0, 0xFFFFFFFF))
@settings(max_examples=50)
def test_add_rospec_tree_roundtrip(rospec: params.ROSpec, mid: int) -> None:
    msg = messages.ADD_ROSPEC(ro_spec=rospec)
    frame = msg.to_bytes(message_id=mid)
    decoded = decode_message(frame)
    assert decoded == msg
    assert decoded.message_id == mid
    assert decoded.to_bytes(message_id=mid) == frame


_epc_choice = st.one_of(
    st.builds(params.EPC_96, epc=st.binary(min_size=12, max_size=12)),
    st.builds(params.EPCData, epc=st.binary(max_size=16).map(BitStr.from_bytes)),
)

_tag_report = st.builds(
    params.TagReportData,
    epc_parameter=_epc_choice,
    antenna_id=st.none() | st.builds(params.AntennaID, antenna_id=st.integers(0, 0xFFFF)),
    peak_rssi=st.none() | st.builds(params.PeakRSSI, peak_rssi=st.integers(-128, 127)),
    tag_seen_count=st.none() | st.builds(params.TagSeenCount, tag_count=st.integers(0, 0xFFFF)),
    custom=st.lists(
        st.one_of(
            st.builds(impinj.ImpinjPeakRSSI, rssi=st.integers(-(2**15), 2**15 - 1)),
            st.builds(impinj.ImpinjRFPhaseAngle, phase_angle=st.integers(0, 4095)),
            st.builds(impinj.ImpinjRFDopplerFrequency, doppler_frequency=st.integers(-1000, 1000)),
        ),
        max_size=3,
    ),
)


@given(reports=st.lists(_tag_report, min_size=1, max_size=3))
@settings(max_examples=50)
def test_tag_report_roundtrip(reports: list[params.TagReportData]) -> None:
    msg = messages.RO_ACCESS_REPORT(tag_report_datas=reports)
    frame = msg.to_bytes()
    decoded = decode_message(frame)
    assert decoded == msg
    assert decoded.to_bytes() == frame


def test_partial_byte_bit_string_roundtrip() -> None:
    epc = params.EPCData(epc=BitStr(4, b"\xa0"))
    r = codec.ByteReader(epc.encode())
    assert codec.decode_parameter(r) == epc


# --- decoder robustness: arbitrary bytes must never crash ------------------


@given(data=st.binary(max_size=80))
@settings(max_examples=300)
def test_decoder_never_crashes_on_garbage(data: bytes) -> None:
    try:
        msg = decode_message(data)
    except MessageDecodeError:
        return
    assert isinstance(msg, LLRPMessage)


@given(
    mtype=st.integers(0, 1023),
    ver=st.integers(0, 7),
    body=st.binary(max_size=48),
)
@settings(max_examples=300)
def test_decoder_never_crashes_on_framed_garbage(mtype: int, ver: int, body: bytes) -> None:
    total = 10 + len(body)
    frame = (
        ((ver << 10) | mtype).to_bytes(2, "big")
        + total.to_bytes(4, "big")
        + (12345).to_bytes(4, "big")
        + body
    )
    try:
        msg = decode_message(frame)
    except MessageDecodeError:
        return
    assert isinstance(msg, LLRPMessage)
    assert msg.message_id == 12345
