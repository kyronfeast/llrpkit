# Integrating llrpkit with downstream systems

This page is the contract sheet for anyone consuming llrpkit's output —
an ERP bridge, a data pipeline, a dashboard of your own. Everything here is
**pinned**: field names never change or disappear within a release line
(new fields may be added). Hand this page to the team on the other side of
the wire and they can build without guessing.

## Installing llrpkit on the host

llrpkit is not on PyPI yet. Until it is, install from the built wheel that
ships in the release archive (or build one with `python -m build`):

```console
$ pip install "llrpkit-0.1.0-py3-none-any.whl[mqtt]"       # broker deployments
$ pip install "llrpkit-0.1.0-py3-none-any.whl[webhook]"    # direct-POST deployments
```

Pin the version in your deployment docs. When llrpkit reaches PyPI the same
extras apply to `pip install "llrpkit[mqtt]"`.

## Choosing a transport

**MQTT** (`llrpkit inventory --mqtt-broker ...`) is right when more than one
consumer wants the stream, or you want the broker's buffering, retained
status, and Last Will liveness. **Webhooks** (`--webhook URL`) are right for
one consumer and no extra moving parts — llrpkit POSTs straight to your
HTTP endpoint. Both carry the same semantics: presence (arrive/depart)
events as the meaningful stream, raw reads as optional telemetry.

## MQTT topics and payloads (pinned)

Base topic defaults to `llrpkit/<reader-host>` (`--mqtt-topic` overrides).

`{base}/tags` — one JSON object per read (QoS from `--mqtt-qos`, default 0):

```json
{"reader": "127.0.0.1:5084", "epc": "e28011...", "antenna": 3,
 "rssi_dbm": -52.25, "phase_deg": 123.5, "doppler_hz": -12.5,
 "channel": 7, "tid": "e2801105...", "at": 1770950400.123}
```

`{base}/events` — with `--mqtt-events`: arrive/depart edges (always QoS 1):

```json
{"event": "arrived",            // or "departed"
 "reader": "dock-door-1",
 "epc": "e28011...",
 "antenna": 3,                  // null if unknown
 "dwell_s": null,               // departures: seconds first→last sighting
 "reads": 4,                    // reads during the visit
 "at": 1770950400.123}
```

`{base}/status` — retained availability, registered as the MQTT Last Will,
so the broker itself announces an ungraceful death:

```json
{"status": "online", "reader": "dock-door-1",
 "topic": "llrpkit/dock-door-1/tags", "at": 1770950400.123}
```

## Webhook contract (pinned)

With `llrpkit inventory --webhook URL --webhook-token TOKEN` (or
`llrpkit.webhook.WebhookSink`), llrpkit POSTs batched JSON:

```json
{"reader": "dock-door-1",
 "token": "TOKEN",
 "events": [
   {"epc": "e28011...", "kind": "arrived", "antenna": 3,
    "rssi": null, "dwell_s": null, "reads": 4, "at": 1770950400.123}
 ]}
```

Rules of the contract: the token travels **in the body**; every entry
carries the same keys with `null` where not applicable, and `epc` is the
only key a receiver must rely on; `kind` is `arrived`, `departed`, or
(with `--webhook-tags`) `read` — `read` entries carry `rssi`; batches hold
at most 500 entries, flushed at least once per second while entries are
pending. Expected responses: `200` (`{"ok": true, "created": N}`); `403`
stops the sink with a token error; `400` stops it as a malformed-body bug;
connection failures and 5xx are retried on the next flush from a bounded,
drop-oldest backlog.

Field-name note: the MQTT events schema says `"event"`, the webhook entry
says `"kind"`. Both predate each other's consumers and both are pinned
as-is — map accordingly.

## The zero-hardware proof chain

Every integration above can be proven end to end with no reader present:

```console
$ llrpkit emulate --port 5084 &
# MQTT path (broker first):
$ mosquitto & \
  llrpkit inventory 127.0.0.1 --search-mode tagfocus --mqtt-broker 127.0.0.1 --mqtt-events
# Webhook path (your receiver first):
$ llrpkit inventory 127.0.0.1 --search-mode tagfocus \
    --webhook http://erp.local:8069/your/endpoint --webhook-token TOKEN
```

llrpkit's own test suite runs both paths against a real Mosquitto broker
and a real HTTP receiver implementing the contract above, including token
rejection, batching, outage buffering, and cancellation cleanup — so the
contracts on this page are enforced by CI, not just documented.
