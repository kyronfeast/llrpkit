"""Inventory building blocks: ROSpec construction and the TagReport model.

``build_rospec`` assembles a complete, reader-ready ROSpec from keyword
arguments, including the Impinj Octane extensions when asked (search mode /
TagFocus in the inventory command, sub-dBm RSSI / phase / Doppler / TID in
the report content). :class:`TagReport` is the flat, unit-converted view of a
decoded ``TagReportData`` that application code actually wants.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from llrpkit.protocol import BitStr, enums, impinj, params

#: The ROSpec identifier llrpkit manages for its own inventory streams.
DEFAULT_ROSPEC_ID = 1


@dataclass(frozen=True, kw_only=True)
class TagReport:
    """One tag observation, flattened from LLRP ``TagReportData``.

    Units: ``rssi_dbm`` is dBm (sub-dBm resolution when the Impinj extension
    is present, whole dBm from the core TV parameter otherwise);
    ``phase_deg`` is degrees 0-360; ``doppler_hz`` is Hz; timestamps are
    microseconds since the Unix epoch (reader UTC clock).
    """

    epc: bytes
    antenna: int | None = None
    rssi_dbm: float | None = None
    channel_index: int | None = None
    first_seen_us: int | None = None
    last_seen_us: int | None = None
    seen_count: int | None = None
    phase_deg: float | None = None
    doppler_hz: float | None = None
    tid: bytes | None = None
    raw: params.TagReportData | None = field(default=None, compare=False, repr=False)

    @property
    def epc_hex(self) -> str:
        return self.epc.hex()

    @classmethod
    def from_param(cls, trd: params.TagReportData) -> TagReport:
        epc_param = trd.epc_parameter
        epc = epc_param.epc if isinstance(epc_param, params.EPC_96) else epc_param.epc.data
        rssi: float | None = None
        if trd.peak_rssi is not None:
            rssi = float(trd.peak_rssi.peak_rssi)
        phase: float | None = None
        doppler: float | None = None
        tid: bytes | None = None
        for p in trd.custom:
            if isinstance(p, impinj.ImpinjPeakRSSI):
                rssi = p.rssi / 100.0
            elif isinstance(p, impinj.ImpinjRFPhaseAngle):
                phase = p.phase_angle * (360.0 / 4096.0)
            elif isinstance(p, impinj.ImpinjRFDopplerFrequency):
                doppler = p.doppler_frequency / 16.0
            elif isinstance(p, impinj.ImpinjSerializedTID):
                tid = b"".join(w.to_bytes(2, "big") for w in p.t_id)
        return cls(
            epc=epc,
            antenna=trd.antenna_id.antenna_id if trd.antenna_id else None,
            rssi_dbm=rssi,
            channel_index=trd.channel_index.channel_index if trd.channel_index else None,
            first_seen_us=(
                trd.first_seen_timestamp_utc.microseconds if trd.first_seen_timestamp_utc else None
            ),
            last_seen_us=(
                trd.last_seen_timestamp_utc.microseconds if trd.last_seen_timestamp_utc else None
            ),
            seen_count=trd.tag_seen_count.tag_count if trd.tag_seen_count else None,
            phase_deg=phase,
            doppler_hz=doppler,
            tid=tid,
            raw=trd,
        )


def build_rospec(
    *,
    ro_spec_id: int = DEFAULT_ROSPEC_ID,
    antennas: Sequence[int] = (),
    session: int = 1,
    search_mode: int | None = None,
    mode_index: int | None = None,
    tari: int = 0,
    transmit_power_index: int | None = None,
    hop_table_id: int = 1,
    channel_index: int = 1,
    tag_population: int = 32,
    epc_filter: bytes | str | None = None,
    filter_action: str = "include",
    filter_mb: int = 1,
    filter_pointer: int = 0x20,
    report_every_n: int = 1,
    duration_ms: int | None = None,
    enable_impinj_reports: bool = False,
    include_phase: bool = False,
    include_doppler: bool = False,
    include_tid: bool = False,
) -> params.ROSpec:
    """Assemble a complete ROSpec for a llrpkit-managed inventory.

    The spec starts and stops on explicit ``START_ROSPEC``/``STOP_ROSPEC``
    (null triggers) unless ``duration_ms`` sets an AISpec duration stop.
    ``antennas`` empty means all antennas (LLRP antenna ID 0). ``session``
    is the C1G2 session (0-3). ``search_mode`` takes an
    :class:`~llrpkit.protocol.impinj.ImpinjInventorySearchType` value —
    TagFocus is ``Single_Target_With_Suppression`` and requires session 1.
    ``epc_filter`` (bytes or hex string) selects tags by EPC prefix before
    they are ever inventoried — with ``filter_action="exclude"`` the matching
    tags are skipped instead. ``filter_mb``/``filter_pointer`` retarget the
    match for advanced uses (e.g. TID-bank filters).
    ``transmit_power_index`` indexes the reader's transmit power table (see
    ``ReaderCapabilities.power_index_for_dbm``); ``hop_table_id`` and
    ``channel_index`` matter only for frequency-hopping / fixed-channel
    regulatory regions respectively.
    """
    if not 0 <= session <= 3:
        raise ValueError(f"session must be 0-3, got {session}")
    if tag_population < 1:
        raise ValueError(f"tag_population must be positive, got {tag_population}")
    if isinstance(epc_filter, str):
        try:
            epc_filter = bytes.fromhex(epc_filter)
        except ValueError as exc:
            raise ValueError(f"epc_filter {epc_filter!r} is not valid hex") from exc
    if filter_action not in ("include", "exclude"):
        raise ValueError(f"filter_action must be 'include' or 'exclude', got {filter_action!r}")
    inv_cmd = params.C1G2InventoryCommand(
        tag_inventory_state_aware=False,
        c1_g2_singulation_control=params.C1G2SingulationControl(
            session=session, tag_population=tag_population, tag_transit_time=0
        ),
    )
    if epc_filter is not None:
        # A C1G2 select filter: match `epc_filter` as a prefix of EPC memory
        # (bank 1 from bit 0x20, where the EPC proper begins). "include"
        # inventories only matching tags; "exclude" inventories the rest.
        action = (
            enums.C1G2StateUnawareAction.Select_Unselect
            if filter_action == "include"
            else enums.C1G2StateUnawareAction.Unselect_Select
        )
        inv_cmd.c1_g2_filters.append(
            params.C1G2Filter(
                t=enums.C1G2TruncateAction.Do_Not_Truncate,
                c1_g2_tag_inventory_mask=params.C1G2TagInventoryMask(
                    mb=filter_mb,
                    pointer=filter_pointer,
                    tag_mask=BitStr.from_bytes(epc_filter),
                ),
                c1_g2_tag_inventory_state_unaware_filter_action=(
                    params.C1G2TagInventoryStateUnawareFilterAction(action=action)
                ),
            )
        )
    if mode_index is not None:
        inv_cmd.c1_g2_rf_control = params.C1G2RFControl(mode_index=mode_index, tari=tari)
    if search_mode is not None:
        inv_cmd.custom.append(
            impinj.ImpinjInventorySearchMode(inventory_search_mode=int(search_mode))
        )
    ant_cfg = params.AntennaConfiguration(
        antenna_id=0, air_protocol_inventory_command_settings=[inv_cmd]
    )
    if transmit_power_index is not None:
        ant_cfg.rf_transmitter = params.RFTransmitter(
            hop_table_id=hop_table_id,
            channel_index=channel_index,
            transmit_power=transmit_power_index,
        )

    stop_type = enums.AISpecStopTriggerType.Null
    stop_duration = 0
    if duration_ms is not None:
        stop_type = enums.AISpecStopTriggerType.Duration
        stop_duration = duration_ms
    ai_spec = params.AISpec(
        antenna_ids=list(antennas) or [0],
        ai_spec_stop_trigger=params.AISpecStopTrigger(
            ai_spec_stop_trigger_type=stop_type, duration_trigger=stop_duration
        ),
        inventory_parameter_specs=[
            params.InventoryParameterSpec(
                inventory_parameter_spec_id=1,
                protocol_id=enums.AirProtocols.EPCGlobalClass1Gen2,
                antenna_configurations=[ant_cfg],
            )
        ],
    )

    content = params.TagReportContentSelector(
        enable_ro_spec_id=False,
        enable_spec_index=False,
        enable_inventory_parameter_spec_id=False,
        enable_antenna_id=True,
        enable_channel_index=True,
        enable_peak_rssi=True,
        enable_first_seen_timestamp=True,
        enable_last_seen_timestamp=True,
        enable_tag_seen_count=True,
        enable_access_spec_id=False,
        air_protocol_epc_memory_selectors=[
            params.C1G2EPCMemorySelector(enable_crc=False, enable_pc_bits=False)
        ],
    )
    report_spec = params.ROReportSpec(
        ro_report_trigger=enums.ROReportTriggerType.Upon_N_Tags_Or_End_Of_ROSpec,
        n=report_every_n,
        tag_report_content_selector=content,
    )
    if enable_impinj_reports:
        selector = impinj.ImpinjTagReportContentSelector(
            impinj_enable_peak_rssi=impinj.ImpinjEnablePeakRSSI(peak_rssi_mode=1)
        )
        if include_phase:
            selector.impinj_enable_rf_phase_angle = impinj.ImpinjEnableRFPhaseAngle(
                rf_phase_angle_mode=1
            )
        if include_doppler:
            selector.impinj_enable_rf_doppler_frequency = impinj.ImpinjEnableRFDopplerFrequency(
                rf_doppler_frequency_mode=1
            )
        if include_tid:
            selector.impinj_enable_serialized_t_id = impinj.ImpinjEnableSerializedTID(
                serialized_t_id_mode=1
            )
        report_spec.custom.append(selector)

    return params.ROSpec(
        ro_spec_id=ro_spec_id,
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
        spec_parameters=[ai_spec],
        ro_report_spec=report_spec,
    )
