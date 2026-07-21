# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately via GitHub's **"Report a vulnerability"**
(Security → Advisories on this repository) rather than a public issue. You should receive an
acknowledgement within a few days. Coordinated disclosure is appreciated; credit is given in
release notes unless you prefer otherwise.

While llrpkit is pre-1.0, only the latest release receives security fixes.

## Deployment posture (please read)

Two facts about the protocol and tooling worth understanding before deploying anything built
with llrpkit:

**LLRP is an unauthenticated protocol.** Anyone who can reach TCP port 5084 on a reader can
control it. Readers belong on a management network or VLAN, not the open internet. The Impinj
R700 additionally supports LLRP over TLS (port 5085), which llrpkit will support; TLS encrypts
the channel but reader access control remains a network-design concern.

**The llrpkit dashboard binds to localhost by default.** Exposing it more widely is an explicit
opt-in flag, and the dashboard itself ships without authentication in early releases — put it
behind a reverse proxy with auth if you need remote access.
