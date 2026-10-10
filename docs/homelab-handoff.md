> Compiled from the ONC HQ Lab project, 2026-09-09 — everything already tested, configured, or planned on the homelab side that's relevant to the PM1630 radiation sensor project. This is an internal handoff document (real IPs/hostnames/MACs included), not a redacted portfolio doc — that's a separate, later step once you're ready to publish a PM1630 repo.

# PM1630 Radiation Monitoring — Homelab-Side Handoff

## 1. Why this document exists

The PM1630 radiation dosimeter integration was originally built and tested inside the ONC HQ Lab project, then split out on 2026-08-15 into its own Claude project — **PM1630 Radiation Monitoring — Hospital X-Ray POC** — once its purpose diverged into a hospital-facing proof of concept rather than a homelab component. Since that split, the ONC HQ Lab project's docs deliberately keep only the *interface facts* (MQTT user, topic names, broker location) — everything about the sensor itself (BLE protocol, decode formulas, scripts, verification history, mobile-app roadmap) lives in that other project.

This doc is the reverse handoff: everything the **homelab side** has since built, tested, and planned that the PM1630 project doesn't have visibility into, because it happened here after the split. Use it to bring that other project up to date, and as the factual base if/when you build a public GitHub repo for it (same redaction methodology as `onc-hq-lab` — see §7).

## 2. Timeline — what's actually been tested

| Date | Event |
|---|---|
| 2026-08-15 | PM1630 BLE GATT integration built and verified end-to-end on a Mac (`pm1630_mqtt_publish.py`, macOS CoreBluetooth via `bleak`). Protocol reverse-engineered via vendor Android APK decompilation (`jadx`); decode formula validated against two independent ground truths (the device's own screen, the vendor's iOS app). Published to `homelab/telemetry/pm1630/data` via new mosquitto user `pm1630`. Split into its own Claude project the same day, once purpose diverged (hospital POC vs. homelab component). |
| 2026-08-21 | Planned (not yet run at the time): PRTG monitoring of `homelab/telemetry/pm1630/data` at a 30s poll interval once Phase 5 (PRTG) lands. Also planned: a homelab-side test of whether the existing Meraki BLE telemetry (`homelab/telemetry/ble/meraki`) can passively observe the PM1630 as an ambient client — a candidate positioning source for the PM1630 project's planned mobile-app heat-map feature. |
| 2026-08-22 | Decision: retire the Mac collector entirely. The GATT connection moves to a dedicated Raspberry Pi Zero W, `pi-pm1630` (hostname `pi-pm1630.lab.local`, VLAN 30) — one of a 3-board Pi fleet decided this session (the others being `pi-arduino` and `pi-hvac`, unrelated Arduino sensor projects). Blocked at this point on a missing Wi-Fi SSID (the Zero W has no Ethernet). |
| 2026-08-24 | Confirmed: `pi-pm1630` needs **no new mosquitto account** — it reuses the existing `pm1630` credentials/topics unchanged (minimal build; the general BLE-gateway role some earlier drafts assumed was explicitly deferred to avoid BLE radio contention). |
| 2026-08-31 | Network prerequisite cleared: new Meraki SSID `ONCIoT` (VLAN 30, VLAN tagging enabled) created and verified end-to-end, plus the Aparta/Rancho AP uplink switch ports widened to allow VLAN 30. This was the last thing blocking the Pi build. |
| **2026-09-03** | **`pi-pm1630` built, live, and verified end-to-end.** See §3–4 for full detail. This is the current state as of this handoff. |

## 3. Current architecture (as of 2026-09-03)

- **Hardware:** Raspberry Pi Zero W (single-core ARM11/armv6, 512 MB RAM) — the Mac-based collector is fully retired.
- **OS:** Raspberry Pi OS (Legacy, 32-bit) Lite. Note: this image is actually **Bookworm**-based, not Bullseye as originally assumed on 2026-08-22 — Raspberry Pi Imager's naming shifted a generation. Bookworm *Lite* boots the original Zero W via a clean flash; Bookworm Desktop/Full does not.
- **Network:** joined the `ONCIoT` SSID (VLAN 30, WPA2 PSK), currently on **plain DHCP at `{{PI_PM1630_IP}}`** — no Meraki fixed-IP reservation yet (flagged, not yet requested — see §6).
- **Collector software:** `pm1630_mqtt_publish.py`, ported from macOS CoreBluetooth to Linux **BlueZ** (still using the `bleak` library — cross-platform API needed only minor changes). The actual protocol/decode logic in that script belongs to your PM1630 project's own documentation, not repeated here.
- **Deployment:** runs as a systemd service, `pm1630-mqtt.service`:
  - `Restart=always`, `RestartSec=15`
  - MQTT password sourced via `EnvironmentFile=~/homelab-secrets/pm1630-mqtt.env` (mode 600) — no TTY under systemd, so an interactive password prompt isn't an option
  - Runs `python3 -u` (unbuffered) so `journalctl` actually shows live output instead of buffering it
  - Self-healing auto-restart was demonstrated live in production — it recovered on its own after hitting a BLE timeout on first boot

## 4. MQTT interface (the homelab broker's side of the contract)

