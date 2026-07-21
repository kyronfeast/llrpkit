"""Golden wire-format vectors, hand-computed from the LLRP 1.0.1 definitions.

Every expected hex string here was derived by hand from the spec layout (the
byte math is in the comments), so these tests are an independent check on the
code generator and codec — not a regression snapshot of their own output.
"""

from __future__ import annotations

import pytest

from llrpkit.exceptions import MessageDecodeError
from llrpkit.protocol import (
    BitStr,
    LLRPMessage,
    UnknownCustomMessage,
    UnknownMessage,
    UnknownParameter,
    decode_message,
    enums,
    impinj,
    messages,
    params,
)


def roundtrip(msg: LLRPMessage, message_id: int, expected_hex: str) -> LLRPMessage:
    """Assert encode matches the hand-computed bytes and decode inverts them."""
    frame = msg.to_bytes(message_id=message_id)
    assert frame.hex() == expected_hex
    decoded = decode_message(frame)
    assert decoded == msg
    assert decoded.message_id == message_id
    assert decoded.protocol_version == 1
    return decoded


# --- empty-body messages ---------------------------------------------------


def test_keepalive() -> None:
    # header: ver 1, type 62 -> (1<<10)|62 = 0x043E; length 10; id 5
    roundtrip(messages.KEEPALIVE(), 5, "043e0000000a00000005")


def test_keepalive_ack() -> None:
    # type 72 -> 0x0448
    roundtrip(messages.KEEPALIVE_ACK(), 5, "04480000000a00000005")


def test_close_connection() -> None:
    # type 14 -> 0x040E
    roundtrip(messages.CLOSE_CONNECTION(), 1, "040e0000000a00000001")


# --- simple fields ---------------------------------------------------------


def test_get_reader_capabilities() -> None:
    # type 1 -> 0x0401; body: RequestedData u8 = 0 (All); length 11
    msg = messages.GET_READER_CAPABILITIES(
        requested_data=enums.GetReaderCapabilitiesRequestedData.All
    )
    roundtrip(msg, 1, "04010000000b0000000100")


def test_set_reader_config_bit_field() -> None:
    # type 3 -> 0x0403; body: ResetToFactoryDefault bit 1 + 7 reserved -> 0x80
    msg = messages.SET_READER_CONFIG(reset_to_factory_default=True)
    roundtrip(msg, 4, "04030000000b0000000480")


def test_error_message_with_status() -> None:
    # ERROR_MESSAGE type 100 -> 0x0464
    # LLRPStatus TLV type 287 -> 0x011F; code u16 0; desc "boom" utf8v (2+4)
    # param len 4+2+2+4 = 12 = 0x0C; message len 10+12 = 22 = 0x16
    msg = messages.ERROR_MESSAGE(
        llrp_status=params.LLRPStatus(
            status_code=enums.StatusCode.M_Success, error_description="boom"
        )
    )
    decoded = roundtrip(msg, 99, "04640000001600000063011f000c00000004626f6f6d")
    assert isinstance(decoded, messages.ERROR_MESSAGE)
    assert decoded.llrp_status.status_code is enums.StatusCode.M_Success
    assert decoded.llrp_status.error_description == "boom"


# --- nested parameter trees ------------------------------------------------


def test_add_rospec_full_tree() -> None:
    # ROSpec (177 -> 0x00B1): id u32=1, priority u8=0, state u8=0        -> 6
    #   ROBoundarySpec (178 -> 0x00B2): 4 + 5 + 9                        -> 18
    #     ROSpecStartTrigger (179 -> 0x00B3): type u8=0 (Null)           -> 5
    #     ROSpecStopTrigger (182 -> 0x00B6): type u8=0, duration u32=0   -> 9
    #   AISpec (183 -> 0x00B7): u16v count 1 + [1] = 4 bytes             -> 24
    #     AISpecStopTrigger (184 -> 0x00B8): type u8=0, duration u32=0   -> 9
    #     InventoryParameterSpec (186 -> 0x00BA): id u16=1, proto u8=1   -> 7
    # ROSpec len = 4+6+18+24 = 52 = 0x34; message len = 62 = 0x3E
    msg = messages.ADD_ROSPEC(
        ro_spec=params.ROSpec(
            ro_spec_id=1,
            priority=0,
            current_state=enums.ROSpecState.Disabled,
            ro_boundary_spec=params.ROBoundarySpec(
                ro_spec_start_trigger=params.ROSpecStartTrigger(
                    ro_spec_start_trigger_type=enums.ROSpecStartTriggerType.Null
                ),
                ro_spec_stop_trigger=params.ROSpecStopTrigger(
                    ro_spec_stop_trigger_type=enums.ROSpecStopTriggerType.Null,
                    duration_trigger_value=0,
                ),
            ),
            spec_parameters=[
                params.AISpec(
                    antenna_ids=[1],
                    ai_spec_stop_trigger=params.AISpecStopTrigger(
                        ai_spec_stop_trigger_type=enums.AISpecStopTriggerType.Null,
                        duration_trigger=0,
                    ),
                    inventory_parameter_specs=[
                        params.InventoryParameterSpec(
                            inventory_parameter_spec_id=1,
                            protocol_id=enums.AirProtocols.EPCGlobalClass1Gen2,
                        )
                    ],
                )
            ],
        )
    )
    roundtrip(
        msg,
        10,
        "04140000003e0000000a"
        "00b100340000000100"
        "00"
        "00b2001200b300050000b60009000000000000"
        "b700180001000100b80009000000000000ba0007000101",
    )


