"""High-level reader facade: connect, capabilities, and streaming inventory.

:class:`Reader` is the front door of llrpkit::

    async with Reader("192.168.1.10") as reader:
        print(reader.model_number, reader.firmware)
        async for tag in reader.inventory(session=1, duration=5.0):
            print(tag.epc_hex, tag.antenna, tag.rssi_dbm)

On connect it fetches and parses the reader's capabilities (antenna count,
transmit power table, RF mode table) and — when the reader is an Impinj —
performs the ``IMPINJ_ENABLE_EXTENSIONS`` handshake so Octane features are
available for the rest of the session.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llrpkit.modes import AnnotatedMode

from llrpkit.client import LLRPClient, check_status
from llrpkit.constants import IMPINJ_PEN, LLRP_PORT
from llrpkit.exceptions import CapabilityError, LLRPConnectionError, LLRPError
from llrpkit.inventory import DEFAULT_ROSPEC_ID, TagReport, build_rospec
from llrpkit.protocol import LLRPMessage, enums, impinj, messages, params


@dataclass(frozen=True)
class RFMode:
    """One entry of the reader's RF mode table (``C1G2UHFRFModeTableEntry``).

    ``mode_id`` is what goes into ``C1G2RFControl.mode_index`` — for Impinj
    readers these are the familiar identifiers (0-5 fixed modes, 1000+
    AutoSet families), and the set varies by model and region.
    """

    mode_id: int
    m_value: int
    bdr_value: int
    dr_value: int
    pie_value: int
    min_tari: int
    max_tari: int
    step_tari: int
    forward_link_modulation: int
    spectral_mask_indicator: int
    epc_hag_conformance: bool

    @classmethod
    def from_entry(cls, e: params.C1G2UHFRFModeTableEntry) -> RFMode:
        return cls(
            mode_id=e.mode_identifier,
            m_value=int(e.m_value),
            bdr_value=e.bdr_value,
            dr_value=int(e.dr_value),
            pie_value=e.pie_value,
            min_tari=e.min_tari_value,
            max_tari=e.max_tari_value,
            step_tari=e.step_tari_value,
            forward_link_modulation=int(e.forward_link_modulation),
            spectral_mask_indicator=int(e.spectral_mask_indicator),
            epc_hag_conformance=bool(e.epchagtc_conformance),
        )


class ReaderCapabilities:
    """Parsed view of ``GET_READER_CAPABILITIES_RESPONSE``.

    Everything llrpkit knows about a connected reader comes from here, not
    from hardcoded model assumptions: antenna count, the transmit power
    table (index -> dBm), the RF mode table, and frequency information.
    """

    def __init__(self, response: messages.GET_READER_CAPABILITIES_RESPONSE) -> None:
        self.raw = response
        self.manufacturer = 0
        self.model_number = 0
        self.firmware = ""
        self.max_antennas = 0
        gdc = response.general_device_capabilities
        if gdc is not None:
            self.manufacturer = int(gdc.device_manufacturer_name)
            self.model_number = int(gdc.model_name)
            self.firmware = gdc.reader_firmware_version
            self.max_antennas = int(gdc.max_number_of_antenna_supported)
        #: Transmit power table: index -> power in dBm.
        self.transmit_powers: dict[int, float] = {}
        #: RF mode table entries as reported by this reader.
        self.modes: list[RFMode] = []
        #: Fixed frequency list in kHz (empty for hopping regions).
        self.fixed_frequencies: list[int] = []
        self.hopping = False
        reg = response.regulatory_capabilities
        uhf = reg.uhf_band_capabilities if reg is not None else None
        if uhf is not None:
            for entry in uhf.transmit_power_level_table_entrys:
                self.transmit_powers[entry.index] = entry.transmit_power_value / 100.0
            for table in uhf.air_protocol_uhfrf_mode_tables:
                if isinstance(table, params.C1G2UHFRFModeTable):
                    self.modes.extend(
                        RFMode.from_entry(e) for e in table.c1_g2_uhfrf_mode_table_entrys
                    )
            info = uhf.frequency_information
            self.hopping = bool(info.hopping)
            if info.fixed_frequency_table is not None:
                self.fixed_frequencies = list(info.fixed_frequency_table.frequency)

    @property
    def is_impinj(self) -> bool:
        return self.manufacturer == IMPINJ_PEN

    def mode(self, mode_id: int) -> RFMode:
        for m in self.modes:
            if m.mode_id == mode_id:
                return m
        raise CapabilityError(f"reader does not report RF mode {mode_id}")

    def power_index_for_dbm(self, dbm: float) -> int:
        """The power-table index for the highest power not exceeding ``dbm``."""
        if not self.transmit_powers:
            raise CapabilityError("reader reported no transmit power table")
        eligible = [(v, i) for i, v in self.transmit_powers.items() if v <= dbm + 1e-9]
        if not eligible:
            lo = min(self.transmit_powers.values())
            raise CapabilityError(f"requested {dbm} dBm is below the reader minimum {lo} dBm")
        return max(eligible)[1]


class Reader:
    """High-level connection to one LLRP reader."""

    def __init__(
        self,
        host: str,
        port: int = LLRP_PORT,
        *,
        response_timeout: float = 5.0,
        connect_timeout: float = 10.0,
        enable_impinj_extensions: bool = True,
    ) -> None:
        self.client = LLRPClient(
            host, port, response_timeout=response_timeout, connect_timeout=connect_timeout
        )
        self._enable_impinj = enable_impinj_extensions
        self._capabilities: ReaderCapabilities | None = None
        self._impinj_enabled = False
        self._inventory_active = False

    # -- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        await self.client.connect()
        response = check_status(
            await self.client.transact(
                messages.GET_READER_CAPABILITIES(
                    requested_data=enums.GetReaderCapabilitiesRequestedData.All
                )
            )
        )
        assert isinstance(response, messages.GET_READER_CAPABILITIES_RESPONSE)
        self._capabilities = ReaderCapabilities(response)
        if self._enable_impinj and self._capabilities.is_impinj:
            check_status(await self.client.transact(impinj.IMPINJ_ENABLE_EXTENSIONS()))
            self._impinj_enabled = True

    async def close(self) -> None:
        await self.client.close()

    async def __aenter__(self) -> Reader:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # -- introspection -----------------------------------------------------

    @property
    def capabilities(self) -> ReaderCapabilities:
        if self._capabilities is None:
            raise LLRPError("reader is not connected")
        return self._capabilities

    @property
    def is_impinj(self) -> bool:
        return self.capabilities.is_impinj

    @property
    def impinj_extensions_enabled(self) -> bool:
        return self._impinj_enabled

    @property
    def model_number(self) -> int:
        return self.capabilities.model_number

    @property
    def firmware(self) -> str:
        return self.capabilities.firmware

    @property
    def max_antennas(self) -> int:
        return self.capabilities.max_antennas

    async def get_config(
        self,
        requested: int = enums.GetReaderConfigRequestedData.All,
    ) -> messages.GET_READER_CONFIG_RESPONSE:
        response = check_status(
            await self.client.transact(
                messages.GET_READER_CONFIG(
                    antenna_id=0, requested_data=requested, gpi_port_num=0, gpo_port_num=0
                )
            )
        )
        assert isinstance(response, messages.GET_READER_CONFIG_RESPONSE)
        return response

    def annotated_modes(self) -> list[AnnotatedMode]:
        """The reader's RF mode table joined with llrpkit's curated guidance."""
        from llrpkit.modes import annotate_modes  # runtime import: modes builds on reader

        return list(annotate_modes(self.capabilities.modes))

    async def set_keepalive(self, period_ms: int | None) -> None:
        """Ask the reader to send periodic ``KEEPALIVE``s; ``None`` disables.

        The client acknowledges them automatically, so enabling this gives
        both ends liveness detection for long-running sessions.
        """
        if period_ms is None:
            spec = params.KeepaliveSpec(
                keepalive_trigger_type=enums.KeepaliveTriggerType.Null,
                periodic_trigger_value=0,
            )
        else:
            spec = params.KeepaliveSpec(
                keepalive_trigger_type=enums.KeepaliveTriggerType.Periodic,
                periodic_trigger_value=period_ms,
            )
        check_status(
            await self.client.transact(
                messages.SET_READER_CONFIG(reset_to_factory_default=False, keepalive_spec=spec)
            )
        )

    async def get_temperature(self) -> float | None:
        """Reader temperature in °C via the Octane extension; None if unavailable."""
        if not self._impinj_enabled:
            return None
        request = messages.GET_READER_CONFIG(
            antenna_id=0,
            requested_data=enums.GetReaderConfigRequestedData.All,
            gpi_port_num=0,
            gpo_port_num=0,
        )
        request.custom.append(
            impinj.ImpinjRequestedData(
                requested_data=impinj.ImpinjRequestedDataType.Impinj_Reader_Temperature
            )
        )
        response = check_status(await self.client.transact(request))
        assert isinstance(response, messages.GET_READER_CONFIG_RESPONSE)
        for p in response.custom:
            if isinstance(p, impinj.ImpinjReaderTemperature):
                return float(p.temperature)
        return None

    async def events(self) -> AsyncGenerator[LLRPMessage, None]:
        """Unsolicited reader notifications (antenna events, exceptions, ...).

        Ends when the connection closes. Feed these to
        :class:`llrpkit.health.HealthMonitor.handle_event` for health tracking.
        """
        while True:
            try:
                # asyncio.timeout, not wait_for: on 3.11 wait_for can swallow a
                # concurrent Task.cancel() when an event is already queued,
                # leaving this generator (and its consumer) uncancellable.
                async with asyncio.timeout(0.25):
                    msg = await self.client.events.get()
            except TimeoutError:
                if not self.client.connected:
                    return
                continue
            yield msg

    # -- inventory ---------------------------------------------------------

    async def inventory(
        self,
        *,
        antennas: Sequence[int] = (),
        session: int = 1,
        search_mode: int | None = None,
        mode_index: int | None = None,
        tx_power_dbm: float | None = None,
        tag_population: int = 32,
        report_every_n: int = 1,
        include_phase: bool = False,
        include_doppler: bool = False,
        include_tid: bool = False,
        duration: float | None = None,
        max_tags: int | None = None,
        ro_spec_id: int = DEFAULT_ROSPEC_ID,
    ) -> AsyncGenerator[TagReport, None]:
        """Stream tag observations until ``duration``/``max_tags`` or ``break``.

        One inventory stream per reader at a time. The underlying ROSpec is
        created on entry and stopped and deleted on exit, however the stream
        ends. Impinj report content (sub-dBm RSSI, plus phase / Doppler /
        TID when requested) is enabled automatically when the Octane
        extensions handshake succeeded.
        """
        if self._inventory_active:
            raise LLRPError("an inventory stream is already active on this reader")
        power_index = (
            self.capabilities.power_index_for_dbm(tx_power_dbm)
            if tx_power_dbm is not None
            else None
        )
        rospec = build_rospec(
            ro_spec_id=ro_spec_id,
            antennas=antennas,
            session=session,
            search_mode=search_mode,
            mode_index=mode_index,
            transmit_power_index=power_index,
            tag_population=tag_population,
            report_every_n=report_every_n,
            enable_impinj_reports=self._impinj_enabled,
            include_phase=include_phase,
            include_doppler=include_doppler,
            include_tid=include_tid,
        )
        client = self.client
        self._inventory_active = True
        try:
            # A stale llrpkit ROSpec from a previous (crashed) session is fine to delete.
            with contextlib.suppress(LLRPError):
                await client.transact(messages.DELETE_ROSPEC(ro_spec_id=ro_spec_id))
            while not client.reports.empty():  # drop stale reports
                client.reports.get_nowait()
            check_status(await client.transact(messages.ADD_ROSPEC(ro_spec=rospec)))
            check_status(await client.transact(messages.ENABLE_ROSPEC(ro_spec_id=ro_spec_id)))
            check_status(await client.transact(messages.START_ROSPEC(ro_spec_id=ro_spec_id)))
            loop = asyncio.get_running_loop()
            deadline = loop.time() + duration if duration is not None else None
            yielded = 0
            while True:
                remaining = None if deadline is None else deadline - loop.time()
                if remaining is not None and remaining <= 0:
                    return
                tick = 0.25 if remaining is None else min(0.25, remaining)
                try:
                    # asyncio.timeout, not wait_for: with reports flowing, a
                    # stop that cancels this stream races the queue being hot;
                    # 3.11's wait_for would consume the cancellation and the
                    # stream would run forever (the pre-release QA hang).
                    async with asyncio.timeout(tick):
                        report = await client.reports.get()
                except TimeoutError:
                    if not client.connected:
                        raise LLRPConnectionError("connection lost during inventory") from None
                    continue
                for trd in report.tag_report_datas:
                    yield TagReport.from_param(trd)
                    yielded += 1
                    if max_tags is not None and yielded >= max_tags:
                        return
        finally:
            self._inventory_active = False
            if client.connected:
                with contextlib.suppress(LLRPError, OSError):
                    await client.transact(messages.STOP_ROSPEC(ro_spec_id=ro_spec_id), timeout=2.0)
                    await client.transact(
                        messages.DELETE_ROSPEC(ro_spec_id=ro_spec_id), timeout=2.0
                    )
