"""Unit tests for ROSpec construction and TagReport extraction."""

from __future__ import annotations

from llrpkit.inventory import TagReport, build_rospec
from llrpkit.protocol import decode_message, enums, impinj, messages, params


def test_build_rospec_roundtrips_and_carries_impinj_options() -> None:
    rospec = build_rospec(
        antennas=(1, 3),
        session=2,
        search_mode=impinj.ImpinjInventorySearchType.Single_Target_With_Suppression,
        mode_index=1002,
        transmit_power_index=11,
        enable_impinj_reports=True,
        include_phase=True,
        include_tid=True,
    )
    frame = messages.ADD_ROSPEC(ro_spec=rospec).to_bytes(message_id=1)
    decoded = decode_message(frame)
    assert isinstance(decoded, messages.ADD_ROSPEC)
    spec = decoded.ro_spec
    ai = spec.spec_parameters[0]
    assert isinstance(ai, params.AISpec)
    assert ai.antenna_ids == [1, 3]
    inv_cfg = ai.inventory_parameter_specs[0].antenna_configurations[0]
    assert inv_cfg.rf_transmitter is not None
    assert inv_cfg.rf_transmitter.transmit_power == 11
    cmd = inv_cfg.air_protocol_inventory_command_settings[0]
    assert isinstance(cmd, params.C1G2InventoryCommand)
    assert cmd.c1_g2_rf_control is not None
    assert cmd.c1_g2_rf_control.mode_index == 1002
    assert cmd.c1_g2_singulation_control is not None
    assert cmd.c1_g2_singulation_control.session == 2
    search = cmd.custom[0]
    assert isinstance(search, impinj.ImpinjInventorySearchMode)
    assert search.inventory_search_mode == 3
    report = spec.ro_report_spec
    assert report is not None
    selector = report.custom[0]
    assert isinstance(selector, impinj.ImpinjTagReportContentSelector)
    assert selector.impinj_enable_peak_rssi is not None
    assert selector.impinj_enable_rf_phase_angle is not None
    assert selector.impinj_enable_serialized_t_id is not None
    assert selector.impinj_enable_rf_doppler_frequency is None


def test_build_rospec_defaults_use_all_antennas_and_null_triggers() -> None:
    rospec = build_rospec()
    ai = rospec.spec_parameters[0]
    assert isinstance(ai, params.AISpec)
    assert ai.antenna_ids == [0]
    assert int(ai.ai_spec_stop_trigger.ai_spec_stop_trigger_type) == int(
        enums.AISpecStopTriggerType.Null
    )
    assert rospec.ro_report_spec is not None
    assert rospec.ro_report_spec.custom == []


def test_build_rospec_duration_sets_aispec_stop() -> None:
    rospec = build_rospec(duration_ms=1500)
    ai = rospec.spec_parameters[0]
    assert isinstance(ai, params.AISpec)
    assert int(ai.ai_spec_stop_trigger.ai_spec_stop_trigger_type) == int(
        enums.AISpecStopTriggerType.Duration
    )
    assert ai.ai_spec_stop_trigger.duration_trigger == 1500


def test_tag_report_prefers_impinj_rssi_and_converts_units() -> None:
    trd = params.TagReportData(
        epc_parameter=params.EPC_96(epc=b"\x11" * 12),
        antenna_id=params.AntennaID(antenna_id=2),
        peak_rssi=params.PeakRSSI(peak_rssi=-50),
        tag_seen_count=params.TagSeenCount(tag_count=3),
        custom=[
            impinj.ImpinjPeakRSSI(rssi=-4712),
            impinj.ImpinjRFPhaseAngle(phase_angle=2048),
            impinj.ImpinjRFDopplerFrequency(doppler_frequency=160),
            impinj.ImpinjSerializedTID(t_id=[0xE280, 0x1105]),
        ],
    )
    tag = TagReport.from_param(trd)
    assert tag.epc == b"\x11" * 12
    assert tag.epc_hex == "11" * 12
    assert tag.antenna == 2
    assert tag.rssi_dbm == -47.12  # Impinj sub-dBm wins over the whole-dBm TV
    assert tag.phase_deg == 180.0
    assert tag.doppler_hz == 10.0
    assert tag.tid == b"\xe2\x80\x11\x05"
    assert tag.seen_count == 3


def test_tag_report_core_only_and_epcdata() -> None:
    from llrpkit.protocol import BitStr

    trd = params.TagReportData(
        epc_parameter=params.EPCData(epc=BitStr.from_bytes(b"\xaa\xbb")),
        peak_rssi=params.PeakRSSI(peak_rssi=-61),
    )
    tag = TagReport.from_param(trd)
    assert tag.epc == b"\xaa\xbb"
    assert tag.rssi_dbm == -61.0
    assert tag.phase_deg is None
    assert tag.tid is None
    assert tag.antenna is None
