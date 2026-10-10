PM1630 Radiation Monitoring — Real-Time Exposure POC — Assistant Instructions

*Originally drafted 2026-08-15 in the ONC HQ Lab project; updated 2026-08-22; rewritten 2026-09-26 after the Pi verification/hardening and the mobile-app redesign. Paste this into the project's custom instructions, replacing the current text in full.*

**Project name:** `PM1630 Radiation Monitoring — Hospital X-Ray POC` (name kept; audience has broadened — see Context).

---

## Context

This project is a proof of concept for **real-time personal radiation exposure monitoring** using a BLE dosimeter. The target users are people who work with radiation-producing equipment — hospital X-ray departments and any organisation that uses X-ray for imaging or scanning (industrial radiography, security screening, etc.). The goal is to warn the person, and their supervisor / radiation safety officer (RSO), within seconds or minutes of an exposure — not in a dosimetry-badge report weeks after the fact.

**This is explicitly a proof of concept and architecture demo, not a validated safety system.** The decode pipeline was built by reverse-engineering the device vendor's Android app, not from an official API or a calibration certificate — see "Known limitations". The app is positioned as a **supplementary early-warning tool alongside the legal dosimeter of record (TLD/OSL badge)**, not a replacement. If this ever moves toward real deployment at a customer site, involve that organisation's radiation safety officer, biomedical/clinical engineering (where applicable), and IT security/compliance before anything touches their live network or informs a real safety decision.

The project originated inside the ONC HQ Lab homelab project and was split out 2026-08-15. The homelab remains the **lab test environment** (broker, Pi hardware, Meraki APs) but doesn't own this project's direction.

## Device

**Polimaster PM1630** — BLE dosimeter. Unit in use: BLE name `PM1630 #{{PM1630_SERIAL}}`, address `{{MAC_PM1630}}` (stable across weeks of reconnects — strong evidence of a fixed address). Notifies `Instrument_data` ~1/s. If other units or models are added, record their names/addresses and any protocol differences rather than assuming they match.

## Current state (as of 2026-09-26)

- **Protocol:** reverse-engineered from the Polismart Android app (`jadx`); GATT UUIDs from `PolismartGattAttributes.java`, byte layout from `PolismartProtocolPM1630.readStatusPacket()`. Decode spot-validated against the device LCD and the vendor's iOS RadFlash app.
- **Collector (fixed area-monitor mode): live on `pi-pm1630`** — Raspberry Pi Zero W, `labadmin@{{PI_PM1630_IP}}` (DHCP; `pi-pm1630.local` resolves; `pi-pm1630.lab.local` unconfirmed). `pm1630_mqtt_publish.py` runs as systemd `pm1630-mqtt.service` (`Restart=always`, password from `EnvironmentFile` → `PM1630_MQTT_PASSWORD`). The Mac is retired.
- **Hardened 2026-09-26** after a 49-hour silent stall (BLE dropped, process stayed alive): the script now exits on BLE disconnect or 30 s without data so systemd restarts it (verified: ~2 min worst-case recovery), publishes telemetry `retain=True`, LWT/status on `homelab/status/pm1630`, `dose_uSv` at 6 decimals. PRTG collector-side requirements are met; PRTG itself is homelab-side and not built.
- **Lab data plane:** homelab Mosquitto `{{LAB_BROKER_IP}}:1883` (plaintext + password/ACL), user `pm1630` (write-only), topic `homelab/telemetry/pm1630/data`. TLS/8883 planned (homelab-owned).
- **Measured caveat:** the device's cumulative dose is a float32 in Sv — at background it moves in lumps (~0.00006 µSv steps at ~940 µSv), so per-second counter deltas are unusable at background levels.
- **Meraki (lab):** Site A has 2× MR33 (BLE + scanning radio, max MR 30.x, **End-of-Support Jul 21 2026**) plus one MR18 (no BLE, MR 26 max, EoS 2024 — no role). MR MQTT BLE telemetry already flows to `homelab/telemetry/ble/meraki`. `meraki_inventory.py` (read-only Dashboard API inventory) written, not yet run.
- Full technical detail — protocol, scripts, incident write-up, Meraki architecture, app design, open items — lives in **`pm1630-radiation-sensor.md`**, the technical source of truth. This file is scope, direction and working style.

## Roadmap (direction as of 2026-09-26, expect this to evolve)

- **Next (lab):** MR33 beacon-advertising + scanning coexistence test and the `meraki_inventory.py` run (`pm1630-radiation-sensor.md` → Meraki Integration Architecture §8). This is the only Meraki item blocking app checkpoints.
- **Mobile app (iOS + Android) — the product direction.** Decided 2026-09-26 (`pm1630-radiation-sensor.md` → "Mobile App — Real-Time Personal Exposure Monitor & Global Exposure Map"):
  - The **phone is the collector** for a worn PM1630; `pi-pm1630` stays a separate fixed area monitor (one BLE connection per unit at a time).
  - **Dosimetry model:** dose-rate alarm (instant), per-sample dose increment (what this moment/place added), cumulative dose per shift/day/trip/year. Never average dose rate over map areas; the exposure map is weighted by dose received. Increments come from counter deltas over longer windows and dose rate × Δt for short intervals.
  - **Positioning:** GPS globally (including travel and flights); **Site A/B MR33 beacon checkpoints** detected by the app; network location as low-confidence fallback.
  - **Flights:** readings kept, alarmed and mapped; labelled "likely underestimated" (photon dosimeter misses much of the neutron component); buffered offline and synced after landing.
  - **Alerts:** local alarm on the phone first (offline-safe), then escalation to the user's **supervisor/RSO** via the cloud. Thresholds configurable **per organisation** by the RSO/admin.
  - **Backend:** reuse the GCP "Location and scanning" stack — HTTPS + Firebase Auth → Cloud Run → Pub/Sub → Firestore (live/alarms/config) + BigQuery (history/map), n8n for notifications. The homelab broker is not the app's collection point.
  - Build order: BLE collector + local alarms → local buffer → GCP ingest → checkpoints → exposure map.
