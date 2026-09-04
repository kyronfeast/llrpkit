# R700 onboarding: enabling LLRP

The Impinj R700 has two control planes, and out of the box it is probably not
speaking the one llrpkit uses. Five minutes of setup fixes that.

## The two interfaces

Modern R700 firmware offers the **Impinj IoT Device Interface** (a REST/MQTT
API) and classic **LLRP**. Per Impinj's R700 *Installation and Operations
Guide* (v8.1.7), the two are **mutually exclusive — the reader uses exactly
one at a time — and the IoT Device Interface is the factory default.** If
llrpkit's connection attempt times out with nothing on port 5084, this is
almost always why.

## Switching to LLRP

Either path works; both are from Impinj's official guide:

**Web UI.** Browse to the reader (`https://<reader-host>`), sign in, and on
the **Home** page find the reader-information panel. Its **Change Interface**
button toggles between the IoT Device Interface and LLRP; confirm the switch
to LLRP in the dialog.

**RShell** (SSH to the reader):

```text
> config rfid interface llrp
```

(`config rfid interface rest` switches back.) The LLRP inbound service on TCP
**5084** is enabled by default once LLRP is the active interface.

## First contact

```console
$ llrpkit capabilities <reader-host>
manufacturer      25882  (Impinj)
...
$ llrpkit inventory <reader-host> --search-mode tagfocus --phase --duration 10
```

llrpkit performs the `IMPINJ_ENABLE_EXTENSIONS` handshake automatically on
connect, so Octane features (TagFocus, sub-dBm RSSI, phase, TID) work
immediately. Remember the handshake is per-connection — if you build directly
on `LLRPClient`, re-enable after every reconnect (the `Reader` class handles
this for you).

## Antenna hubs: getting past four antennas

An R700 has four physical RP-TNC ports, but with **Impinj R702 antenna hubs**
one reader can address up to **32 antennas**. Each hub converts one reader port
into eight, and up to four hubs attach to one reader. This is common in larger
sites (dock doors, multi-zone lines) and it needs no special llrpkit code —
but it does need a one-time reader setting, and it changes how antennas are
numbered.

**Activate the hub feature first (or the extra antennas won't exist).** It's off
by default. Via RShell:

```text
> show feature anthub          # anthubStatus='Disabled' out of the box
> config feature enable anthub
> reboot
```

(The Web UI Home page has an equivalent antenna-hub toggle.) After the reboot
the reader reports the expanded antenna count in its capabilities, and because
llrpkit reads `max_number_of_antenna_supported` dynamically, `llrpkit
capabilities` will simply show up to 32 ports with no code change:

```console
$ llrpkit capabilities <reader-host>
...
antenna ports     32
$ llrpkit inventory <reader-host> --antennas 9,10,11,12   # a hub's worth
```

**Port numbering renumbers to 1–32 once any hub is attached.** With hubs, the
reader addresses antennas 1–32 as ordinary LLRP antenna IDs — hub multiplexing
is transparent to LLRP. A reader port *without* a hub takes the first number of
its 8-slot block (port 1 → antenna 1, port 2 → 9, port 3 → 17, port 4 → 25), so
plan your `--antennas` lists around those blocks. You can mix hubs and
direct-connected antennas on the same reader.

**Budget for hub loss.** A hub adds ~1.7 dB insertion loss — treat it like cable
loss when setting transmit power to stay within your region's limit.

To develop against a hub-sized reader with no hardware, the emulator takes an
antenna count: `LLRPEmulator(antenna_count=32)`.

## Troubleshooting the first hour

**Connection refused / timeout on 5084** — the reader is still in IoT mode;
switch interfaces as above. **"reader refused the connection: connection
already exists"** — LLRP allows one controlling client; close ItemTest, a
crashed service, or another llrpkit session first. If nothing is obviously
holding it, an admin can force the reader to drop the current LLRP client from
RShell: `config rfid llrp connclose` (returns *8-Permission-Denied* if a CAP,
not a client, owns the link). **Everything connects but
Octane fields are missing** — you are talking to the reader through something
that skipped the extensions handshake; use `Reader`, not a raw client.
**Works, then dies after minutes of idle** — enable keepalives
(`await reader.set_keepalive(5000)`) so both ends notice a dead link.

A note on TLS: the R700 itself *does* support LLRP over TLS on port **5085**
(RShell `config rfid llrp inbound tcp security encrypt`), and the reader can
also be configured to dial *out* to a controller — reader-initiated connections
(`config rfid llrp outbound add <host>`). llrpkit is a **client-initiated,
plaintext** LLRP client today: it connects *to* the reader on 5084, and its own
TLS and reader-initiated-listen support are still on the roadmap. Until then,
leave the reader's inbound interface on 5084 and put LLRP on a management VLAN
(see `SECURITY.md`).

*Sources: Impinj R700 RShell Reference Manual v10.3 (`config rfid interface
llrp`/`rest` with REST as the default; `config rfid llrp connclose`; inbound
TCP 5084/5085 security; reader-initiated outbound); Impinj R700 Installation
and Operations Guide (interface exclusivity, Change Interface button, default
ports); Impinj R700 Antenna Hub User Guide v10.3 (hub activation, 1–32 port
numbering, ~1.7 dB insertion loss).*
