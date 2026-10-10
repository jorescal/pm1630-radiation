PM1630 Radiation Monitoring — Real-Time Exposure POC — Assistant Instructions

*Originally drafted 2026-08-15 in the ONC HQ Lab project; updated 2026-08-22; rewritten 2026-09-26 (Pi verification/hardening, mobile-app redesign); updated 2026-10-10 (GCP backend + real-time dashboard work; repository/sync section added the same day). Paste this into the project's custom instructions, replacing the current text in full.*

**Project name:** `PM1630 Radiation Monitoring — Hospital X-Ray POC` (name kept; audience has broadened — see Context).

---

## Context

This project is a proof of concept for **real-time personal radiation exposure monitoring** using a BLE dosimeter. The target users are people who work with radiation-producing equipment — hospital X-ray departments and any organisation that uses X-ray for imaging or scanning (industrial radiography, security screening, etc.). The goal is to warn the person, and their supervisor / radiation safety officer (RSO), within seconds or minutes of an exposure — not in a dosimetry-badge report weeks after the fact.

**This is explicitly a proof of concept and architecture demo, not a validated safety system.** The decode pipeline was built by reverse-engineering the device vendor's Android app, not from an official API or a calibration certificate — see "Known limitations". The app is positioned as a **supplementary early-warning tool alongside the legal dosimeter of record (TLD/OSL badge)**, not a replacement. If this ever moves toward real deployment at a customer site, involve that organisation's radiation safety officer, biomedical/clinical engineering (where applicable), and IT security/compliance before anything touches their live network or informs a real safety decision.

The project originated inside the ONC HQ Lab homelab project and was split out 2026-08-15. The homelab remains the **lab test environment** (broker, Pi hardware, Meraki APs) but doesn't own this project's direction.

## Device

**Polimaster PM1630** — BLE dosimeter. Unit in use: BLE name `PM1630 #{{PM1630_SERIAL}}`, address `{{MAC_PM1630}}` (stable across weeks of reconnects — strong evidence of a fixed address). Notifies `Instrument_data` ~1/s. If other units or models are added, record their names/addresses and any protocol differences rather than assuming they match.

## Current state (as of 2026-10-10)

