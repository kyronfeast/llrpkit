"""An in-process LLRP reader emulator.

This is llrpkit's test rig and zero-hardware demo: an asyncio TCP server that
speaks genuine LLRP — the connection-attempt handshake, capabilities,
configuration, the full ROSpec lifecycle, and the Impinj Octane extensions
handshake — and streams synthetic tag reports from a configurable tag
population while a ROSpec is active.

It models protocol behavior and plausible statistics, not RF physics; the
point is that everything a client does against a real Impinj reader has a
faithful wire-level counterpart here. Behavioral realism grows over time
(reader modes and sessions influencing read rate arrive with the tuning
work); differences found against real hardware are treated as emulator bugs.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass

from llrpkit.constants import IMPINJ_PEN, MESSAGE_HEADER_LEN
from llrpkit.exceptions import MessageDecodeError
from llrpkit.protocol import BitStr, LLRPMessage, decode_message, enums, impinj, messages, params

log = logging.getLogger(__name__)

_MAX_FRAME = 4 * 1024 * 1024

#: How the RF mode index scales the synthetic read rate: FM0-style modes are
#: fast, dense-reader Miller-8 is slow, AutoSet families sit in between.
_MODE_RATE_FACTORS = {0: 1.6, 1: 1.25, 2: 1.0, 3: 0.55, 4: 1.4, 5: 1.0}


@dataclass(frozen=True)
class _ScanProfile:
    """Digest of what the active ROSpecs ask the 'RF front end' to do."""

    antennas: set[int]
    content: set[str]
    rate_factor: float
    tagfocus: bool
    #: Parsed C1G2 select filters: (mb, pointer, pattern, include).
    filters: tuple[tuple[int, int, BitStr, bool], ...] = ()
    #: Requested transmit power; scales read rate and weak-tag visibility.
    power_dbm: float = 30.0


def _bits_equal(data: bytes, offset_bits: int, pattern: BitStr) -> bool:
    """True if ``pattern`` matches ``data`` starting at ``offset_bits``."""
    if offset_bits < 0 or offset_bits + pattern.bit_len > len(data) * 8:
        return False
    for i in range(pattern.bit_len):
        p = offset_bits + i
        data_bit = (data[p // 8] >> (7 - p % 8)) & 1
        pat_bit = (pattern.data[i // 8] >> (7 - i % 8)) & 1
        if data_bit != pat_bit:
            return False
    return True


@dataclass(frozen=True)
class EmulatedTag:
    """One synthetic tag: where it can be seen and how strongly."""

    epc: bytes
    antennas: tuple[int, ...] = (1,)
    rssi_dbm: float = -55.0
    weight: float = 1.0


def default_population(count: int = 12, antenna_count: int = 4) -> list[EmulatedTag]:
    """A pleasant default tag population spread across antennas."""
    tags = []
    for i in range(count):
        epc = bytes([0xE2, 0x00, 0x00, 0x17, 0x01, 0x0B, 0x01, 0x62, 0x10, 0x00, i >> 8, i & 0xFF])
        tags.append(
            EmulatedTag(
                epc=epc,
                antennas=(1 + (i % antenna_count),),
                rssi_dbm=-45.0 - (i % 6) * 3.5,
                weight=1.0 + (i % 3),
            )
        )
    return tags


class LLRPEmulator:
    """A fake Impinj-flavored LLRP reader listening on a TCP port.

    Usage::

        async with LLRPEmulator() as emu:
            reader = Reader("127.0.0.1", emu.port)
            ...

    One controlling client at a time, as LLRP semantics require — a second
    connection is refused with the proper ``ConnectionAttemptEvent``.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        tags: Sequence[EmulatedTag] | None = None,
        reads_per_sec: float = 100.0,
        antenna_count: int = 4,
        seed: int = 1,
        model_number: int = 700,
        firmware: str = "llrpkit-emu 0.1",
    ) -> None:
        self.host = host
        self.port = port
        self.antenna_count = antenna_count
        self.reads_per_sec = reads_per_sec
        self.model_number = model_number
        self.firmware = firmware
        self.tags = list(tags) if tags is not None else default_population(12, antenna_count)
        self._rng = random.Random(seed)
        self._server: asyncio.Server | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._send_lock = asyncio.Lock()
        self._extensions_enabled = False
        self._rospecs: dict[int, tuple[params.ROSpec, str]] = {}
        #: AccessSpecs: id -> (spec, enabled); counts track stop triggers.
        self._accessspecs: dict[int, tuple[params.AccessSpec, bool]] = {}
        self._access_counts: dict[int, int] = {}
        #: Per-tag memory banks, keyed by current EPC.
        self._memories: dict[bytes, dict[int, bytearray]] = {}
        self._locked_banks: dict[bytes, set[int]] = {}
        self._report_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._keepalive_acked = asyncio.Event()
        #: KEEPALIVE_ACKs received from the client (test observability).
        self.keepalive_acks = 0
        self._drop_once: set[type[LLRPMessage]] = set()
        self._temperature = 41.5
        self._disconnected: set[int] = set()
        self._focus_counts: dict[bytes, int] = {}

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self._stop_reporting()
        self._stop_keepalive()
        if self._writer is not None:
            with contextlib.suppress(Exception):
                self._writer.close()
            self._writer = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def __aenter__(self) -> LLRPEmulator:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    # -- test hooks --------------------------------------------------------

    def drop_next(self, msg_type: type[LLRPMessage]) -> None:
        """Silently swallow the next message of ``msg_type`` (timeout testing)."""
        self._drop_once.add(msg_type)

    async def send_keepalive(self) -> None:
        self._keepalive_acked.clear()
        await self._send(messages.KEEPALIVE(), message_id=99)

    async def wait_keepalive_ack(self, timeout: float = 2.0) -> None:
        async with asyncio.timeout(timeout):
            await self._keepalive_acked.wait()

    def set_tag_passwords(
        self, epc: bytes, *, access: int | None = None, kill: int | None = None
    ) -> None:
        """Test hook: program a tag's access/kill passwords (reserved bank)."""
        reserved = self._memory_for(epc)[0]
        if kill is not None:
            reserved[0:4] = kill.to_bytes(4, "big")
        if access is not None:
            reserved[4:8] = access.to_bytes(4, "big")

    def set_temperature(self, celsius: float) -> None:
        """Set the temperature reported via the Octane extension."""
        self._temperature = celsius

    async def set_antenna_connected(self, antenna_id: int, connected: bool) -> None:
        """Fault injection: (dis)connect an antenna port and notify the client.

        A disconnected port stops producing tag reads, exactly like a cable
        pulled from a live reader, and the client receives the corresponding
        ``AntennaEvent`` notification.
        """
        if connected:
            self._disconnected.discard(antenna_id)
            event_type = enums.AntennaEventType.Antenna_Connected
        else:
            self._disconnected.add(antenna_id)
            event_type = enums.AntennaEventType.Antenna_Disconnected
        await self._send(
            messages.READER_EVENT_NOTIFICATION(
                reader_event_notification_data=params.ReaderEventNotificationData(
                    timestamp=params.UTCTimestamp(microseconds=self._now_us()),
                    antenna_event=params.AntennaEvent(event_type=event_type, antenna_id=antenna_id),
                )
            )
        )

    # -- connection handling -----------------------------------------------

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self._writer is not None:
            await self._send_connection_event(
                writer,
                enums.ConnectionAttemptStatusType.Failed_A_Client_Initiated_Connection_Already_Exists,
            )
            with contextlib.suppress(Exception):
                await writer.drain()
                writer.close()
            return
        self._writer = writer
        self._extensions_enabled = False
        self._rospecs = {}
        self._disconnected = set()
        self._focus_counts = {}
        await self._send_connection_event(writer, enums.ConnectionAttemptStatusType.Success)
        try:
            while True:
                header = await reader.readexactly(MESSAGE_HEADER_LEN)
                length = int.from_bytes(header[2:6], "big")
                if not MESSAGE_HEADER_LEN <= length <= _MAX_FRAME:
                    break
                body = b""
                if length > MESSAGE_HEADER_LEN:
                    body = await reader.readexactly(length - MESSAGE_HEADER_LEN)
                try:
                    msg = decode_message(header + body)
                except MessageDecodeError:
                    log.warning("emulator: undecodable frame, dropping connection")
                    break
                await self._handle_message(msg)
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            self._stop_reporting()
            self._stop_keepalive()
            self._writer = None
            with contextlib.suppress(Exception):
                writer.close()

    async def _send_connection_event(self, writer: asyncio.StreamWriter, status: int) -> None:
        event = messages.READER_EVENT_NOTIFICATION(
            reader_event_notification_data=params.ReaderEventNotificationData(
                timestamp=params.UTCTimestamp(microseconds=self._now_us()),
                connection_attempt_event=params.ConnectionAttemptEvent(status=status),
            )
        )
        writer.write(event.to_bytes(message_id=0))
        await writer.drain()

    async def _send(self, msg: LLRPMessage, *, message_id: int = 0) -> None:
        writer = self._writer
        if writer is None:
            return
        async with self._send_lock:
            try:
                writer.write(msg.to_bytes(message_id=message_id))
                await writer.drain()
            except (ConnectionError, OSError):  # client went away mid-send
                self._stop_reporting()

    @staticmethod
    def _now_us() -> int:
        return int(time.time() * 1_000_000)

    @staticmethod
    def _status_ok() -> params.LLRPStatus:
        return params.LLRPStatus(status_code=enums.StatusCode.M_Success)

    @staticmethod
    def _status_error(code: int, description: str) -> params.LLRPStatus:
        return params.LLRPStatus(status_code=code, error_description=description)

    # -- message handling --------------------------------------------------

    async def _handle_message(self, msg: LLRPMessage) -> None:
        if type(msg) in self._drop_once:
            self._drop_once.discard(type(msg))
            return
        mid = msg.message_id
        if isinstance(msg, messages.KEEPALIVE_ACK):
            self.keepalive_acks += 1
            self._keepalive_acked.set()
        elif isinstance(msg, messages.GET_READER_CAPABILITIES):
            await self._send(self._capabilities_response(), message_id=mid)
        elif isinstance(msg, impinj.IMPINJ_ENABLE_EXTENSIONS):
            self._extensions_enabled = True
            await self._send(
                impinj.IMPINJ_ENABLE_EXTENSIONS_RESPONSE(llrp_status=self._status_ok()),
                message_id=mid,
            )
        elif isinstance(msg, messages.SET_READER_CONFIG):
            if msg.keepalive_spec is not None:
                self._apply_keepalive_spec(msg.keepalive_spec)
            await self._send(
                messages.SET_READER_CONFIG_RESPONSE(llrp_status=self._status_ok()),
                message_id=mid,
            )
        elif isinstance(msg, messages.GET_READER_CONFIG):
            response = messages.GET_READER_CONFIG_RESPONSE(llrp_status=self._status_ok())
            if self._extensions_enabled and self._temperature_requested(msg):
                response.custom.append(
                    impinj.ImpinjReaderTemperature(temperature=round(self._temperature))
                )
            await self._send(response, message_id=mid)
        elif isinstance(msg, messages.ADD_ROSPEC):
            await self._send(
                messages.ADD_ROSPEC_RESPONSE(llrp_status=self._add_rospec(msg.ro_spec)),
                message_id=mid,
            )
        elif isinstance(msg, messages.ENABLE_ROSPEC):
            status = self._set_rospec_state(msg.ro_spec_id, "Disabled", "Enabled")
            await self._send(messages.ENABLE_ROSPEC_RESPONSE(llrp_status=status), message_id=mid)
        elif isinstance(msg, messages.DISABLE_ROSPEC):
            status = self._set_rospec_state(msg.ro_spec_id, "Enabled", "Disabled")
            await self._send(messages.DISABLE_ROSPEC_RESPONSE(llrp_status=status), message_id=mid)
        elif isinstance(msg, messages.START_ROSPEC):
            status = self._set_rospec_state(msg.ro_spec_id, "Enabled", "Active")
            self._focus_counts = {}  # fresh TagFocus suppression state per run
            self._sync_reporting()
            await self._send(messages.START_ROSPEC_RESPONSE(llrp_status=status), message_id=mid)
        elif isinstance(msg, messages.STOP_ROSPEC):
            status = self._set_rospec_state(msg.ro_spec_id, "Active", "Enabled")
            self._sync_reporting()
            await self._send(messages.STOP_ROSPEC_RESPONSE(llrp_status=status), message_id=mid)
        elif isinstance(msg, messages.DELETE_ROSPEC):
            if msg.ro_spec_id in self._rospecs:
                del self._rospecs[msg.ro_spec_id]
                status = self._status_ok()
            else:
                status = self._status_error(
                    enums.StatusCode.M_ParameterError, f"no ROSpec {msg.ro_spec_id}"
                )
            self._sync_reporting()
            await self._send(messages.DELETE_ROSPEC_RESPONSE(llrp_status=status), message_id=mid)
        elif isinstance(msg, messages.ADD_ACCESSSPEC):
            spec = msg.access_spec
            if spec.access_spec_id in self._accessspecs:
                status = self._status_error(
                    enums.StatusCode.M_DuplicateParameter,
                    f"AccessSpec {spec.access_spec_id} exists",
                )
            else:
                self._accessspecs[spec.access_spec_id] = (spec, False)
                status = self._status_ok()
            await self._send(messages.ADD_ACCESSSPEC_RESPONSE(llrp_status=status), message_id=mid)
        elif isinstance(msg, messages.ENABLE_ACCESSSPEC):
            for spec_id in list(self._accessspecs):
                if msg.access_spec_id in (0, spec_id):
                    self._accessspecs[spec_id] = (self._accessspecs[spec_id][0], True)
            await self._send(
                messages.ENABLE_ACCESSSPEC_RESPONSE(llrp_status=self._status_ok()),
                message_id=mid,
            )
        elif isinstance(msg, messages.DISABLE_ACCESSSPEC):
            for spec_id in list(self._accessspecs):
                if msg.access_spec_id in (0, spec_id):
                    self._accessspecs[spec_id] = (self._accessspecs[spec_id][0], False)
            await self._send(
                messages.DISABLE_ACCESSSPEC_RESPONSE(llrp_status=self._status_ok()),
                message_id=mid,
            )
        elif isinstance(msg, messages.DELETE_ACCESSSPEC):
            for spec_id in list(self._accessspecs):
                if msg.access_spec_id in (0, spec_id):
                    del self._accessspecs[spec_id]
                    self._access_counts.pop(spec_id, None)
            await self._send(
                messages.DELETE_ACCESSSPEC_RESPONSE(llrp_status=self._status_ok()),
                message_id=mid,
            )
        elif isinstance(msg, messages.GET_ACCESSSPECS):
            access_specs: list[params.AccessSpec] = []
            for access_spec, enabled in self._accessspecs.values():
                access_spec.current_state = enabled
                access_specs.append(access_spec)
            await self._send(
                messages.GET_ACCESSSPECS_RESPONSE(
                    llrp_status=self._status_ok(), access_specs=access_specs
                ),
                message_id=mid,
            )
        elif isinstance(msg, messages.GET_ROSPECS):
            ro_specs: list[params.ROSpec] = []
            for ro_spec, state in self._rospecs.values():
                ro_spec.current_state = getattr(enums.ROSpecState, state)
                ro_specs.append(ro_spec)
            await self._send(
                messages.GET_ROSPECS_RESPONSE(llrp_status=self._status_ok(), ro_specs=ro_specs),
                message_id=mid,
            )
        elif isinstance(msg, messages.CLOSE_CONNECTION):
            await self._send(
                messages.CLOSE_CONNECTION_RESPONSE(llrp_status=self._status_ok()),
                message_id=mid,
            )
            if self._writer is not None:
                with contextlib.suppress(Exception):
                    self._writer.close()
        else:
            await self._send(
                messages.ERROR_MESSAGE(
                    llrp_status=self._status_error(
                        enums.StatusCode.M_UnsupportedMessage,
                        f"emulator does not handle {type(msg).__name__}",
                    )
                ),
                message_id=mid,
            )

    def _temperature_requested(self, msg: messages.GET_READER_CONFIG) -> bool:
        if int(msg.requested_data) == int(enums.GetReaderConfigRequestedData.All):
            return True
        wanted = {
            int(impinj.ImpinjRequestedDataType.All_Configuration),
            int(impinj.ImpinjRequestedDataType.Impinj_Reader_Temperature),
        }
        return any(
            isinstance(p, impinj.ImpinjRequestedData) and int(p.requested_data) in wanted
            for p in msg.custom
        )

    def _apply_keepalive_spec(self, spec: params.KeepaliveSpec) -> None:
        self._stop_keepalive()
        if (
            int(spec.keepalive_trigger_type) == int(enums.KeepaliveTriggerType.Periodic)
            and spec.periodic_trigger_value > 0
        ):
            period_s = spec.periodic_trigger_value / 1000.0
            self._keepalive_task = asyncio.get_running_loop().create_task(
                self._keepalive_loop(period_s)
            )

    def _stop_keepalive(self) -> None:
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            self._keepalive_task = None

    async def _keepalive_loop(self, period_s: float) -> None:
        try:
            while True:
                await asyncio.sleep(period_s)
                await self._send(messages.KEEPALIVE(), message_id=98)
        except asyncio.CancelledError:
            raise

    def _add_rospec(self, rospec: params.ROSpec) -> params.LLRPStatus:
        if rospec.ro_spec_id in self._rospecs:
            return self._status_error(
                enums.StatusCode.M_DuplicateParameter, f"ROSpec {rospec.ro_spec_id} exists"
            )
        self._rospecs[rospec.ro_spec_id] = (rospec, "Disabled")
        return self._status_ok()

    def _set_rospec_state(self, ro_spec_id: int, expected: str, new: str) -> params.LLRPStatus:
        entry = self._rospecs.get(ro_spec_id)
        if entry is None:
            return self._status_error(enums.StatusCode.M_ParameterError, f"no ROSpec {ro_spec_id}")
        spec, state = entry
        if state != expected:
            return self._status_error(
                enums.StatusCode.M_ParameterError,
                f"ROSpec {ro_spec_id} is {state}, expected {expected}",
            )
        self._rospecs[ro_spec_id] = (spec, new)
        return self._status_ok()

    # -- capabilities ------------------------------------------------------

    def _capabilities_response(self) -> messages.GET_READER_CAPABILITIES_RESPONSE:
        gdc = params.GeneralDeviceCapabilities(
            max_number_of_antenna_supported=self.antenna_count,
            can_set_antenna_properties=False,
            has_utc_clock_capability=True,
            device_manufacturer_name=IMPINJ_PEN,
            model_name=self.model_number,
            reader_firmware_version=self.firmware,
            receive_sensitivity_table_entrys=[
                params.ReceiveSensitivityTableEntry(index=1, receive_sensitivity_value=0)
            ],
            gpio_capabilities=params.GPIOCapabilities(num_gp_is=4, num_gp_os=4),
            per_antenna_air_protocols=[
                params.PerAntennaAirProtocol(
                    antenna_id=i, protocol_id=bytes([enums.AirProtocols.EPCGlobalClass1Gen2])
                )
                for i in range(1, self.antenna_count + 1)
            ],
        )
        llrp_caps = params.LLRPCapabilities(
            can_do_rf_survey=False,
            can_report_buffer_fill_warning=True,
            supports_client_request_op_spec=False,
            can_do_tag_inventory_state_aware_singulation=False,
            supports_event_and_report_holding=True,
            max_num_priority_levels_supported=1,
            client_request_op_spec_timeout=0,
            max_num_ro_specs=4,
            max_num_specs_per_ro_spec=4,
            max_num_inventory_parameter_specs_per_ai_spec=4,
            max_num_access_specs=8,
            max_num_op_specs_per_access_spec=8,
        )
        # Power table: indices 1..21 covering 10.0 .. 30.0 dBm in 1 dB steps.
        powers = [
            params.TransmitPowerLevelTableEntry(index=i, transmit_power_value=1000 + (i - 1) * 100)
            for i in range(1, 22)
        ]
        # A plausible Impinj-flavored RF mode table: fixed modes plus AutoSet ids.
        mode_rows = [
            (0, 0, 640000),  # "max throughput"-ish: FM0
            (1, 1, 640000),  # Miller-2 hybrid
            (2, 2, 274000),  # dense reader M4
            (3, 3, 170600),  # dense reader M8
            (1002, 2, 274000),  # AutoSet family
            (1003, 2, 274000),
        ]
        mode_entries = [
            params.C1G2UHFRFModeTableEntry(
                mode_identifier=mode_id,
                dr_value=True,  # DR 64/3
                epchagtc_conformance=False,
                m_value=m,
                forward_link_modulation=0,
                spectral_mask_indicator=2,
                bdr_value=bdr,
                pie_value=1500,
                min_tari_value=6250,
                max_tari_value=25000,
                step_tari_value=1875,
            )
            for mode_id, m, bdr in mode_rows
        ]
        uhf = params.UHFBandCapabilities(
            transmit_power_level_table_entrys=powers,
            frequency_information=params.FrequencyInformation(
                hopping=True,
                frequency_hop_tables=[
                    params.FrequencyHopTable(
                        hop_table_id=1,
                        frequency=[902_750 + 500 * i for i in range(50)],
                    )
                ],
            ),
            air_protocol_uhfrf_mode_tables=[
                params.C1G2UHFRFModeTable(c1_g2_uhfrf_mode_table_entrys=mode_entries)
            ],
        )
        return messages.GET_READER_CAPABILITIES_RESPONSE(
            llrp_status=self._status_ok(),
            general_device_capabilities=gdc,
            llrp_capabilities=llrp_caps,
            regulatory_capabilities=params.RegulatoryCapabilities(
                country_code=840, communications_standard=1, uhf_band_capabilities=uhf
            ),
        )

    # -- tag reporting -----------------------------------------------------

    def _sync_reporting(self) -> None:
        active = any(state == "Active" for _, state in self._rospecs.values())
        task_alive = self._report_task is not None and not self._report_task.done()
        if active and not task_alive:
            self._report_task = asyncio.get_running_loop().create_task(self._report_loop())
        elif not active:
            self._stop_reporting()

    def _stop_reporting(self) -> None:
        if self._report_task is not None:
            self._report_task.cancel()
            self._report_task = None

    def _scan_profile(self) -> _ScanProfile:
        """What the active ROSpecs ask for, digested for the report loop.

        This is where tuning becomes visible behavior: the RF mode index
        scales the read rate (FM0-ish modes fast, Miller-8 slow), and
        TagFocus (search mode 3 in session 1) suppresses tags after their
        first few sightings — just like the real feature exists to do.
        """
        antennas: set[int] = set()
        content: set[str] = set()
        rate_factor = 1.0
        tagfocus = False
        filters: list[tuple[int, int, BitStr, bool]] = []
        power_dbm = 30.0
        for spec, state in self._rospecs.values():
            if state != "Active":
                continue
            for sp in spec.spec_parameters:
                if not isinstance(sp, params.AISpec):
                    continue
                ids = set(sp.antenna_ids)
                if 0 in ids or not ids:
                    antennas.update(range(1, self.antenna_count + 1))
                else:
                    antennas.update(ids)
                for inv_spec in sp.inventory_parameter_specs:
                    for cfg in inv_spec.antenna_configurations:
                        if cfg.rf_transmitter is not None and cfg.rf_transmitter.transmit_power:
                            # Table: index 1..21 -> 10.0..30.0 dBm.
                            power_dbm = 10.0 + (int(cfg.rf_transmitter.transmit_power) - 1)
                        for cmd in cfg.air_protocol_inventory_command_settings:
                            for flt in cmd.c1_g2_filters:
                                mask = flt.c1_g2_tag_inventory_mask
                                action = flt.c1_g2_tag_inventory_state_unaware_filter_action
                                include = True
                                if action is not None:
                                    include = int(action.action) in (
                                        int(enums.C1G2StateUnawareAction.Select_Unselect),
                                        int(enums.C1G2StateUnawareAction.Select_DoNothing),
                                        int(enums.C1G2StateUnawareAction.DoNothing_Select),
                                    )
                                filters.append(
                                    (int(mask.mb), int(mask.pointer), mask.tag_mask, include)
                                )
                            session = 1
                            if cmd.c1_g2_singulation_control is not None:
                                session = int(cmd.c1_g2_singulation_control.session)
                            if cmd.c1_g2_rf_control is not None:
                                rate_factor = _MODE_RATE_FACTORS.get(
                                    cmd.c1_g2_rf_control.mode_index, 1.0
                                )
                            for custom in cmd.custom:
                                if (
                                    isinstance(custom, impinj.ImpinjInventorySearchMode)
                                    and int(custom.inventory_search_mode) == 3
                                    and session == 1
                                ):
                                    tagfocus = True
            report = spec.ro_report_spec
            if report is not None and self._extensions_enabled:
                for custom_param in report.custom:
                    if isinstance(custom_param, impinj.ImpinjTagReportContentSelector):
                        if custom_param.impinj_enable_peak_rssi is not None:
                            content.add("rssi")
                        if custom_param.impinj_enable_rf_phase_angle is not None:
                            content.add("phase")
                        if custom_param.impinj_enable_rf_doppler_frequency is not None:
                            content.add("doppler")
                        if custom_param.impinj_enable_serialized_t_id is not None:
                            content.add("tid")
        antennas -= self._disconnected
        return _ScanProfile(antennas, content, rate_factor, tagfocus, tuple(filters), power_dbm)

    # -- tag memory and access operations ---------------------------------

    def _memory_for(self, epc: bytes) -> dict[int, bytearray]:
        """Lazily created Gen2 memory banks for the tag currently at ``epc``."""
        if epc not in self._memories:
            self._memories[epc] = {
                0: bytearray(8),  # kill password (words 0-1) + access password (2-3)
                2: bytearray(b"\xe2\x80\x11\x05" + epc[-8:]),  # matches reported TID
                3: bytearray(64),  # 32 words of user memory
            }
        return self._memories[epc]

    def _bank_bytes(self, epc: bytes, mb: int) -> bytes:
        if mb == 1:  # EPC bank: CRC word, PC word, then the EPC itself
            pc = (len(epc) // 2) << 11
            return b"\x00\x00" + pc.to_bytes(2, "big") + epc
        return bytes(self._memory_for(epc)[mb])

    def _access_password_of(self, epc: bytes) -> int:
        return int.from_bytes(self._memory_for(epc)[0][4:8], "big")

    def _kill_password_of(self, epc: bytes) -> int:
        return int.from_bytes(self._memory_for(epc)[0][0:4], "big")

    def _tagspec_matches(self, tagspec: params.C1G2TagSpec, epc: bytes) -> bool:
        for pattern in tagspec.c1_g2_target_tags:
            if pattern.tag_data.bit_len == 0:
                continue
            if int(pattern.mb) != 1:
                return False  # only EPC-bank targeting is modeled
            matched = _bits_equal(epc, int(pattern.pointer) - 0x20, pattern.tag_data)
            if matched != bool(pattern.match):
                return False
        return True

    def _replace_tag_epc(self, tag: EmulatedTag, new_epc: bytes) -> None:
        from dataclasses import replace

        index = self.tags.index(tag)
        self.tags[index] = replace(tag, epc=new_epc)
        if tag.epc in self._memories:
            self._memories[new_epc] = self._memories.pop(tag.epc)
        if tag.epc in self._locked_banks:
            self._locked_banks[new_epc] = self._locked_banks.pop(tag.epc)
        if tag.epc in self._focus_counts:
            self._focus_counts[new_epc] = self._focus_counts.pop(tag.epc)

    def _execute_access(self, tag: EmulatedTag, trd: params.TagReportData) -> None:
        """Run enabled AccessSpecs against the tag being reported."""
        for spec_id in list(self._accessspecs):
            spec, enabled = self._accessspecs[spec_id]
            if not enabled:
                continue
            if not self._tagspec_matches(spec.access_command.air_protocol_tag_spec, tag.epc):
                continue
            for op in spec.access_command.access_command_op_specs:
                result = self._run_access_op(op, tag)
                if result is not None:
                    trd.access_command_op_spec_results.append(result)
            trigger = spec.access_spec_stop_trigger
            if (
                int(trigger.access_spec_stop_trigger)
                == int(enums.AccessSpecStopTriggerType.Operation_Count)
                and trigger.operation_count_value > 0
            ):
                self._access_counts[spec_id] = self._access_counts.get(spec_id, 0) + 1
                if self._access_counts[spec_id] >= trigger.operation_count_value:
                    del self._accessspecs[spec_id]
                    self._access_counts.pop(spec_id, None)

    def _run_access_op(
        self, op: object, tag: EmulatedTag
    ) -> (
        params.C1G2ReadOpSpecResult
        | params.C1G2WriteOpSpecResult
        | params.C1G2KillOpSpecResult
        | params.C1G2LockOpSpecResult
        | params.C1G2BlockWriteOpSpecResult
        | None
    ):
        epc = tag.epc
        if isinstance(op, params.C1G2Read):
            if op.access_password != self._access_password_of(epc):
                return params.C1G2ReadOpSpecResult(
                    result=enums.C1G2ReadResultType.Nonspecific_Tag_Error,
                    op_spec_id=op.op_spec_id,
                )
            bank = self._bank_bytes(epc, int(op.mb))
            start = op.word_pointer * 2
            end = len(bank) if op.word_count == 0 else start + op.word_count * 2
            if start >= len(bank) or end > len(bank):
                return params.C1G2ReadOpSpecResult(
                    result=enums.C1G2ReadResultType.Nonspecific_Tag_Error,
                    op_spec_id=op.op_spec_id,
                )
            data = bank[start:end]
            words = [int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2)]
            return params.C1G2ReadOpSpecResult(
                result=enums.C1G2ReadResultType.Success,
                op_spec_id=op.op_spec_id,
                read_data=words,
            )
        if isinstance(op, (params.C1G2Write, params.C1G2BlockWrite)):
            result_cls = (
                params.C1G2WriteOpSpecResult
                if isinstance(op, params.C1G2Write)
                else params.C1G2BlockWriteOpSpecResult
            )
            if op.access_password != self._access_password_of(epc):
                return result_cls(
                    result=enums.C1G2WriteResultType.Nonspecific_Tag_Error,
                    op_spec_id=op.op_spec_id,
                )
            if int(op.mb) in self._locked_banks.get(epc, set()):
                return result_cls(
                    result=enums.C1G2WriteResultType.Tag_Memory_Locked_Error,
                    op_spec_id=op.op_spec_id,
                )
            payload = b"".join(w.to_bytes(2, "big") for w in op.write_data)
            if int(op.mb) == 1:
                offset = (op.word_pointer - 2) * 2  # EPC proper starts at word 2
                if offset < 0 or offset + len(payload) > len(epc):
                    return result_cls(
                        result=enums.C1G2WriteResultType.Tag_Memory_Overrun_Error,
                        op_spec_id=op.op_spec_id,
                    )
                new_epc = epc[:offset] + payload + epc[offset + len(payload) :]
                self._replace_tag_epc(tag, new_epc)
            else:
                bank_mem = self._memory_for(epc)[int(op.mb)]
                start = op.word_pointer * 2
                if start + len(payload) > len(bank_mem):
                    return result_cls(
                        result=enums.C1G2WriteResultType.Tag_Memory_Overrun_Error,
                        op_spec_id=op.op_spec_id,
                    )
                bank_mem[start : start + len(payload)] = payload
            return result_cls(
                result=enums.C1G2WriteResultType.Success,
                op_spec_id=op.op_spec_id,
                num_words_written=len(op.write_data),
            )
        if isinstance(op, params.C1G2Kill):
            if op.kill_password == 0:
                return params.C1G2KillOpSpecResult(
                    result=enums.C1G2KillResultType.Zero_Kill_Password_Error,
                    op_spec_id=op.op_spec_id,
                )
            if op.kill_password != self._kill_password_of(epc):
                return params.C1G2KillOpSpecResult(
                    result=enums.C1G2KillResultType.Nonspecific_Tag_Error,
                    op_spec_id=op.op_spec_id,
                )
            with contextlib.suppress(ValueError):
                self.tags.remove(tag)  # a killed tag is gone for good
            return params.C1G2KillOpSpecResult(
                result=enums.C1G2KillResultType.Success, op_spec_id=op.op_spec_id
            )
        if isinstance(op, params.C1G2Lock):
            return params.C1G2LockOpSpecResult(
                result=enums.C1G2LockResultType.Nonspecific_Reader_Error,  # not modeled
                op_spec_id=op.op_spec_id,
            )
        return None

    @staticmethod
    def _passes_filters(epc: bytes, filters: tuple[tuple[int, int, BitStr, bool], ...]) -> bool:
        """Apply C1G2 select filters; only EPC-bank (mb=1) filters are modeled."""
        for mb, pointer, pattern, include in filters:
            if mb != 1:
                continue
            matched = _bits_equal(epc, pointer - 0x20, pattern)
            if matched != include:
                return False
        return True

    async def _report_loop(self) -> None:
        try:
            while True:
                profile = self._scan_profile()
                # Transmit power scales throughput and decides whether weak
                # tags are energized at all (visibility rule below).
                power_scale = 0.55 + 0.45 * (profile.power_dbm - 10.0) / 20.0
                rate = max(1.0, self.reads_per_sec * profile.rate_factor * power_scale)
                await asyncio.sleep(1.0 / rate)
                antennas, content = profile.antennas, profile.content
                visible = [
                    t
                    for t in self.tags
                    if antennas.intersection(t.antennas)
                    and t.rssi_dbm >= -42.0 - profile.power_dbm
                    and self._passes_filters(t.epc, profile.filters)
                ]
                if not visible:
                    continue
                if profile.tagfocus:
                    # TagFocus: once a tag has answered a few times it stays
                    # suppressed (S1 flag held), so the field goes quiet apart
                    # from occasional persistence lapses.
                    fresh = [t for t in visible if self._focus_counts.get(t.epc, 0) < 3]
                    if fresh:
                        visible = fresh
                    elif self._rng.random() > 0.02:
                        continue
                tag = self._rng.choices(visible, weights=[t.weight for t in visible])[0]
                if profile.tagfocus:
                    self._focus_counts[tag.epc] = self._focus_counts.get(tag.epc, 0) + 1
                antenna = self._rng.choice(sorted(antennas.intersection(tag.antennas)))
                rssi = tag.rssi_dbm + self._rng.gauss(0.0, 1.5)
                now = self._now_us()
                trd = params.TagReportData(
                    epc_parameter=params.EPC_96(epc=tag.epc)
                    if len(tag.epc) == 12
                    else params.EPCData(epc=BitStr.from_bytes(tag.epc)),
                    antenna_id=params.AntennaID(antenna_id=antenna),
                    peak_rssi=params.PeakRSSI(peak_rssi=max(-128, min(127, round(rssi)))),
                    channel_index=params.ChannelIndex(channel_index=self._rng.randint(1, 50)),
                    first_seen_timestamp_utc=params.FirstSeenTimestampUTC(microseconds=now),
                    last_seen_timestamp_utc=params.LastSeenTimestampUTC(microseconds=now),
                    tag_seen_count=params.TagSeenCount(tag_count=1),
                )
                if "rssi" in content:
                    trd.custom.append(impinj.ImpinjPeakRSSI(rssi=round(rssi * 100)))
                if "phase" in content:
                    trd.custom.append(
                        impinj.ImpinjRFPhaseAngle(phase_angle=self._rng.randint(0, 4095))
                    )
                if "doppler" in content:
                    trd.custom.append(
                        impinj.ImpinjRFDopplerFrequency(
                            doppler_frequency=self._rng.randint(-320, 320)
                        )
                    )
                if "tid" in content:
                    tid = b"\xe2\x80\x11\x05" + tag.epc[-8:]
                    words = [int.from_bytes(tid[i : i + 2], "big") for i in range(0, len(tid), 2)]
                    trd.custom.append(impinj.ImpinjSerializedTID(t_id=words))
                self._execute_access(tag, trd)
                await self._send(messages.RO_ACCESS_REPORT(tag_report_datas=[trd]))
        except asyncio.CancelledError:
            raise
