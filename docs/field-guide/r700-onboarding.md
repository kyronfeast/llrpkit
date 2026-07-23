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

## Troubleshooting the first hour

**Connection refused / timeout on 5084** — the reader is still in IoT mode;
switch interfaces as above. **"reader refused the connection: connection
already exists"** — LLRP allows one controlling client; close ItemTest, a
crashed service, or another llrpkit session first. **Everything connects but
Octane fields are missing** — you are talking to the reader through something
that skipped the extensions handshake; use `Reader`, not a raw client.
**Works, then dies after minutes of idle** — enable keepalives
(`await reader.set_keepalive(5000)`) so both ends notice a dead link.

A note on TLS: IANA assigns port 5085 for LLRP over TLS and llrpkit exposes
the constant, but the client's TLS support is still on the roadmap — put LLRP
on a management VLAN regardless (see `SECURITY.md`).

*Sources: Impinj R700 Installation and Operations Guide v8.1.7 (interface
exclusivity, Change Interface button, RShell command, default ports);
Impinj IoT Device Interface FAQ.*