| Field | Value |
|---|---|
| Broker | `mqtt.lab.local` ({{LAB_BROKER_IP}}), Mosquitto 2.0.11 |
| Mosquitto user | `pm1630` — **write-only** by ACL design |
| Telemetry topic | `homelab/telemetry/pm1630/data` (write-only for `pm1630`) |
| Status/LWT topic | `homelab/status/pm1630` — added 2026-09-03, retained, `online`/`offline` |
| Listener in use today | 1883 (plaintext, password + ACL auth) |
| Listener planned | 8883 (TLS, `require_certificate true` / mTLS) — configured and listening, **not yet verified end-to-end**; blocked only on requesting a Client Auth leaf cert for `pi-pm1630.lab.local` from the lab's AD CS, not on anything else |

**ACL stanza (mqtt VM, `/etc/mosquitto/acl`), as currently deployed:**
```
# PM1630 radiation dosimeter - external project's test telemetry, published by pi-pm1630 (systemd, BlueZ bleak collector)
user pm1630
topic write homelab/telemetry/pm1630/#
topic write homelab/status/pm1630
```

**Important gotcha for verification/debugging:** the `pm1630` account is write-only and cannot subscribe to its own topics to self-verify. Every verification pass on the homelab side has used the `testuser` account (full `readwrite #`) to confirm data is actually landing — if your PM1630 project ever needs to independently confirm data is flowing, you'll need either a read-capable credential or to ask for a temporary grant, not `pm1630` itself.

## 5. Real gotchas hit building this (worth carrying into your PM1630 docs)

- **BlueZ pairing requirement:** unlike macOS CoreBluetooth, BlueZ requires the PM1630 be paired/trusted via `bluetoothctl` *before* `bleak` can hold a stable connection by MAC address alone. An unpaired/temporary BlueZ device object gets purged by the OS, especially at low RSSI or low device battery — this caused a genuinely flaky connection until traced to this cause. One stale D-Bus state also needed a reboot to clear after a Ctrl+C'd test run.
- **SD card corruption:** the Pi survived one SD card corruption during setup, requiring a re-flash — not a software bug, just a real-world reliability note for anyone reproducing this build.
- **Systemd + secrets:** systemd services have no TTY, so an interactive `getpass()`/password-prompt pattern (fine for the original Mac script) doesn't work — needed `EnvironmentFile=` instead.
- **Output buffering:** Python's stdout is block-buffered by default when not attached to a terminal, which silently hides log output under `journalctl` unless you run with `python3 -u`.

## 6. Outstanding / deferred items (homelab side)

1. **Meraki fixed-IP reservation for `pi-pm1630`** — currently plain DHCP at `{{PI_PM1630_IP}}`; not yet requested.
2. **8883 mTLS verification** — no longer blocked on the Pi itself (it's live); only remaining step is requesting the Client Auth leaf cert (`pi-pm1630.lab.local`) from AD CS and switching the collector from 1883 to 8883.
3. **PRTG monitoring of `homelab/status/pm1630`** — scoped (30s poll interval, freshness-check pattern already proven for other topics) but not yet built; blocked on Phase 5 (PRTG Core), which itself is deferred until a second RAM DIMM is replaced on the Proxmox host. The ACL grant for a future `prtg-core` account is already scaffolded (commented out) and will need no new stanza — just uncommenting.
4. **Homelab-side Meraki BLE positioning test (planned 2026-08-21, not yet run)** — whether the PM1630's BLE advertisement (independent of its GATT connection) is visible in the existing `homelab/telemetry/ble/meraki` telemetry as an ambient client, as a candidate passive-positioning signal for your mobile-app heat-map idea. This is a read-only `mosquitto_sub` capture against already-flowing data — no config change needed on either side. Three things it would answer: (a) does GATT-connecting to the PM1630 suppress its BLE advertising, (b) is its advertised MAC stable across sessions, (c) with only 2 Site-A APs, do they report meaningfully different RSSI for the same MAC (a rough directional-usability check, not a positioning-accuracy proof). This test hasn't run yet — say the word if you want it done before or alongside anything else.
5. **User decision on record (2026-09-03, verbatim):** *"I would like to keep this radiation sensor working and collecting data in my lab while I wait for the PM1630 project's. Let's fix everything here for now and move the project if required later."* — so `pi-pm1630` is a standing homelab component, not a one-time migration proof, until you say otherwise.

## 7. If/when you build a public GitHub repo for this project

The same redaction methodology used for `onc-hq-lab` (github.com/jorescal/onc-hq-lab) applies here if any of this ever gets published: RFC 5737/3849/2606 documentation-space placeholders for IPs/hostnames, explicit `[REDACTED-*]` markers for MACs/keys/serials — never a plausible-looking fake substituted for something identifying. That repo's build is currently on hold pending your go-ahead in *that* Claude project, per your own priority order (IoT sensors first, then PM1630, then Location and Scanning) — this handoff doc is what unblocks you to raise it there whenever you're ready, without losing any of the homelab-side context above.

## 8. Cross-reference — where this lives in ONC HQ Lab's own docs

- `homelab_build_plan_v5.md` §6.3b (full narrative + Change Log entries dated 2026-08-15, 08-22, 08-24, 08-31, 09-03)
- `mqtt-vm-operational.md` — PM1630 section (ACL, verification, deferred items)
- `ip-vlan-plan.md` — IoT Radio Settings subsection (Meraki BLE telemetry context) and VLAN 30 fixed-IP table (`pi-pm1630` row)

Nothing about the sensor's own protocol, decode formulas, or roadmap is in ONC HQ Lab's docs — that's all already in your PM1630 project, untouched by any of this.