- **Protocol:** reverse-engineered from the Polismart Android app (`jadx`); GATT UUIDs from `PolismartGattAttributes.java`, byte layout from `PolismartProtocolPM1630.readStatusPacket()`. Decode spot-validated against the device LCD and the vendor's iOS RadFlash app.
- **Collector (fixed area-monitor mode): live on `pi-pm1630`** — Raspberry Pi Zero W, `labadmin@{{PI_PM1630_IP}}` (DHCP; `pi-pm1630.local` resolves). `pm1630_mqtt_publish.py` runs as systemd `pm1630-mqtt.service`; hardened 2026-09-26 (exits on BLE disconnect / 30 s without data so systemd restarts it, retained telemetry, LWT on `homelab/status/pm1630`, `dose_uSv` at 6 decimals). Health check 2026-10-07: running and connected; recovered by itself from three site power outages (Pi is on a surge suppressor, not a UPS); its journal is volatile (lost at reboot) — user chose not to change that.
- **Lab data plane:** homelab Mosquitto `{{LAB_BROKER_IP}}:1883` (plaintext + password/ACL), user `pm1630` (write-only), topic `homelab/telemetry/pm1630/data`.
- **GCP backend (built 2026-10-08)** in project `{{GCP_PROJECT_ID}}` (shared with the separate "Location and scanning" / OSL stack — PM1630 resources are additive and prefixed `pm1630-`, never modify OSL's): Firestore database `pm1630` (own rules), Pub/Sub `pm1630-readings` / `pm1630-alarms` / `pm1630-deadletter`, BigQuery dataset `pm1630` (`raw_batches`, `alarms`, view `readings`; full ~1 Hz history, no expiry), service account `pm1630-ingest` (least privilege), device token in Secret Manager, and Cloud Run **`pm1630-ingest` deployed and tested** (`/v1/batch`, `/v1/event`, `/health`). Minute summaries in Firestore expire after 30 days (TTL active).
- **Lab → GCP bridge:** `pm1630-bridge` service code written and tested locally; **not deployed** — waiting on a homelab LXC `gcp-bridge` (VLAN 30) and a read-only Mosquitto user `gcp-bridge`.
- **Tooling:** `gcloud` installed on the Mac (account `{{OWNER_EMAIL}}`, default project `{{GCP_PROJECT_ID}}`); Cloud Shell also used.
- **Measured caveat:** the device's cumulative dose is a float32 in Sv — at background it moves in lumps (~0.00006 µSv steps), so per-second counter deltas are unusable at background levels; 60 s windows are fine.
- **Meraki (lab):** Site A has 2× MR33 (BLE + scanning radio, max MR 30.x, End-of-Support Jul 21 2026) plus one MR18 (no role). `meraki_inventory.py` written, not yet run.
- Full technical detail lives in **`pm1630-radiation-sensor.md`** (technical source of truth); code in `pm1630-ingest/` and `pm1630-bridge/`. This file is scope, direction and working style.

## Roadmap (direction as of 2026-10-10, expect this to evolve)

- **Now — real-time dashboard on GCP** (prioritised by the user 2026-10-08, before the Meraki tests and the app). Phases: P0 verify GCP ✅ · P1a foundations ✅ · P1b ingest ✅ · **P2 homelab LXC + MQTT user (homelab-owned, pending)** · P3 deploy the bridge · P4 history (already flowing via the BigQuery subscription) · P5 private dashboard (Firebase Hosting + Auth — not yet enabled in the project) · P6 public page (cached endpoint, default `*.web.app` URL) · P7 later: Meraki "which MR33 hears the PM1630" view with a two-level off switch.
  - Refresh ≤ 60 s; the **backend is the source of truth** so the phone app stays thin.
  - **History:** keep everything at full resolution, lowest cost — one Pub/Sub message per 60 s batch, BigQuery partitioned by day, no expiry; no per-reading Firestore writes; dashboards read pre-aggregated data.
  - **Alarms:** stored in the database; every client (monitoring-center dashboard, each phone) receives them independently from there. Backend evaluates per-organisation thresholds (none configured yet) and treats "no data" as an alarm.
  - **Public site is hosted in GCP, not on the lab NGINX** (no inbound exposure of the lab, survives lab power outages).
- **After the dashboard (lab):** MR33 beacon-advertising + scanning coexistence test and the `meraki_inventory.py` run.
- **Mobile app (iOS + Android)** — design unchanged from 2026-09-26 (`pm1630-radiation-sensor.md` → Mobile App): phone as collector for a worn unit, local alarms on the phone, dose-rate / dose-increment / cumulative model, GPS globally + Site A/B MR33 beacon checkpoints, flights kept and labelled lower-bound, posts to the same `pm1630-ingest` API. Build order: BLE collector + local alarms → buffer → ingest → checkpoints → exposure map.
- **Homelab-owned follow-ups:** `gcp-bridge` LXC + MQTT user, TLS/8883, fixed IP for `pi-pm1630`, PRTG (check reading age), optional power-loss resilience, handoff-doc corrections.
- Keep this section current — full-text replacement whenever direction firms up.

## Known limitations (read before treating any output as authoritative)

- Decode derived from reverse-engineering a vendor app, not official documentation or a calibration certificate — **spot-validated, not formally verified**. Adequate for a POC/demo, not for a real safety or compliance decision.
- `mUnitFactor` confirmed only for the device's current unit setting (µSv/h, µSv). Re-verify against the screen if the unit setting changes.
- Dose-counter resolution is coarse at background (see Current state); flight readings are a lower bound.
- Lab telemetry path is still plaintext MQTT (password/ACL, no TLS yet); the lab → GCP leg is HTTPS with a bearer token. Not acceptable for any customer network without a security review.
- Lab Meraki APs are past End-of-Support — fine for a POC, never a basis for a deployment claim.
- No patient or clinical data is or should ever be involved — device/environmental telemetry plus, in the app, the wearer's own dose and location. Label every demo as a POC.

## My skill level

Intermediate. Comfortable with networking, Linux, Windows administration, Meraki dashboard, Proxmox. Don't need every concept explained from scratch — I can look up references you cite. Do explain unfamiliar protocol-level details, BLE/GATT quirks, mobile-platform constraints, and radiation-monitoring domain knowledge (dosimetry units, dose vs dose rate, calibration, regulatory limits) when they matter.

## How I work

- Give exact commands, not descriptions. Copy-paste-ready blocks.
- My Mac's shell is **zsh** (use `read "VAR?Prompt: "`, not bash `read -rp`, for commands run on the Mac; macOS `sed -i ''`); the Pi, Cloud Shell and LXCs are bash. Say explicitly **where** each command runs (Mac, Cloud Shell, Pi, LXC).
- GCP changes are additive only: prefix `pm1630-`, never edit OSL resources, least-privilege service accounts, verify state read-only before building.
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
- **Public dashboard:** aggregated, cached data only; never show MAC addresses, IPs, Meraki AP names/MACs, or anything that locates the lab/residence; permanent "proof of concept — not calibrated, not for safety decisions" banner.
- Secrets (device tokens, MQTT passwords) move machine-to-machine by pipe (Secret Manager → SSH → file), never displayed or pasted.

## Relationship to ONC HQ Lab (the homelab project)

- The homelab is the **lab test environment**: Mosquitto broker, Meraki APs, and the `pi-pm1630` hardware. Its docs (`mqtt-vm-operational.md`, `ip-vlan-plan.md`, `homelab_build_plan_v5.md`) keep only interface facts, with a pointer back here for device/protocol/app matters.
- **Boundary:** the homelab owns "the box" — Pi hardware, network placement (VLAN 30, `ONCIoT` SSID, DHCP/reservation), AD CS certs, broker config/ACLs, Meraki dashboard config. This project owns what runs on the Pi for this sensor (collector script, decode logic, systemd unit behaviour) and everything about the mobile app and its GCP backend. If docs disagree about who owns something, flag it rather than assuming.
- The mobile app's data plane is **independent of the homelab** (GCP). When the Pi/area-monitor path also moves off the homelab broker, update both projects: retire the interface note there, record the new architecture here.
- `pm1630-homelab-handoff.md` (2026-09-09) is the homelab's last handoff; known corrections are tracked in `pm1630-radiation-sensor.md` Open items.
- **Repository (added 2026-10-10):** this project is the section `sections/radiation-sensor/` of the ONC HQ Lab hub repo (`~/onc-hq-lab`, private), published on its own as the public repo `jorescal/pm1630-radiation` via `git subtree`, and linked here as a GitHub knowledge source. Paths in this project are relative to the section repo (`docs/pm1630-radiation-sensor.md`, `ingest/`, `bridge/`, `collector/`, `claude/project-instructions.md`). Follow the hub's `claude/SYNC-PROTOCOL.md`: exact repo paths, complete file contents, a `CHANGELOG.md` line, and a "**hub interface change**" flag whenever something affects the lab (broker users/ACLs, topics, the `gcp-bridge` LXC, IPs, certs).
- Section files are public: never put real IPs, MACs, serials, emails, GCP project IDs/numbers or Cloud Run URLs in them — use the `{{TOKENS}}` (e.g. `{{LAB_BROKER_IP}}`, `{{MAC_PM1630}}`, `{{GCP_PROJECT_ID}}`) defined in the hub's `private/sanitize-map.tsv`. The hub-side contract for this project is `docs/integrations/radiation-sensor.md` in the hub; when hub context is needed, attach it or link the hub repo.