def test_tag_report_with_tv_params() -> None:
    # RO_ACCESS_REPORT type 61 -> 0x043D
    # TagReportData (240 -> 0x00F0):
    #   EPC-96 TV (13): 0x8D + 12 bytes                                  -> 13
    #   AntennaID TV (1): 0x81 + u16 3                                   -> 3
    #   PeakRSSI TV (6): 0x86 + s8 -42 = 0xD6                            -> 2
    #   TagSeenCount TV (8): 0x88 + u16 2                                -> 3
    # TagReportData len = 4+13+3+2+3 = 25 = 0x19; message len = 35 = 0x23
    msg = messages.RO_ACCESS_REPORT(
        tag_report_datas=[
            params.TagReportData(
                epc_parameter=params.EPC_96(epc=bytes(range(12))),
                antenna_id=params.AntennaID(antenna_id=3),
                peak_rssi=params.PeakRSSI(peak_rssi=-42),
                tag_seen_count=params.TagSeenCount(tag_count=2),
            )
        ]
    )
    decoded = roundtrip(
        msg,
        7,
        "043d000000230000000700f000198d000102030405060708090a0b81000386d6880002",
    )
    assert isinstance(decoded, messages.RO_ACCESS_REPORT)
    report = decoded.tag_report_datas[0]
    assert report.peak_rssi is not None
    assert report.peak_rssi.peak_rssi == -42
    assert isinstance(report.epc_parameter, params.EPC_96)
    assert report.epc_parameter.epc == bytes(range(12))


def test_epc_data_bit_vector() -> None:
    # EPCData (241 -> 0x00F1): u1v = bit count u16 96 = 0x0060 + 12 bytes
    # EPCData len = 4+2+12 = 18 = 0x12; TagReportData len = 22 = 0x16
    # message len = 10+22 = 32 = 0x20
    epc = bytes.fromhex("30395fb4a3d525c000000001")
    msg = messages.RO_ACCESS_REPORT(
        tag_report_datas=[
            params.TagReportData(epc_parameter=params.EPCData(epc=BitStr.from_bytes(epc)))
        ]
    )
    decoded = roundtrip(msg, 8, "043d000000200000000800f0001600f100120060" + epc.hex())
    assert isinstance(decoded, messages.RO_ACCESS_REPORT)
    assert isinstance(decoded.tag_report_datas[0].epc_parameter, params.EPCData)
    assert decoded.tag_report_datas[0].epc_parameter.epc == BitStr(96, epc)


def test_reader_event_notification() -> None:
    # READER_EVENT_NOTIFICATION type 63 -> 0x043F
    # ReaderEventNotificationData (246 -> 0x00F6):
    #   UTCTimestamp (128 -> 0x0080): u64 1_600_000_000_000_000 us
    #     = 0x0005AF3107A40000; param len 12
    #   ConnectionAttemptEvent (256 -> 0x0100): status u16 0; len 6
    # data len = 4+12+6 = 22 = 0x16; message len = 32 = 0x20
    msg = messages.READER_EVENT_NOTIFICATION(
        reader_event_notification_data=params.ReaderEventNotificationData(
            timestamp=params.UTCTimestamp(microseconds=1_600_000_000_000_000),
            connection_attempt_event=params.ConnectionAttemptEvent(
                status=enums.ConnectionAttemptStatusType.Success
            ),
        )
    )
    decoded = roundtrip(
        msg,
        0,
        "043f0000002000000000" + "00f60016" + "0080000c0005af3107a40000" + "010000060000",
    )
    assert isinstance(decoded, messages.READER_EVENT_NOTIFICATION)
    event = decoded.reader_event_notification_data.connection_attempt_event
    assert event is not None
    assert event.status is enums.ConnectionAttemptStatusType.Success


# --- Impinj Octane extensions ---------------------------------------------


def test_impinj_enable_extensions() -> None:
    # CUSTOM_MESSAGE type 1023 -> header (1<<10)|1023 = 0x07FF
    # body: vendor u32 25882 = 0x651A, subtype u8 21 = 0x15, reserved u32
    # message len = 10+4+1+4 = 19 = 0x13
    roundtrip(impinj.IMPINJ_ENABLE_EXTENSIONS(), 2, "07ff00000013000000020000651a1500000000")


def test_impinj_enable_extensions_response() -> None:
    # subtype 22 = 0x16; LLRPStatus success, empty description -> 8 bytes
    # message len = 10+5+8 = 23 = 0x17
    msg = impinj.IMPINJ_ENABLE_EXTENSIONS_RESPONSE(
        llrp_status=params.LLRPStatus(status_code=enums.StatusCode.M_Success)
    )
    roundtrip(msg, 2, "07ff00000017000000020000651a16011f000800000000")