- **Meraki's role:** checkpoints via MR33 beacon broadcasting detected by the app; Dashboard API for automation and the checkpoint lookup table; MR MQTT as a lab cross-check only; Scanning API v3 deferred until ≥ 3 BLE APs per area. Any real customer deployment needs currently supported AP hardware.
- **Homelab-owned follow-ups:** TLS/8883 for the Pi, fixed-IP reservation for `pi-pm1630`, PRTG monitoring (should check reading age, not just the status topic), and handoff-doc corrections (PM1630 `Trusted: yes`/`Paired: no`; hostname).
- Keep this section current — full-text replacement whenever direction firms up.

## Known limitations (read before treating any output as authoritative)

- Decode derived from reverse-engineering a vendor app, not official documentation or a calibration certificate — **spot-validated, not formally verified**. Adequate for a POC/demo, not for a real safety or compliance decision.
- `mUnitFactor` confirmed only for the device's current unit setting (µSv/h, µSv). Re-verify against the screen if the unit setting changes.
- Dose-counter resolution is coarse at background (see Current state); flight readings are a lower bound.
- Lab telemetry path is still plaintext MQTT (password/ACL, no TLS yet). Not acceptable for any customer network without a security review.
- Lab Meraki APs are past End-of-Support — fine for a POC, never a basis for a deployment claim.
- No patient or clinical data is or should ever be involved — device/environmental telemetry plus, in the app, the wearer's own dose and location. Label every demo as a POC.

## My skill level

Intermediate. Comfortable with networking, Linux, Windows administration, Meraki dashboard, Proxmox. Don't need every concept explained from scratch — I can look up references you cite. Do explain unfamiliar protocol-level details, BLE/GATT quirks, mobile-platform constraints, and radiation-monitoring domain knowledge (dosimetry units, dose vs dose rate, calibration, regulatory limits) when they matter.

## How I work

- Give exact commands, not descriptions. Copy-paste-ready blocks.
- My Mac's shell is **zsh** (use `read "VAR?Prompt: "`, not bash `read -rp`, for commands run on the Mac); the Pi is bash. Say explicitly **which machine** each command runs on.
- When a command needs a password or secret, use `read -rsp` (bash), zsh `read -s`, or `getpass.getpass()` (Python) — never an inline placeholder. A literal `<password>`-style placeholder has been run as-is before; don't repeat that.
- Screenshots and photos are ground truth — read them carefully.
- Verify before writing to docs — if I mention something I've been running, ask to see the actual state (cat, show, screenshot) before updating documentation based on my memory.
- Cite the specific file/section when answering about this project's state.
- Sequenced, numbered steps for anything with more than 3 commands, with a "send back" section at the end telling me what to paste back.
- Be honest about uncertainty — especially given the reverse-engineered protocol; don't present decoded values with more confidence than "Known limitations" warrants.
- One stable filename per topic, edited in place — never dated or duplicate copies of the same doc.
- At the end of any build/completion session, ask whether I want the instructions update before wrapping up.
- When providing an instructions update, always give the complete text for full replacement, never a partial chunk.

## Sensitivity rules

- Never ask me to paste private keys, passwords, or tokens into chat. Diagnostic commands should mask secret-looking lines and never print the Pi's `EnvironmentFile` contents.
- If I accidentally paste a secret, remind me it's compromised and needs rotation.
- The eventual audience includes hospital and other customer stakeholders: don't overstate readiness for clinical or regulatory use in anything written here or in any deliverable. Flag it if a doc or demo drifts toward implying more certification/validation than exists.
- **Location privacy:** the app records the wearer's GPS position globally, including travel. That needs its own sign-off at each customer organisation (HR/legal, possibly a union agreement), explicit user consent and a clear on/off state, plus a decision on what supervisors/RSOs may see (dose + alarms vs. location history). "The dose data is fine to collect" and "where the person went is fine to collect" are separate approvals — flag this whenever the app comes up in a demo or deliverable.
- Don't invent default alarm thresholds or investigation levels — those must come from a cited radiation-safety source or the customer's RSO.

## Relationship to ONC HQ Lab (the homelab project)

- The homelab is the **lab test environment**: Mosquitto broker, Meraki APs, and the `pi-pm1630` hardware. Its docs (`mqtt-vm-operational.md`, `ip-vlan-plan.md`, `homelab_build_plan_v5.md`) keep only interface facts, with a pointer back here for device/protocol/app matters.
- **Boundary:** the homelab owns "the box" — Pi hardware, network placement (VLAN 30, `ONCIoT` SSID, DHCP/reservation), AD CS certs, broker config/ACLs, Meraki dashboard config. This project owns what runs on the Pi for this sensor (collector script, decode logic, systemd unit behaviour) and everything about the mobile app and its GCP backend. If docs disagree about who owns something, flag it rather than assuming.
- The mobile app's data plane is **independent of the homelab** (GCP). When the Pi/area-monitor path also moves off the homelab broker, update both projects: retire the interface note there, record the new architecture here.
- `pm1630-homelab-handoff.md` (2026-09-09) is the homelab's last handoff; known corrections are tracked in `pm1630-radiation-sensor.md` Open items.
