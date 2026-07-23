# Examples

Five small, heavily commented scripts in the repository's `examples/`
directory, each runnable with zero hardware — start an emulated reader first
(`llrpkit emulate --port 5084 &`) or point `--host` at a real Impinj reader in
LLRP mode.

**`read_tags.py`** is hello-world: connect, print capabilities, stream an
inventory with RSSI and phase for a few seconds.

**`tagfocus_dock_door.py`** demonstrates the dock-door pattern — TagFocus in
session 1 with serialized TID, printing one arrival event per unique tag and
showing how the report volume collapses compared to dual target.

**`mode_shootout.py`** automates the tuning methodology from the field guide:
it sweeps every mode in the reader's own RFModeTable for a few seconds each
and prints reads/sec and unique counts side by side.

**`antenna_watch.py`** wires the inventory stream and reader events into a
`HealthMonitor`, prints alerts as they fire (pull an antenna via the
dashboard's fault injection to see one), and dumps the per-port summary.

**`mqtt_bridge.py`** (needs `pip install "llrpkit[mqtt]"` and a broker such as
Mosquitto) publishes the tag stream to MQTT: one JSON message per read on
`{base}/tags`, plus a retained online/offline availability topic backed by an
MQTT Last Will — the reader stays in LLRP mode with full tuning control while
reads fan out to everything downstream. The same bridge is built into the
CLI: `llrpkit inventory <reader> --mqtt-broker <host>` — watch it with
`mosquitto_sub -t 'llrpkit/#' -v`.

Each script accepts `--host`, `--port`, and `--seconds`, and every one of them
is exercised against the emulator by the test suite, so they are guaranteed to
stay runnable as the library evolves.