def test_impinj_search_mode_parameter() -> None:
    # Custom parameter TLV: type 1023 -> 0x03FF, len 4+4+4+2 = 14 = 0x0E
    # vendor 0x651A, subtype u32 23 = 0x17, mode u16 3 (TagFocus)
    p = impinj.ImpinjInventorySearchMode(
        inventory_search_mode=impinj.ImpinjInventorySearchType.Single_Target_With_Suppression
    )
    assert p.encode().hex() == "03ff000e0000651a000000170003"


def test_impinj_peak_rssi_parameter() -> None:
    # subtype u32 57 = 0x39; RSSI s16 -4250 (= -42.50 dBm) -> 0xEF66
    p = impinj.ImpinjPeakRSSI(rssi=-4250)
    assert p.encode().hex() == "03ff000e0000651a00000039ef66"


def test_impinj_params_ride_in_tag_report_custom_slot() -> None:
    msg = messages.RO_ACCESS_REPORT(
        tag_report_datas=[
            params.TagReportData(
                epc_parameter=params.EPC_96(epc=b"\x00" * 12),
                custom=[
                    impinj.ImpinjPeakRSSI(rssi=-3001),
                    impinj.ImpinjRFPhaseAngle(phase_angle=1024),
                ],
            )
        ]
    )
    decoded = decode_message(msg.to_bytes(message_id=1))
    assert decoded == msg
    assert isinstance(decoded, messages.RO_ACCESS_REPORT)
    first = decoded.tag_report_datas[0].custom[0]
    assert isinstance(first, impinj.ImpinjPeakRSSI)
    assert first.rssi == -3001


# --- resilience and forward compatibility ---------------------------------


def test_unknown_message_survives_roundtrip() -> None:
    # Message type 300 does not exist in LLRP 1.0.1 (e.g. a 1.1 message).
    unknown = UnknownMessage(msg_type=300, payload=b"\x01\x02\x03")
    frame = unknown.to_bytes(message_id=9)
    decoded = decode_message(frame)
    assert isinstance(decoded, UnknownMessage)
    assert decoded == unknown
    assert decoded.to_bytes(message_id=9) == frame


def test_unknown_custom_message_survives_roundtrip() -> None:
    # vendor 666 = 0x29A, subtype 42 = 0x2A, payload de ad; len 17 = 0x11
    frame = bytes.fromhex("07ff00000011000000010000029a2adead")
    decoded = decode_message(frame)
    assert isinstance(decoded, UnknownCustomMessage)
    assert decoded.vendor_id == 666
    assert decoded.subtype == 42
    assert decoded.payload == b"\xde\xad"
    assert decoded.to_bytes(message_id=1) == frame


def test_unknown_tlv_parameter_preserved_via_unbound() -> None:
    inner = UnknownParameter(param_type=500, payload=b"\xaa\xbb")
    msg = messages.READER_EVENT_NOTIFICATION(
        reader_event_notification_data=params.ReaderEventNotificationData(
            timestamp=params.UTCTimestamp(microseconds=1),
        )
    )
    msg.reader_event_notification_data.unbound_params.append(inner)
    reframe = msg.to_bytes()
    redecoded = decode_message(reframe)
    assert isinstance(redecoded, messages.READER_EVENT_NOTIFICATION)
    assert redecoded.reader_event_notification_data.unbound_params == [inner]
    assert redecoded.to_bytes() == reframe


def test_decode_rejects_truncated_header() -> None:
    with pytest.raises(MessageDecodeError):
        decode_message(b"\x04\x3e\x00")


def test_decode_rejects_length_mismatch() -> None:
    with pytest.raises(MessageDecodeError):
        decode_message(bytes.fromhex("043e0000000b00000005"))  # says 11, is 10


def test_decode_rejects_unknown_tv_type() -> None:
    # TagReportData containing TV byte 0xFF (type 127, undefined)
    with pytest.raises(MessageDecodeError):
        decode_message(bytes.fromhex("043d0000000f0000000100f00005ff"))


def test_decode_rejects_missing_required_parameter() -> None:
    # ADD_ROSPEC with an empty body is missing its required ROSpec.
    with pytest.raises(MessageDecodeError):
        decode_message(bytes.fromhex("04140000000a00000001"))


def test_decode_rejects_bad_utf8() -> None:
    # ERROR_MESSAGE with LLRPStatus whose description bytes are invalid UTF-8:
    # LLRPStatus len 4+2+2+2 = 10 = 0x0A; message len 20 = 0x14
    with pytest.raises(MessageDecodeError):
        decode_message(bytes.fromhex("046400000014000000" + "01011f000a0000" + "0002ffff"))


def test_decode_rejects_short_tlv_length() -> None:
    # TLV claiming length 2 (< 4-byte minimum) inside a message body
    with pytest.raises(MessageDecodeError):
        decode_message(bytes.fromhex("043d0000000e0000000100f00002"))


def test_version_passthrough() -> None:
    frame = messages.KEEPALIVE().to_bytes(message_id=1, version=2)
    decoded = decode_message(frame)
    assert decoded.protocol_version == 2
    assert decoded.to_bytes(message_id=1) == frame
