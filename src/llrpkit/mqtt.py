"""Publish llrpkit tag streams to an MQTT broker (Mosquitto, EMQX, ...).

Requires the ``mqtt`` extra: ``pip install "llrpkit[mqtt]"``.

This is the bridge pattern for the R700: the reader stays in **LLRP mode**
(full ROSpec/RF-mode/TagFocus control through llrpkit) while tag data fans
out over MQTT to everything downstream — the same distribution you would get
from the reader's own IoT Device Interface, without giving up the LLRP
control plane. One :class:`MQTTBridge` publishes one reader's stream::

    from llrpkit import Reader
    from llrpkit.mqtt import MQTTBridge

    async with Reader("192.168.1.10") as reader:
        bridge = MQTTBridge("broker.local", base_topic="rfid/dock-door-1")
        await bridge.run(reader, search_mode=3, session=1)  # publishes until cancelled

Topics under ``base_topic``:

``{base}/tags``
    One JSON object per tag observation (see :func:`tag_payload`).
``{base}/status``
    Retained ``{"status": "online"|"offline", ...}``. The *offline* payload
    is registered as the MQTT Last Will, so the broker publishes it even if
    the bridge dies without saying goodbye — subscribers can always trust
    this topic for liveness (the same availability pattern Home Assistant
    and the Impinj IoT interface use).

Connection problems surface as :class:`aiomqtt.MqttError`; LLRP problems as
:class:`llrpkit.exceptions.LLRPError`. The bridge is cancellation-correct:
cancelling :meth:`MQTTBridge.run` tears down the inventory ROSpec and
publishes the retained *offline* status on the way out.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from llrpkit.inventory import TagReport
from llrpkit.reader import Reader

try:
    import aiomqtt
except ImportError as _exc:  # pragma: no cover - depends on install flavor
    raise ImportError(
        'llrpkit.mqtt needs the "mqtt" extra:\n\n    pip install "llrpkit[mqtt]"\n'
    ) from _exc

__all__ = ["MQTTBridge", "tag_payload"]


def _resurface_swallowed_cancel() -> None:
    """Re-raise a task cancellation that third-party code consumed.

    Python 3.11's ``asyncio.wait_for`` can catch an external ``Task.cancel()``
    that races the awaited future completing and return the value instead of
    re-raising (python/cpython#86296). llrpkit's own waits use
    ``asyncio.timeout`` and are immune, but aiomqtt acknowledges QoS>0
    publishes through ``wait_for`` — so at high tag rates a cancel aimed at
    the bridge can be eaten by a PUBACK arriving in the same event-loop tick.
    A swallowed cancel leaves ``Task.cancelling() > 0`` with nothing pending;
    checking after each third-party await turns "silently un-cancelled" back
    into prompt cancellation. Found by this bridge's own regression test —
    the same failure mode as QA-9 in QA_REPORT.md, one dependency down.
    """
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError


def tag_payload(tag: TagReport, reader: str) -> dict[str, Any]:
    """The JSON-ready shape published to ``{base}/tags`` for one observation.

    ``reader`` identifies the source (host:port or a user label) so multiple
    bridges can share one broker and consumers can still tell streams apart.
    ``at`` is wall-clock seconds since the Unix epoch at publish time.
    """
    return {
        "reader": reader,
        "epc": tag.epc_hex,
        "antenna": tag.antenna,
        "rssi_dbm": tag.rssi_dbm,
        "phase_deg": round(tag.phase_deg, 1) if tag.phase_deg is not None else None,
        "doppler_hz": tag.doppler_hz,
        "channel": tag.channel_index,
        "tid": tag.tid.hex() if tag.tid is not None else None,
        "at": round(time.time(), 3),
    }


@dataclass
class MQTTBridge:
    """Publish one reader's inventory stream to an MQTT broker.

    ``qos`` applies to tag messages (status messages always use QoS 1 —
    they are rare and must arrive). QoS 0 is the right default for
    high-rate tag streams: at hundreds of reads per second, per-message
    broker acknowledgements buy little and cost throughput.
    """

    broker_host: str
    broker_port: int = 1883
    base_topic: str = "llrpkit"
    username: str | None = None
    password: str | None = None
    qos: int = 0
    client_id: str | None = None
    #: Tag messages published so far (readable while :meth:`run` is active).
    published: int = field(default=0, init=False)

    @property
    def tags_topic(self) -> str:
        return f"{self.base_topic}/tags"

    @property
    def status_topic(self) -> str:
        return f"{self.base_topic}/status"

    def _status_payload(self, status: str, reader: str) -> str:
        return json.dumps(
            {
                "status": status,
                "reader": reader,
                "topic": self.tags_topic,
                "at": round(time.time(), 3),
            }
        )

    async def run(
        self,
        reader: Reader,
        *,
        reader_label: str | None = None,
        **inventory_kwargs: Any,
    ) -> int:
        """Stream ``reader.inventory(**inventory_kwargs)`` into the broker.

        Runs until the inventory ends (``duration``/``max_tags``) or the
        task is cancelled; either way the ROSpec is torn down and the
        retained *offline* status is published. Returns the number of tag
        messages published.
        """
        label = reader_label or f"{reader.client.host}:{reader.client.port}"
        will = aiomqtt.Will(
            self.status_topic, self._status_payload("offline", label), qos=1, retain=True
        )
        async with aiomqtt.Client(
            hostname=self.broker_host,
            port=self.broker_port,
            username=self.username,
            password=self.password,
            identifier=self.client_id,
            will=will,
        ) as client:
            await client.publish(
                self.status_topic, self._status_payload("online", label), qos=1, retain=True
            )
            _resurface_swallowed_cancel()
            stream = reader.inventory(**inventory_kwargs)
            try:
                async with contextlib.aclosing(stream):
                    async for tag in stream:
                        await client.publish(
                            self.tags_topic, json.dumps(tag_payload(tag, label)), qos=self.qos
                        )
                        self.published += 1
                        _resurface_swallowed_cancel()
            finally:
                # A graceful goodbye: the Last Will only fires when we vanish
                # without one. Bounded so a dead broker cannot stall teardown.
                with contextlib.suppress(Exception):
                    async with asyncio.timeout(2.0):
                        await client.publish(
                            self.status_topic,
                            self._status_payload("offline", label),
                            qos=1,
                            retain=True,
                        )
        return self.published
