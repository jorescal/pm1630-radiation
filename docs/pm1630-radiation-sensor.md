> Last updated: 2026-09-26
> Split out of ONC HQ Lab (homelab project) on 2026-08-15 — see that project's `mqtt-vm-operational.md`, `ip-vlan-plan.md`, and `homelab_build_plan_v5.md` for the interface-only pointer left behind there, and this project's own instructions doc for scope/roadmap/working style.

# PM1630 Radiation Sensor — Protocol, Pipeline & Verification

> Status: ✅ Decode protocol reverse-engineered and verified. ✅ MQTT publish pipeline built and verified end-to-end (2026-08-15), currently sinking into the ONC HQ Lab homelab's mqtt broker as a test environment. ✅ Pi collector (`pi-pm1630`) live since 2026-09-03, verified and hardened 2026-09-26 (stall watchdog, retained telemetry, LWT). ✅ PRTG collector-side requirements (retain/LWT/persistent connection) met 2026-09-26 — PRTG itself not built yet (homelab). 🔄 Mobile app redesigned 2026-09-26 as a real-time personal exposure monitor with a global GPS exposure map (iOS + Android, phone as collector, local alarms + supervisor/RSO escalation, GCP backend) — see Mobile App section; not yet built. 🆕 Meraki integration architecture (MR33/MR18 capabilities, MQTT streaming vs Scanning API vs Dashboard API, automation endpoint map, verification plan) designed 2026-09-26 — see "Meraki Integration Architecture" section; not yet built or verified.
>
> ✅ 2026-09-26: `pi-pm1630` state verified directly on the Pi (unit file, script, journal, BlueZ). Collector hardened the same day after a 49-hour silent stall — see Scripts → Incident. Scripts, Data plane and Roadmap sections now reflect the Pi, not the retired Mac.

---

## Device

**Polimaster PM1630** — handheld radiation dosimeter, BLE-capable.

- Observed BLE advertised name: `PM1630 #{{PM1630_SERIAL}}`
- Standard BLE Device Information service present (`0000180a-0000-1000-8000-00805f9b34fb`) — manufacturer, model, serial, firmware/hardware revision all readable via standard characteristics.
- Custom Polimaster data service and characteristics use a distinctive convention: the 128-bit UUID bytes decode as readable ASCII text when interpreted as a string. This makes the GATT layout self-documenting once you know to look:

| UUID | Decodes as (ASCII) | Purpose |
|---|---|---|
| `23506f6c-696d-6173-7465-725f55554944` | `#Polimaster_UUID` | Data service |
| `23496e73-7472-756d-656e-745f73657474` | `#Instrument_sett` | Device settings characteristic |
| `23496e73-7472-756d-656e-745f64617461` | `#Instrument_data` | **Device status characteristic — the one this pipeline reads** |
| `23496e73-7472-756d-656e-745f73706563` | `#Instrument_spec` | Spectrum characteristic (not yet used) |
| `23496e73-7472-756d-656e-745f68697374` | `#Instrument_hist` | Device history characteristic (not yet used) |
| `00002902-0000-1000-8000-00805f9b34fb` | (standard) | Client Characteristic Configuration descriptor (enables notify) |

Source: `PolismartGattAttributes.java`, decompiled from the vendor's official Android app (see Protocol reverse-engineering below).

## Protocol reverse-engineering

**Initial approach (superseded):** correlating captured BLE `Instrument_data` notify payloads against photos/video of the device's own on-screen display, across three separate capture sessions. This proved inconclusive — captured sessions either showed a flat/unchanging on-screen value, or (in one case using the vendor's iOS "RadFlash" app as the reference) didn't time-overlap with the script's own BLE connection window, since only one BLE connection can hold the device at a time.

**Working approach:** the user proposed decompiling the vendor's official Android app (`Polismart_5.1.140_apkcombo.com.xapk`) instead of guessing from traffic. Steps used:

1. Downloaded `jadx` (v1.5.1) from its GitHub releases.
2. Unzipped the `.xapk` to find the base `.apk`.
3. Decompiled with `jadx` to readable Java source.
4. Grepped the decompiled source for UUID/protocol-related strings to locate the authoritative parsing logic.

This gave two key files:

**`PolismartGattAttributes.java`** — the GATT UUID table above.

**`PolismartProtocolPM1630.java`** — `readStatusPacket(ByteBuffer, DatablockDB)`, the authoritative parser for the `Instrument_data` payload:

```java
protected boolean readStatusPacket(ByteBuffer byteBuffer, DatablockDB datablockDB) {
    byte b = byteBuffer.get(3);
    float f = byteBuffer.getFloat(4);
    long j = byteBuffer.getInt(8) & 4294967295L;
    float f2 = byteBuffer.getFloat(12);
    byte b2 = byteBuffer.get(16);
    byte b3 = byteBuffer.get(17);
    datablockDB.setDose(f * this.mUnitFactor);
    datablockDB.setDoseTime(j);
    datablockDB.setDoseRate(f2 * this.mUnitFactor);
    datablockDB.setBatteryLevel(b3);
    datablockDB.setDRStatistics(b2);
    datablockDB.setFlags(b & 255);
    ...
}
```

**`BasePolismartProtocol.java`** confirmed two more essential details: the payload is wrapped `ByteBuffer.wrap(value).order(ByteOrder.LITTLE_ENDIAN)` before `readStatusPacket` runs, and `value[0]` is a CRC8 checksum, validated via `getCRC8(value, value.length) == value[0]` before the rest of the payload is trusted.

### Byte layout (`Instrument_data`, little-endian)

| Offset | Type | Field | Notes |
|---|---|---|---|
| 0 | uint8 | CRC8 checksum | Covers the rest of the payload; validate before trusting the decode |
| 3 | uint8 (bitfield) | `flags` | |
| 4 | float32 | `dose` | × `mUnitFactor` → µSv (empirically confirmed for this device's current unit setting) |
| 8 | uint32 | `doseTime` | Seconds |
| 12 | float32 | `doseRate` | × `mUnitFactor` → µSv/h (same caveat) |
| 16 | uint8 | `drStatistics` | Percent |
| 17 | uint8 | `batteryLevel` | Percent |

**Open question / known limitation:** the exact value and behavior of `mUnitFactor` across different device-configured unit settings (e.g. if switched to mR/h or another unit) hasn't been independently confirmed from source — it's been empirically validated only against this device's current configuration (µSv/µSv-h). If the on-device unit setting is ever changed, re-validate against the screen before trusting new decoded output.

### Cross-validation

Decoded output was checked against two independent ground truths, both matching within display-rounding tolerance:

1. **The device's own LCD screen** — 873 µSv total dose / 0.12 µSv/h dose rate, matching the script's `dose=873.91 µSv` / `doseRate=0.1279 µSv/h`.
2. **The vendor's iOS "RadFlash" app**, connected to the same device independently.

## Scripts

### `pm1630_mqtt_publish.py` — collector of record on `pi-pm1630` (verified on the Pi 2026-09-26)

| Item | Verified value |
|---|---|
| Host | `pi-pm1630` — Raspberry Pi Zero W, Raspbian (Debian 12 base), kernel 6.12 armv6l; DHCP `{{PI_PM1630_IP}}`; reachable as `pi-pm1630.local` (mDNS) and by IP — `pi-pm1630.lab.local` not confirmed |
| Login | `labadmin` |
| Script | `/home/labadmin/pm1630_mqtt_publish.py` (previous version kept as `pm1630_mqtt_publish.py.bak`) — project copy of the same file: `pm1630_mqtt_publish.py` |
| Runtime | system `python3` 3.11, `bleak` (BlueZ D-Bus backend) + `paho-mqtt` in `~/.local` |
| Unit | `/etc/systemd/system/pm1630-mqtt.service` — `User=labadmin`, `EnvironmentFile=/home/labadmin/homelab-secrets/pm1630-mqtt.env` (provides `PM1630_MQTT_PASSWORD`), `ExecStart=/usr/bin/python3 -u …`, `Restart=always`, `RestartSec=15`, enabled |
| PM1630 BLE address | `{{MAC_PM1630}}` — `Trusted: yes`, **`Paired: no`** (the handoff doc's "paired/trusted" is only half right) |
| Device notify cadence | **~1 notification per second** (measured from journal timestamps) |

Behaviour (2026-09-26 version): scans for a device named `PM1630`, connects by address, subscribes to `Instrument_data`, decodes (byte layout above; Sv × 1,000,000 → µSv), prints one line per notify to the journal and publishes JSON to `homelab/telemetry/pm1630/data` with **`retain=True`**. LWT `offline` on `homelab/status/pm1630`, `online` published after connect, `offline` published on every exit path. **Exits (for systemd restart) on BLE disconnect or after 30 s without a notification.**

Published JSON shape (2026-09-26 — `dose_uSv` now 6 decimals, was 3; values illustrative):
```json
{"flags": 0, "dose_uSv": 940.252095, "dose_time_s": 23230881, "dose_rate_uSvh": 0.1299, "dr_statistics_pct": 1, "battery_pct": 98, "timestamp": "2026-09-26T08:42:04.592000+00:00"}
```

#### Incident 2026-09-24 → 09-26: silent 49-hour stall (fixed 2026-09-26)

- **Symptom:** last reading Sep 24 00:18:45 CST; service still `active (running)` on Sep 26; `bluetoothctl` showed `Connected: no`. No error in the collector or `bluetooth` logs.
- **Cause:** the 2026-09-03 Pi port's read loop (`while True: await asyncio.sleep(1)`) never checked the BLE link. bleak raises nothing when a peripheral drops, so the process stayed alive with no data and `Restart=always` never fired. The MQTT session (and therefore the `online` status/LWT) most likely stayed up the whole time — a status-topic check alone would have reported healthy.
- **Fix:** `disconnected_callback` + 30 s no-data watchdog → process exits non-zero → systemd restarts.
- **Verified 2026-09-26** with a forced `bluetoothctl disconnect`: exit detected at 02:26:52 (2 s), restart +15 s, first reconnect attempt hit bleak's 30 s connect timeout (also seen on a manual restart at 01:38 — the PM1630 seems to need a short while after a disconnect before accepting a new connection), second retry connected at 02:29:01. **Worst observed gap: ~2 min**, versus 49 h before.
- Cause of the original drop on Sep 24 is unknown (device was in range at RSSI −27 dBm afterwards).

#### Health check 2026-10-07

- Collector `active`, BLE `Connected: yes`, ~1 reading/s; dose counter 940.25 → 979.39 µSv between 2026-09-26 02:42 and 2026-10-07 21:39 (≈ 0.138 µSv/h average, consistent with the ~0.127 µSv/h displayed rate — no counter reset or jump).
- Pi rebooted uncleanly three times (logged as Sep 28, Oct 5, Oct 7 — timestamps are the fake-hwclock restore time, not the real boot time) due to **site power outages** (confirmed by user); the service recovered on its own each time. `vcgencmd get_throttled` = `0x0`, no SD/ext4 errors in the kernel log. Pi is on a surge suppressor (not a UPS).
- **Journal is volatile** (journald reports no persistent journal despite `/var/log/journal` existing), so collector/BLE history is lost at each reboot. User chose not to investigate further.

#### Dose-counter resolution (observed 2026-09-26)

The device's cumulative dose is a float32 in Sv. At ~940 µSv one float32 step is ≈ 0.00006 µSv, while one second at background (~0.13 µSv/h) adds only ≈ 0.000036 µSv. The counter therefore stays flat for several seconds and then moves in lumps (e.g. identical `940.203201` for 6 s; later steps of +0.000407 / +0.000233 µSv per second). Averaged over 02:23:05 → 02:42:03 (1,138 s) the counter rose 0.048487 µSv ≈ 0.153 µSv/h, broadly consistent with the displayed ~0.13 µSv/h dose rate. Consequence for the app's dose-increment design: use counter deltas over longer windows (or when the rate is elevated) and dose rate × Δt for short intervals at background — see Mobile App → Dosimetry model. Step size grows as the lifetime counter grows.

### Mac-era scripts (retired 2026-08-22)

Originally in `~/Desktop/` on Jorge's Mac with a venv at `~/ble-test-venv/` (`bleak` CoreBluetooth backend + `paho-mqtt`): `pm1630_decoded.py` (read-only console decoder, no network — still a useful offline test tool), `pm1630_read.py`, `pm1630_notify.py`, and the original Mac `pm1630_mqtt_publish.py` (interactive `getpass`, no LWT/retain). Project copies of the first three are kept; the project's `pm1630_mqtt_publish.py` now holds the Pi version.

### `meraki_inventory.py` — read-only Meraki Dashboard API inventory (added 2026-09-26)

GET-only script supporting the Meraki Integration Architecture section below: lists each MR AP (model, MAC, firmware, lat/lng, floor plan, tags), network Bluetooth settings, configured MQTT brokers (no secrets printed), Location & Scanning settings, floor plans, and Bluetooth clients seen in the last 24 h filtered for `PM1630`. API key via `getpass`. Project path `claude/meraki_inventory.py`. Not yet run.

## Data plane — current (verified 2026-09-26)

`pi-pm1630` publishes to the **ONC HQ Lab homelab's** Mosquitto broker:

| Field | Value |
|---|---|
| Broker | `{{LAB_BROKER_IP}}:1883` (plaintext + password/ACL, homelab `mqtt` VM) — TLS/8883 planned (homelab-owned, needs the Pi's AD CS client cert) |
| MQTT user | `pm1630` (write-only ACL on `homelab/telemetry/pm1630/#` and `homelab/status/pm1630`) |
| Telemetry topic | `homelab/telemetry/pm1630/data` — retained, qos 0, ~1 msg/s |
| Status topic | `homelab/status/pm1630` — retained `online`/`offline`, qos 1, LWT |
| Client ID | `pi-pm1630-collector` |

This remains the lab/area-monitor data plane. The mobile app does **not** use it — the app sends to the GCP "Location and scanning" stack (see Mobile App section). The `pm1630` user can't subscribe; lab-side verification uses the homelab's `testuser` (see `pm1630-homelab-handoff.md` §4).

Historical: first verified end-to-end from the Mac on 2026-08-15 (console output and `mosquitto_sub` matched field-for-field).

## Roadmap (updated 2026-09-26)

- **Done:** Pi collector (`pi-pm1630`, fixed area-monitor mode) live since 2026-09-03; hardened 2026-09-26 (stall watchdog, retained telemetry, finer dose resolution).
- **Next (2026-10-08, prioritised by user):** real-time dashboard on GCP with private + public views — see "Real-Time Dashboard" section (phases P0–P6).
- **After the dashboard (lab):** MR33 beacon-advertising + scanning coexistence test and `meraki_inventory.py` run (Meraki §8) — the only Meraki item blocking app checkpoints.
- **Then (product):** mobile app (iOS + Android) as the collector for worn units — BLE collector + local alarms first, GCP ingest, Site A/B beacon checkpoints, global GPS exposure map last. See Mobile App section.
- **Lab-side, homelab-owned:** TLS/8883 for the Pi, fixed-IP reservation for `pi-pm1630`, PRTG monitoring (now unblocked on the collector side).
- Not yet scoped: whether the Pi/area-monitor path eventually also reports to the GCP stack instead of the homelab broker.

## PRTG Monitoring — Collector Requirements (30s Poll, Homelab Side)

*Added 2026-08-21.* The ONC HQ Lab homelab plans to monitor `homelab/telemetry/pm1630/data` via PRTG at a 30-second poll interval once its Phase 5 (PRTG) is built — see that project's `homelab_build_plan_v5.md`, sections 6.3b and 6.6, for the full homelab-side design (ACL, an example EXE/Script sensor with a reading-age/freshness channel). This is independent of whatever this project does with the same data — the homelab wants its own visibility into the collector's health as part of its general monitoring coverage, separate from anything this project builds for its own purposes.

To make that possible, `pm1630_mqtt_publish.py` needs three changes on this side:

1. **Publish with the MQTT `retain` flag set.** PRTG's poll pattern is a short-lived `mosquitto_sub -C 1` snapshot read each cycle, not a persistent live subscriber — without `retain`, a poll landing between publishes gets nothing. In `paho-mqtt`: `client.publish(topic, payload, qos=..., retain=True)`.
2. **Set an LWT (Last Will and Testament) on `homelab/status/pm1630`** (retained, `online`/`offline` payload), matching the homelab's `homelab/status/<device>` convention. Configure via `client.will_set("homelab/status/pm1630", payload="offline", retain=True)` before `connect()`, then publish `"online"` (retained) right after a successful connect. This is what lets a PRTG freshness-check sensor tell "collector process is down" apart from "reading just hasn't refreshed yet" — retained telemetry data alone can't distinguish those two states.
3. **Run as a persistent, long-lived connection**, not a fresh BLE connect-and-disconnect per poll cycle. The PM1630 only accepts one BLE connection at a time (confirmed this session — the iPhone RadFlash app's connection dropped the Mac script's mid-session). Reconnecting BLE every 30s would be slower and risks that same connection-exclusivity conflict if anything else tries to read the device concurrently. Structure the collector as a long-running process (`launchd` on the Mac for now, `systemd` once this moves to a Pi) that holds the BLE connection open and publishes on the device's own notify cadence — PRTG's 30s figure is its own poll cadence, not a requirement on how often the device reports.

No broker/ACL change is needed on the homelab side for this — the `pm1630` mosquitto user's existing write-only grant (`homelab/telemetry/pm1630/#`) already covers the new `homelab/status/pm1630` LWT topic, since it's a wildcard.

**Status 2026-09-26: all three met** on `pi-pm1630` — LWT/`online` since the 2026-09-03 port, `retain=True` and the stall watchdog added 2026-09-26 (see Scripts). Caveat learned from the Sep 24 incident: the status topic reflects the MQTT session, not BLE data flow — a PRTG sensor should check **reading age on the retained telemetry** as well as the status topic. (The watchdog now makes a BLE stall end the process, which flips status to `offline`, but reading age remains the more direct check.)

## Real-Time Dashboard — GCP backend + private/public views

*Added 2026-10-08. Decisions stated/confirmed by the user 2026-10-08; design-level, nothing built. Prioritised **before** the Meraki tests and the mobile app.*

### Goal & decisions

- Real-time view of the sensor (**refresh ≤ 60 s**), and the first piece of the shared backend: the **server becomes the data source**, the phone app later becomes thin (collect, buffer, forward, local alarm). History, map, analytics and supervisor views come from the backend.
- **Platform: GCP** — reuse the "Location and scanning" project (user-reported 2026-10-08: project + billing, Firestore + Pub/Sub, Cloud Run in use, Firebase enabled — to verify with read-only `gcloud` before building).
- **Viewers:** the user privately now, plus a **public web page** (default Firebase `*.web.app` URL; custom domain later).
- **Meraki AP observations of the PM1630:** wanted, with an **easy off switch** — but **deferred** (user 2026-10-08: lab test for later); not in the first dashboard version.
- **Alarms in two places (user 2026-10-08):** on the phone (local, offline-safe) **and at the monitoring center** (dashboard + notifications). For the fixed Pi, which has no phone, the backend evaluates thresholds itself.
- **History is a core requirement (user 2026-10-08):** keep as much as possible at full resolution, at the lowest possible cost — see History & cost below.
- **Not hosted on the lab NGINX:** a public site there would open an inbound path into the lab (DC/AD CS live there), expose the home IP, and go down with every power outage. The lab only makes **outbound** HTTPS calls; the lab NGINX stays available for an internal/VPN-only mirror if ever wanted.
- **Local alarms stay on the phone** (offline/flight safety); the monitoring center gets the same alarm events from the backend.
- Honest limit: with today's fixed Pi collector the dashboard can't show a *person roaming* with continuous dose (BLE range ≈ one room). What it can show now is **which MR33 hears the PM1630** next to the dose stream — a first visual roaming test between the two Site A APs. Continuous roaming dose comes later from the phone collector, through the same backend.

### Architecture

```
pi-pm1630 ─MQTT─▶ homelab broker ◀─MQTT─ MR33s (homelab/telemetry/ble/meraki)
                        │  read-only MQTT user "gcp-bridge"
                  gcp-bridge (new Proxmox LXC, VLAN 30 — homelab provisions, this project owns the service)
                   ├─ every 60 s: one summary per device
                   ├─ alarm / offline events: immediately
                   └─ Meraki observations of the PM1630 MAC only — ON/OFF (bridge setting)
                        │ HTTPS POST, bearer device token (outbound only)
                        ▼
              Cloud Run "pm1630-ingest" ─▶ Firestore (live state, minute summaries, config)
                                        └▶ Pub/Sub ─▶ BigQuery subscription (full history, no code)
   Private dashboard (Firebase Hosting + Firebase Auth, user only): Firestore realtime, full detail
   Public page (Firebase Hosting, no login): Cloud Run "pm1630-public", response cached 60 s
```

- **Why a bridge, not a Pi change:** one service forwards both the dose stream and the Meraki feed; the Pi collector stays untouched; phones will later POST to the **same** ingest API.
- **Volume / cost:** ~2 Firestore writes per device per minute (live doc + minute summary) ≈ 2,900/day — well inside the free tier; raw per-second readings are not written to Firestore. Public viewers never read Firestore directly (cached endpoint), so traffic can't run up read costs.

### GCP project state (verified 2026-10-08, read-only `gcloud`)

- Project `{{GCP_PROJECT_ID}}` (account `{{OWNER_EMAIL}}`), shared with the existing "Location and scanning" (OSL) stack — **PM1630 resources must be additive and namespaced (`pm1630-…`) and must not modify OSL resources.**
- Enabled APIs: Cloud Run, Cloud Build, Artifact Registry, Firestore (+ `firebaserules`), Pub/Sub, BigQuery (+ Storage/Data Transfer), Secret Manager, IAM. **Not seen:** Firebase Hosting, Firebase Auth (Identity Toolkit), FCM — to confirm.
- Cloud Run (all `us-central1`): `meraki-to-pubsub`, `pubsub-to-firestore`, `osl-alert-sink`, `osl-daily-rollup`, `osl-deliver-webhook`, `osl-process-observations`, `osl-sweep-absences`, `osl-unknown-summary`.
- Firestore: `(default)` database, Native mode, `nam5` (US multi-region) — used by OSL.
- Pub/Sub topics: `scanning-api-post`, `presence-events` (OSL).
- BigQuery datasets: `location` (OSL).
- Planned PM1630 additions: Cloud Run `pm1630-ingest`, `pm1630-public` (`us-central1`); topics `pm1630-readings`, `pm1630-alarms`; BigQuery dataset `pm1630`; secret(s) `pm1630-device-token-*`; dedicated least-privilege service account `pm1630-ingest`.

### GCP resources — P1a foundations (✅ created and tested 2026-10-08)

- **Firestore:** named database **`pm1630`**, Native mode, `nam5` — user decision 2026-10-08 (own rules, zero risk to OSL's `(default)`; small cost because the free allowance only covers `(default)`).
- **Pub/Sub:** `pm1630-readings` (one message per 60 s batch), `pm1630-alarms`, `pm1630-deadletter` (+ hold subscription, 7-day retention).
- **BigQuery** dataset **`pm1630`** (location `US`, same as OSL's `location` dataset), no partition expiry (keep everything; untouched partitions drop to long-term pricing automatically):
  - `raw_batches` — one row per batch: `batch_ts`, `received_ts`, `device_id`, `source`, `readings` ARRAY<STRUCT<ts, dose_rate_uSvh, dose_uSv, dose_time_s, dr_statistics_pct, battery_pct, flags>>, minute summary columns; partitioned by `DATE(batch_ts)`, clustered by `device_id`. Nesting all ~60 readings in one row keeps Pub/Sub/BigQuery message counts at 1,440/day/device.
  - `alarms` — partitioned by `DATE(ts)`, clustered by `device_id`.
  - view `readings` — flat one-row-per-reading via `UNNEST`.
- **Pub/Sub → BigQuery subscriptions** (`--use-table-schema --drop-unknown-fields`, dead-letter after 5 attempts). Timestamps are sent as **integer microseconds since epoch** (the documented integer→TIMESTAMP mapping) to avoid string-format rejections. The Pub/Sub service agent gets `bigquery.dataEditor` on dataset `pm1630` only (BigQuery `GRANT … ON SCHEMA`).
- **Service account `pm1630-ingest`**: `pubsub.publisher` on the two PM1630 topics only; `datastore.user` with an IAM condition limiting it to database `pm1630`; `secretmanager.secretAccessor` on the PM1630 token secret only.
- **Device token** `pm1630-device-token-pi-pm1630` in Secret Manager — generated locally with `openssl`, piped straight in, never printed.
- **Firebase Hosting / Auth / FCM not enabled yet** — needed at P5 (dashboard) and for phones later.
- **Verified 2026-10-08 (run in Cloud Shell):** Firestore `pm1630` created (`freeTier: false`, delete protection on); topics, dataset, tables, view, BigQuery + dead-letter subscriptions, service account, conditional Firestore role, token secret all created. End-to-end test: a JSON batch with integer-microsecond timestamps and a nested `readings` array landed in `raw_batches` and the `readings` view within 60 s; dead-letter empty. A `test-device` row remains in history (delete later with DML once out of the streaming buffer).
- Note: the project IAM policy is now **version 3** (conditional binding). Future unconditional `gcloud projects add-iam-policy-binding` calls may need `--condition=None`.

### P1b — `pm1630-ingest` Cloud Run service (✅ deployed and tested 2026-10-08)

Project copy: `pm1630-ingest/main.py`, `requirements.txt`, `Procfile` (Flask + gunicorn, buildpack source deploy).

- `POST /v1/batch` — bearer token → collector (`TOKEN_<COLLECTOR>` env from Secret Manager), device must be in `DEVICES_<COLLECTOR>` (`|`-separated). Accepts ISO or epoch s/ms/µs timestamps; publishes the batch (µs timestamps) to `pm1630-readings`; writes `devices/{id}/minutes/{yyyyMMddHHmm}` (with `expire_at` for a 30-day TTL) and the live `devices/{id}` doc. Dose increment = counter delta vs the previous batch's last value (covers gaps), else within-batch delta. Replayed older batches never overwrite live state. `clock_suspect` when the bridge's `received_at` and the last reading differ by > 5 min.
- Thresholds from Firestore `config/thresholds` (`default` / `devices.<id>`, key `rate_uSvh_max`); **none configured = no radiation alarms** (values must come from an RSO / cited source). Entering/leaving `rate_high` writes `alarms/{id}` and publishes to `pm1630-alarms`.
- `POST /v1/event` — `collector_online` / `collector_offline`; offline raises an alarm.
- `GET /health` (renamed from `/healthz` 2026-10-08: Cloud Run's Google front end intercepts `/healthz` and returns its own 404).
- Tested locally against stubbed Pub/Sub/Firestore: 401 on bad token, 403 on foreign device, batch summary/increment, threshold alarm + no repeat, replay handling, offline alarm.
- **Verified 2026-10-08:** URL `{{PM1630_INGEST_URL}}` (equivalent newer form `{{PM1630_INGEST_URL_ALT}}`); revision `00002` serves `/health` → `{"ok":true}`; Firestore TTL on `minutes.expire_at` **ACTIVE**; bad token → 401; good batch → 200 (`dose_increment_uSv` 0.0 for a single-reading first batch, as designed); row landed in BigQuery `raw_batches` with `source = pi-pm1630`; Firestore `devices/test-device` live doc shows `status: online`, `collector: pi-pm1630`, `last_dose_uSv: 979.4`.
- Deploy: public at the IAM level (`--allow-unauthenticated`) because the bridge has no Google identity; auth is enforced by the bearer token. Max 2 instances, scale to zero.

### Bridge container `gcp-bridge` — plan (2026-10-08; lab specifics pending the ONC HQ Lab docs)

| Item | Plan | To reconcile with lab docs |
|---|---|---|
| Host | Proxmox **unprivileged LXC**, Debian 12, 1 vCPU / 512 MB / 4 GB, onboot, included in existing backup job | which node/storage; backup policy |
| Network | VLAN 30 (same as broker and Pi), static IP + DNS name `gcp-bridge.lab.local` | free IP in `ip-vlan-plan.md` VLAN 30 table |
| Firewall | inbound: SSH from admin VLAN only; outbound: TCP 1883 (later 8883) to `{{LAB_BROKER_IP}}`, HTTPS 443 to `*.run.app` / Google APIs, NTP | existing firewall policy for VLAN 30 |
| Broker access | read-only Mosquitto user `gcp-bridge` (see open item 24); move to 8883/mTLS with an AD CS client cert when the Pi does | `mqtt-vm-operational.md` ACL conventions |
| Software | `/opt/pm1630-bridge` Python venv (`paho-mqtt`, `requests`), systemd `pm1630-bridge.service`, `Restart=always`, secrets via `EnvironmentFile` (mode 600: MQTT password, device token) | — |
| Function | subscribe `homelab/telemetry/pm1630/data` + `homelab/status/pm1630`; every 60 s POST one batch (all readings + summary) to `pm1630-ingest`; offline/online events immediately; Meraki forwarding OFF by default (`FORWARD_MERAKI=false`, phase P7) | — |
| Resilience | SQLite queue `/var/lib/pm1630-bridge/queue.db` — buffer while the internet/GCP is unreachable, replay in order, capped (~7 days) | — |
| Time | LXC uses the Proxmox host clock (NTP). The Pi has no RTC — after a power outage its first readings can carry a stale clock until NTP syncs, so the bridge also stamps `received_ts` and ingest flags batches whose device clock is skewed > 5 min | — |
| Health | backend treats "no batch > 3 min" as `stale` → monitoring-center alarm; bridge logs one line per batch | — |
| Ownership | homelab provisions the LXC, IP, firewall and MQTT user; this project owns everything inside `/opt/pm1630-bridge` and the unit | — |

### P3 — bridge service code (written 2026-10-08, not yet deployed — waiting for the LXC + MQTT user)

Project copy: `pm1630-bridge/pm1630_bridge.py`, `requirements.txt`, `pm1630-bridge.service`.

- Subscribes to `homelab/telemetry/pm1630/data` (QoS 0) and `homelab/status/pm1630` (QoS 1) as the read-only `gcp-bridge` user.
- **Skips retained telemetry** (the Pi now publishes with `retain=True`, so a reconnect would otherwise re-send an old reading) and de-duplicates by timestamp.
- Every 60 s queues one batch `{device_id: pm1630-{{PM1630_SERIAL}}, received_at, readings[]}`; LWT status changes and a **"no readings > 180 s" stall** queue `collector_offline`/`collector_online` events (startup with retained `online` sends nothing).
- Local **SQLite queue** (`/var/lib/pm1630-bridge/queue.db`, cap ~7 days) — sends oldest-first, exponential back-off to 5 min on network/5xx errors, 400/401/403/413 moved to a `dead` table instead of blocking the queue; partial window kept on shutdown.
- `FORWARD_MERAKI` reserved (P7), ignored with a warning if set.
- systemd unit: dedicated `pm1630bridge` user, `EnvironmentFile=/etc/pm1630-bridge/bridge.env` (MQTT password + device token, mode 600), `Restart=always`, hardening (`ProtectSystem=strict`, `ProtectHome`, `NoNewPrivileges`, `PrivateTmp`).
- Tested locally with mocked MQTT messages and HTTP: retained/duplicate/bad messages skipped, batch flush, outage → back-off → replay, LWT offline event, stall detection and resume, 403 → dead table without blocking later items.

### History & cost (added 2026-10-08)

- **Keep every reading** (~1/s per device), not just minute summaries: the bridge sends one HTTPS batch per 60 s containing all raw readings plus the summary; ingest publishes the raw batch to Pub/Sub → **BigQuery raw table** (partitioned by day, clustered by device) and writes only live state + minute summary to Firestore.
- **Order of magnitude per device:** ~86,400 readings/day ≈ 10–15 MB/day uncompressed ≈ 4–5 GB/year. At list prices (to re-check at build time) that sits inside BigQuery's free storage/query allowance for a handful of devices, then costs cents per month; BigQuery moves untouched partitions to cheaper long-term storage automatically. Pub/Sub and Cloud Run volumes (1,440 batches/day) are far below their free allowances.
- **Cost guards:** no per-reading Firestore writes; dashboards query BigQuery only through pre-aggregated tables/views (1-min, 1-h) instead of scanning raw data; budget alert on the GCP project.
- **Gaps:** if the bridge loses internet it buffers locally (SQLite) and replays; if the lab is down entirely, rate history for that window is lost but the device's cumulative dose counter still covers the total. Future option: backfill from the PM1630's own on-board history (`#Instrument_hist` characteristic — not yet decoded).
- Optional second copy: a full-resolution local archive in the homelab (free disk), if the lab resources allow — pending review of ONC HQ Lab resources.

### Monitoring-center alarms (added 2026-10-08)

- Ingest evaluates per-organisation thresholds (dose rate, cumulative per shift/day) on every batch and writes an `alarms/{id}` document (device, type, value, threshold, time, acknowledged-by).
- Dashboard: realtime alarm banner/sound, acknowledge button, alarm history (BigQuery).
- Notifications via n8n (channel TBD). Collector `offline`/stale (no data > N min) is also an alarm type, so a silent stall like Sep 24 reaches the monitoring center.
- Phone-raised alarms (later) arrive through the same API with their original timestamp.
- **Delivery model (user 2026-10-08): independent subscribers.** The alarm lives in the database; every client — monitoring-center dashboard, each phone — receives it from there on its own (Firestore realtime listener while open; push notification, e.g. Firebase Cloud Messaging, when the app is in the background), wherever it is. No client depends on another to relay it.

### Data model (proposed)

| Firestore path | Content |
|---|---|
| `devices/{deviceId}` | live: last summary, `last_seen`, collector `status` (online/offline/stale), battery, uncertainty |
| `devices/{deviceId}/minutes/{yyyyMMddHHmm}` | 60 s summary: `rate_min/mean/max_uSvh`, `dose_increment_uSv`, `dose_total_uSv`, `n_readings`, `dr_statistics_pct`, `battery_pct` (TTL ~30 days; long-term history in BigQuery) |
| `devices/{deviceId}/meraki/latest` | per-AP RSSI / last-seen for the device (written only when Meraki forwarding is on) |
| `config/public` | `public_enabled`, `show_meraki` (both read by the API and the dashboards; off = not served, not just hidden) |

Dose increment per minute uses the device's cumulative counter delta (60 s windows are long enough at background — see Scripts → Dose-counter resolution).

### Meraki off switch (two levels)

1. `config/public.show_meraki = false` → API stops serving Meraki fields, dashboards hide them — immediate.
2. Bridge setting `FORWARD_MERAKI=false` → Meraki data stops leaving the lab at all.

### Public page rules

- Shows: current dose rate, 24 h chart, dose received today, "last updated N min ago", collector online/offline.
- Never shows: MAC addresses, IPs, Meraki AP names/MACs, the site's location or AP names that could identify the residence (public labels like "AP-1/AP-2" only if Meraki is shown publicly at all).
- Permanent banner: *"Proof of concept — device decode reverse-engineered, not calibrated; not for safety decisions."*
- If the lab is offline (e.g. power outage) the page stays up and shows the reading age.

### Build phases

| Phase | Deliverable | Owner |
|---|---|---|
| P0 | Verify GCP state read-only (`gcloud`): project, region, enabled APIs, Firestore mode, existing Cloud Run / Pub/Sub | this project |
| P1 | Cloud Run `pm1630-ingest` + Firestore schema + device token in Secret Manager; tested with `curl` | this project |
| P2 | Proxmox LXC + read-only MQTT user `gcp-bridge` | **homelab** |
| P3 | `gcp-bridge` service (Python, systemd): subscribe, 60 s batches (raw + summary), local buffer/replay, immediate alarm/offline events | this project |
| P4 | Pub/Sub topic + BigQuery subscription (history) | this project |
| P5 | Private dashboard (Firebase Hosting + Auth, realtime) | this project |
| P6 | `pm1630-public` endpoint (cached) + public page | this project |
| P7 (later) | Meraki AP observations of the PM1630, with the two-level off switch | this project |


## Mobile App — Real-Time Personal Exposure Monitor & Global Exposure Map

*Revised 2026-09-26 — replaces the 2026-08-21 "circulation tracking & heat-map" design (zone-based, fixed-Pi collector, server-side Meraki correlator). Decisions below were stated/confirmed by the user 2026-09-26. Design-level only — nothing built.*

### Purpose & audience

- **Real-time personal radiation exposure detection** for people who work with radiation-producing equipment — hospitals (X-ray departments) and any organisation that uses X-ray for imaging or scanning (industrial radiography, security screening, etc.). The goal is to warn the person **within seconds/minutes** of an exposure, not in a dosimetry-badge report weeks after the fact.
- The map is a secondary output: a **global exposure map** built from GPS-tagged samples, including while travelling (flights included).
- Platforms: **iOS and Android**.
- Role: **Supplementary early-warning tool** alongside the legal dosimeter of record (TLD/OSL badge) — not a replacement for it until the decode is formally verified and the device's calibration is documented (see Known limitations).

### Dosimetry model (decided 2026-09-26)

Dose (µSv) = dose rate (µSv/h) × time. Dose **does** accumulate (regulatory limits are cumulative — ICRP: 20 mSv/yr occupational averaged over 5 years, 1 mSv/yr public); dose **rate** is the early-warning signal for how fast it's accumulating right now. The app uses three distinct signals and never averages dose rate over a map area:

| Signal | Used for | Evaluated where |
|---|---|---|
| **Dose rate** (µSv/h), every device notify | Instant alarm — "you are in a hot area now" | On the phone, immediately, works offline |
| **Dose increment** (µSv received since previous sample) | What *this moment at this place* added to the person — the unit of the exposure map | Stored per sample with time + position |
| **Cumulative dose** per shift / day / trip / year | Limit tracking and cumulative alarms | Phone (live) + cloud (authoritative history) |

- **Dose increment is taken from the device's own cumulative `dose` counter** (Δ between consecutive readings), not computed as dose_rate × Δt. The PM1630 integrates internally, so this is more accurate and survives gaps: if BLE drops for N minutes, the next reading's Δ still contains the dose received during the gap — that chunk is stored with `position_quality = "unknown"` rather than lost. If Δ < 0 (device counter reset by the user), start a new segment and flag it.
- **Exposure map weight = dose increment**, so two minutes near a source outweighs one second passing it. Display aggregations (per trip, per day, per map cell) **sum** increments; they never average rates. Dose rate is still shown per point (colour/tooltip) for context.
- **`drStatistics`** (device statistical uncertainty, %) is stored on every sample and used to down-weight/flag noisy low-count readings — relevant at normal background levels.
- **Sampling cadence:** alarms evaluated on every device notify; samples persisted every *X* seconds (configurable; proposed default 10 s) **plus immediately on any alarm-state change**. Measured device notify cadence: ~1/s (2026-09-26). **Counter resolution caveat (observed 2026-09-26):** at background the float32 dose counter moves in lumps of ≈ 0.00006 µSv and stays flat for seconds at a time, so per-second counter deltas are unusable at background — compute increments from counter deltas over longer windows (e.g. ≥ 60 s) or when the rate is elevated, and from dose rate × Δt for short intervals; reconcile the two so the summed increments track the counter over time. See Scripts → Dose-counter resolution.

### Positioning (decided 2026-09-26)

| Context | Source | Stored as |
|---|---|---|
| Anywhere (global, incl. travel) | Phone GNSS/GPS — primary | `source: "gps"`, `accuracy_m` |
| Checkpoints — **Site A/B MR33s only, for now** | App detects the MR33 **iBeacon advertisements** (see Meraki section §3) | `source: "meraki_beacon"`, `checkpoint_id` |
| Indoors, no GPS, no checkpoint | OS network location | `source: "network"`, low confidence |
| No fix at all | — | `position_quality: "unknown"` (dose still recorded) |

### Flights & travel (decided 2026-09-26)

- User observation: dose rate spikes for the duration of a flight. This is expected — cosmic radiation at cruise altitude is typically ~10–50× ground level. **Flight readings are kept, alarmed and mapped like any other reading** until confirmed harmless.
- Caveat to display, not hide: a photon dosimeter likely **under-reports** total aircrew-type dose (the neutron component is largely not detected), so flight segments are labelled "likely underestimated — lower bound".
- Airplane mode: GNSS (receive-only) and BLE keep working; uploads don't → **local buffer on the phone**, sync after landing, alarms still fire locally. Flight segments auto-flagged from GNSS altitude/speed (proposed: altitude > 3,000 m or ground speed > 250 km/h), `flight: true`.

### Alerting & roles (decided 2026-09-26)

- **Recipients: the user AND their organisation's supervisor / radiation safety officer (RSO).**
- **Thresholds are set per organisation** (by the RSO/admin), not hard-coded — exposure limits and investigation levels differ by country and employer. Types: dose-rate alarm (instant) and cumulative alarms (shift / day / trip / year).
- **Local first:** the phone alarms the user immediately from its own threshold check — no cloud dependency, works offline and in flight.
- **Escalation:** when online, the alarm event goes to the cloud and n8n notifies the supervisor/RSO. Alarms raised offline escalate on reconnect **with their original timestamp and position**.
- Data model: organisation → RSO/supervisor(s) → users → devices (PM1630 serials). An RSO sees their own organisation's users only.

### Backend (decided 2026-09-26): reuse the GCP "Location and scanning" stack

```
PM1630 ──BLE──▶ Phone app (iOS / Android)
                 ├─ local alarms (dose rate + cumulative)        ← real time, offline-safe
                 ├─ GPS fix  /  MR33 iBeacon checkpoint (Site A/B)
                 ├─ local buffer (flights, no signal)
                 └─ HTTPS + Firebase Auth ──▶ Cloud Run ingest ──▶ Pub/Sub ─┬─▶ Firestore  (live state, alarms, org config/thresholds)
                                                                            ├─▶ BigQuery   (sample history, exposure map, reports)
                                                                            └─▶ n8n        (supervisor / RSO notifications)
```

- **HTTPS, not MQTT**, from the phone: GCP no longer has a managed MQTT service (Cloud IoT Core was retired in 2023), and HTTPS + Firebase Auth works through any mobile/hotel/airport network. Firestore's SDK offline persistence can double as the flight buffer.
- **The homelab broker is not the collection point for the app.** It stays for the fixed Pi area monitor and the Meraki MQTT lab cross-check only.

### Sample record (proposed)

```json
{
  "org_id": "…", "user_id": "…", "device_serial": "{{PM1630_SERIAL}}",
  "ts": "2026-09-26T14:03:10.120Z",
  "dose_rate_uSvh": 0.128, "dose_total_uSv": 873.91, "dose_increment_uSv": 0.0004,
  "dr_statistics_pct": 12, "battery_pct": 99, "flags": 0,
  "lat": 9.9347, "lng": -84.0875, "alt_m": 1150, "accuracy_m": 8,
  "source": "gps", "checkpoint_id": null, "position_quality": "good",
  "flight": false, "segment_id": "…", "alarm_state": "none"
}
```

### Relationship to the Raspberry Pi collector

- The phone is the collector of record for a **worn** PM1630. `pi-pm1630` becomes a **fixed area monitor** (a unit mounted in one place), a separate mode.
- The PM1630 accepts one BLE connection at a time, so a given unit is on either the Pi or a phone, never both.

### Privacy/HR flag

Stronger than before: the app now records **GPS position globally, including during travel**, not just work-zone presence. Needs (a) sign-off from whatever governs employee monitoring at each customer organisation (HR, legal, possibly a union agreement), separate from the radiation-safety/biomed/IT-security review; (b) explicit user consent and a clear on/off state; (c) a decision on what the supervisor/RSO sees — dose + alarms vs. precise location history. "The dose data is fine to collect" and "where the person went is fine to collect" remain separate approvals.

### Status

Design decisions recorded 2026-09-26. Nothing built. Next: the Meraki homelab verification (inventory, MR33 beacon broadcasting + scanning together) — see Meraki section §8 — then app scaffolding (BLE collector + local alarms first, map last).

## Meraki Integration Architecture — Existing MR33 / MR18 Infrastructure

*Added 2026-09-26, design-level — nothing here is built or verified yet. Revised later the same day after the app redesign: checkpoints now come from MR33 beacon broadcasting detected by the app (§3–4); MQTT is a lab cross-check, not the positioning path.* Scope: how to use the existing Cisco Meraki APs (MR33 ×2 at Site A — Rancho + Aparta, confirmed by user 2026-09-26 — plus one MR18) and the Meraki interfaces — **MR MQTT Data Streaming**, **Scanning API v3**, and the **Dashboard REST API v1** — to supply the positioning layer and to automate configuration/inventory/health. Sources: the two PDFs in this project (`MR MQTT Data Streaming.pdf`, `Meraki Location Analytics.pdf`), Meraki's MQTT doc page, the Dashboard API reference, Meraki's EOL and firmware-restriction pages (links at end of section).

### 1. Hardware reality check

| | **MR33** (×2, Site A) | **MR18** |
|---|---|---|
| Wi-Fi | 802.11ac Wave 2, 2×2 | 802.11n, 2×2 |
| Dedicated scanning radio | ✅ dual-band (WIDS/WIPS, spectrum, location analytics) | ✅ dual-band (WIPS/spectrum) |
| BLE radio | ✅ 2.4 GHz BLE, beacon + scanning | ❌ none |
| Max firmware | **MR 30.x** | **MR 26.x** |
| MR MQTT streaming (needs Wave 2+, IoT radio, MR ≥ 28, MR Advanced license) | ✅ capable (and already live in the homelab on `homelab/telemetry/ble/meraki`) | ❌ fails firmware + no IoT radio |
| Scanning API v3 (needs Wave 2+, MR ≥ 27) | ✅ Wi-Fi + BLE | ❌ |
| End-of-Sale / **End-of-Support** | May 7 2021 / **Jul 21 2026 — already passed** | Feb 13 2017 / **Mar 31 2024 — passed** |

Consequences:

- **The MR33s carry the whole design.** They are the only APs here that can see BLE at all. Past EoS they keep working and stay dashboard-manageable, but receive no more support or security patches — acceptable for the homelab POC, **not** something to present as a hospital deployment target.
- **The MR18 has no role in positioning.** No BLE, stuck on MR 26 (below both MQTT's 28.x and Scanning API v3's 27.x floor). Keep it out of the Site A network that runs MQTT: Meraki's doc notes the MQTT option may not appear when a network contains non-supporting APs (move MQTT-capable APs to their own network if so). Useful only as Wi-Fi coverage / spare.
- **2 APs is below Meraki's own 3-AP threshold.** Scanning API v3 and the dashboard Location Heatmap only compute x/y when a client is heard by ≥ 3 APs (firmware 26.1+); with 2 APs v3 returns only `nearestApMac`/`nearestApRssi`. So in the homelab the achievable result is nearest-AP / "closer to A or B" — enough to validate the pipeline, not positioning accuracy (for checkpoint entry/exit this doesn't matter).

### 2. The three Meraki data paths — which does what

| | **MR MQTT streaming** | **Scanning API v3** | **Dashboard REST API v1** |
|---|---|---|---|
| Direction | AP → your broker directly (local, no cloud hop) | Meraki cloud → HTTPS POST to your receiver | You → `https://api.meraki.com/api/v1` (pull) |
| Data | Raw per-AP observations: RSSI, client MAC, AP MAC, AP serial, timestamp, radio, network ID, raw payload, + iBeacon UUID/major/minor | Per-client aggregate: x/y + lat/lng + variance + floor plan (≥ 3 APs), `rssiRecords` per AP, nearest AP | Config, inventory, AP coordinates/floor plans, device status, BLE clients (history, ≤ 7-day lookback) |
| Cadence | Publish frequency configurable, min **1 s**; each client published once per cycle | ~1 min, plus 1–2 min delay (Meraki: "not a real-time location service") | On demand; rate-limited |
| Filters | BLE type (None/iBeacon/Eddystone/Unknown), MAC allow list, UUID allow list, RSSI hysteresis | Radio type per receiver (WiFi / BLE) | n/a |
| Transport security | None or **TLS** (CA cert upload) | HTTPS + shared secret + validator | HTTPS + API key |
| Fit for this project | **Lab cross-check** of what the APs observe (was: primary positioning signal, before the 2026-09-26 app revision) | Later, once ≥ 3 APs exist (hospital) — gives Meraki-computed x/y for free; overlaps the separate "Location and scanning" project's receiver work | **Automation + reference data** — provisioning, checkpoint lookup table, health, quick verification |

**Recommendation (revised 2026-09-26):** checkpoints via **MR33 beacon broadcasting detected by the app** (§3); Dashboard API for automation and the checkpoint lookup table; MQTT as a homelab cross-check; Scanning API v3 deferred until there are ≥ 3 BLE-capable APs covering each area of interest.

### 3. Collector mobility — resolved 2026-09-26

The phone app is the collector for a worn PM1630; the fixed Pi is a separate area-monitor mode (see Mobile App section). For checkpoints, the direction is **reversed** from the earlier design: instead of the APs tracking the device and a server joining the data, the **MR33s broadcast Meraki iBeacons** (Bluetooth settings: `advertisingEnabled: true`, `majorMinorAssignmentMode: "Unique"` → each AP gets its own major/minor under the network UUID) and the **app detects them** to know "at checkpoint Rancho/Aparta".

Why: works on the phone with no server-side join and no connectivity; no infrastructure-side tracking of the device's MAC; removes the dependence on the PM1630's own advertising behaviour (does it stop while connected? does its MAC rotate?). Unverified: that an MR33 advertises beacons **and** scans (for MQTT) at the same time — the datasheet lists "beacon and BLE scanning support" but not explicitly concurrent. First item in §8.

Platform note: iOS region monitoring for iBeacons works in the background (entry/exit events); continuous ranging is foreground-only or brief — fine for checkpoint entry/exit, not for continuous RSSI.

### 4. Meraki's role after the app revision

- **App path (product shape):** PM1630 → phone ← MR33 beacons; phone → GCP (Mobile App section). **No Meraki-to-cloud dependency** on the critical path.
- **Checkpoint table from the Dashboard API:** `GET /devices/{serial}/wireless/bluetooth/settings` gives each AP's beacon UUID/major/minor; combined with `GET /networks/{networkId}/devices` (name, lat/lng, tags) this produces the checkpoint lookup `(uuid, major, minor) → checkpoint_id, name, lat/lng` loaded into the app/backend config — automatable, no manual transcription.
- **Lab cross-check (homelab only):** MR MQTT → homelab broker keeps showing what the APs observed, to validate the app's checkpoint claims during testing. The earlier server-side correlator (join dose + MQTT RSSI by MAC/timestamp) is **no longer on the critical path** — optional lab tool only.
- **Scanning API v3 → Cloud Run receiver** (the "Location and scanning" stack already has this pattern): optional later, once sites have ≥ 3 BLE-capable APs; 1–3 min latency makes it unsuitable for alarms anyway.

### 5. MR MQTT settings to use (Wireless > IoT radio settings)

| Setting | Homelab test | Notes |
|---|---|---|
| Broker security | **TLS**, CA = homelab AD CS root, port 8883 | Closes the plaintext gap on the AP→broker leg; mosquitto 8883 already listening (per handoff §4). Needs a broker user with write-only ACL on the Meraki topic. |
| Topic | keep existing `homelab/telemetry/ble/meraki` | Default is `meraki/v1/mr/<NetworkID>/<AP MAC>/<ble/wifi>/<Client MAC>/` if ever reset |
| Publish frequency | 1 s during tests | Raise later; message volume grows with clients × APs |
| Hysteresis | off during tests | Turn on later to cut volume |
| BLE type filter | **None** (PM1630 adverts are most likely classed "Unknown") → **iBeacon** for phone-tag mode | |
| MAC / UUID allow list | PM1630 MAC once confirmed stable; project UUID for phone tags | Also a **privacy control**: publish only project tags, never staff/patient personal devices |
| **BLE beacon advertising** (Bluetooth settings) | **Enabled**, `majorMinorAssignmentMode: Unique` | Checkpoint source for the app (§3). Check it coexists with scanning/MQTT (§8 step 1) |
| Constraints | BLE MQTT and ESL can't coexist in a network; needs MR Advanced license; Bluetooth scanning must be on | |

Exact JSON payload format isn't published in Meraki's doc — capture real messages from the live topic before writing any parser.

### 6. Dashboard API automation — endpoint map

Base `https://api.meraki.com/api/v1`, header `Authorization: Bearer <key>` (legacy `X-Cisco-Meraki-API-Key` also accepted). Use a key from a **read-only** dashboard admin for inventory/monitoring; a separate write-capable key only for provisioning. Handle HTTP 429 with `Retry-After` (the official `meraki` Python SDK does this automatically).

| Purpose | Method + path | Use here | Checked 2026-09-26 |
|---|---|---|---|
| Find org / network | `GET /organizations`, `GET /organizations/{orgId}/networks` | IDs for everything else | standard v1 |
| AP inventory + coordinates | `GET /networks/{networkId}/devices` | model, MAC, serial, firmware, lat/lng, `floorPlanId`, tags → checkpoint lookup table | standard v1 |
| Floor plans | `GET /networks/{networkId}/floorPlans` | floor plan image + corner coordinates → heat-map background | standard v1 |
| BLE scanning + beacon advertising | `GET/PUT /networks/{networkId}/wireless/bluetooth/settings` | assert `scanningEnabled: true`, `advertisingEnabled: true`, `majorMinorAssignmentMode: "Unique"`, `eslEnabled: false` | ✅ |
| Per-AP beacon IDs (checkpoint table) | `GET /devices/{serial}/wireless/bluetooth/settings` | each AP's `uuid`/`major`/`minor` → `checkpoint_id` lookup for the app | ✅ (endpoint exists; fields not re-checked) |
| MQTT broker as code | `GET/POST /networks/{networkId}/mqttBrokers` (+ `PUT …/{mqttBrokerId}`) | name, host, port, `authentication{username,password}`, `security{mode, tls{caCertificate, verifyHostnames}}` | ✅ (via Cisco Ansible module docs) |
| Quick "does Meraki see the PM1630?" | `GET /networks/{networkId}/bluetoothClients?timespan=86400&perPage=1000` | `mac`, `deviceName`, `lastSeen`, `seenByDeviceMac` — not real-time, ≤ 7 days | ✅ |
| Scanning API v3 receiver | `GET/PUT /networks/{networkId}/locationScanning`, `PUT …/locationScanning/httpServers` | `analyticsEnabled`, `scanningApiEnabled`; receivers with `scanningApiVersion: 3`, `radioType: "BLE"`, `httpServer{url, sharedSecret}` (PUT replaces the whole list) | ✅ |
| AP health | `GET /organizations/{orgId}/devices/statuses`, webhooks `…/webhooks/httpServers` + `…/alerts/settings` | AP-down → PRTG / n8n; a dead AP silently degrades positioning | standard v1 |
| Firmware check | `GET /networks/{networkId}/firmwareUpgrades` | assert MR ≥ 28 before relying on MQTT | standard v1 |

Not found in the API this pass: the IoT-radio **RSSI publisher** settings (topic, frequency, filters, allow lists) — treat them as dashboard-only until shown otherwise. "Standard v1" rows are long-standing endpoints not re-checked this session.

### 7. Hospital-deployment implications

- **AP hardware:** both models here are past EoS. A hospital pilot needs currently supported Wi-Fi 5 Wave 2+/Wi-Fi 6+ APs with an IoT radio, MR 28+, MR Advanced licensing — check Meraki's EOL list at the time.
- **Density:** Meraki recommends a site survey with tertiary coverage (≥ 3 APs) across every area where x/y is wanted.
- **Shielding:** X-ray rooms are typically lead-lined, which will heavily attenuate 2.4 GHz BLE/Wi-Fi. Expect RSSI positioning to behave as room-level zone detection, probably needing an AP inside or at each shielded room — a plausible expectation, not tested.
- **Privacy:** Meraki's cloud hashes MACs for Location Analytics, but MQTT delivers **raw MACs** to the local broker — allow lists (§5) are the control that keeps it to project tags only. Staff location tracking still needs its own HR/legal/union sign-off, separate from the radiation-safety/biomed/IT-security review (see Privacy/HR flag above).
- **Claims:** position estimates are coarse zone/dwell indicators layered over spot-validated (not calibrated) dose data — nothing here is clinically validated.

### 8. Verification plan (homelab, 2× MR33)

1. Run `meraki_inventory.py` (read-only) → confirm AP models/firmware, `scanningEnabled`/`advertisingEnabled`, existing broker config.
2. **Beacon + scanning coexistence (critical for the app):** enable beacon advertising (Unique major/minor) on the Site A network while MQTT scanning stays on; at Site A, use a phone BLE scanner app (e.g. nRF Connect) to confirm each MR33 advertises an iBeacon with its own major/minor, **and** confirm `homelab/telemetry/ble/meraki` keeps flowing.
3. Optional (lab cross-check only): `bluetoothctl info <MAC>` → `AddressType` on `pi-pm1630`, and the connected-vs-stopped MQTT capture for the PM1630's own advertising. No longer blocking.
4. Then: app scaffolding — BLE collector + local alarms first, checkpoints second, map last.

Sources: [MR MQTT Data Streaming](https://documentation.meraki.com/Wireless/Operate_and_Maintain/How_Tos/Features_and_Integrations/MR_MQTT_Data_Streaming) · [Dashboard API v1](https://developer.cisco.com/meraki/api-v1/introduction/) · [Bluetooth Clients](https://developer.cisco.com/meraki/api-v1/get-network-bluetooth-clients/) · [Wireless Bluetooth Settings](https://developer.cisco.com/meraki/api-v1/get-network-wireless-bluetooth-settings/) · [Location Scanning](https://developer.cisco.com/meraki/api-v1/get-network-location-scanning/) · [Location Scanning HTTP Servers](https://developer.cisco.com/meraki/api-v1/update-network-location-scanning-http-servers/) · [Ansible networks_mqtt_brokers](https://docs.ansible.com/projects/ansible/latest/collections/cisco/meraki/networks_mqtt_brokers_module.html) · [Meraki EOL dates](https://documentation.meraki.com/General_Administration/Other_Topics/Meraki_End-of-Life_(EOL)_Products_and_Dates) · [Firmware version restrictions](https://documentation.meraki.com/Platform_Management/Product_Information/Compatibility_and_Firmware/Firmware_Upgrades/Product_Firmware_Version_Restrictions) · [MR33 datasheet](https://www.cloudwifiworks.co.il/datasheets/meraki_datasheet_mr33.pdf) · [MR18 specs](https://www.cloudwifiworks.com/mr18.asp)

## Known limitations (read before treating output as authoritative)

- The decode was derived by reverse-engineering a consumer/professional vendor app, not from Polimaster's official published documentation or a calibration certificate. Treat it as **spot-validated, not formally verified**.
- `mUnitFactor` behavior across different device unit settings hasn't been independently confirmed — see the byte layout table above.
- No authentication or encryption on this telemetry path yet (plaintext MQTT). Fine for a homelab test environment; not acceptable for anything touching a real hospital network without a security review.
- No patient data is or should ever be involved — this is device/environmental telemetry only (dose, dose rate, battery, device status).
- The app is designed as a supplementary early-warning tool, not the dosimeter of record; flight readings are a likely underestimate (neutron component largely undetected by a photon dosimeter).
- The homelab's Meraki APs (MR33, MR18) are past End-of-Support — fine for a POC, not a basis for any hospital deployment claim.
- **If this ever moves from POC toward real deployment in an actual hospital, involve the hospital's radiation safety officer, biomedical engineering, and IT security/compliance before it touches their network or informs any real safety decision.**

## Open items

1. **Shared decode module** — `pm1630_decoded.py` and `pm1630_mqtt_publish.py` currently duplicate the `decode()` logic. Candidate refactor into a shared `pm1630_protocol.py`, not yet done, low priority.
2. **`pm1630` MQTT credential rotation** — the password for this account was set up during this session's testing; track rotation status jointly with the homelab's `mqtt-vm-operational.md` Deferred items (that project owns the broker, so it owns the rotation record for now).
3. **`mUnitFactor` validation across unit settings** — not yet tested with the device configured to a different measurement unit.
4. ~~Pi-based telemetry capability~~ — **closed 2026-09-26:** verified live on `pi-pm1630` (see Scripts).
5. **Meraki infrastructure architecture** — designed 2026-09-26, see "Meraki Integration Architecture" section. Not yet built or verified.
6. ~~PRTG collector requirements~~ — **closed 2026-09-26:** retain, LWT and persistent connection + stall watchdog all in place on the Pi. PRTG itself remains homelab-side (Phase 5).
7. **Mobile app positioning approach** — decided 2026-09-26: GPS globally + MR33 beacon checkpoints at Site A/B (app-side detection), network location as low-confidence fallback. Supersedes the 2026-08-21 "Meraki telemetry + server-side correlator" default. Not yet built.
8. **Privacy/HR review for staff location tracking** — not yet initiated; scope grew 2026-09-26 to global GPS incl. travel, plus what supervisors/RSOs may see. Required before the app touches any real staff member's data — see Privacy/HR flag above and this project's instructions doc Sensitivity rules.
9. **Homelab verification test for Meraki-telemetry positioning** — *downgraded 2026-09-26 to an optional lab cross-check (no longer blocking; checkpoints now come from MR33 beacons, see item 14).* Three things it would answer (the 2026-08-21 design that motivated them has been replaced — see Mobile App section): (a) whether the PM1630 keeps advertising BLE while the collector script holds a GATT connection to it, (b) whether its advertised MAC is stable across sessions — *2026-09-26: strong evidence yes — the Pi has reconnected to `{{MAC_PM1630}}` for weeks without pairing, which BlueZ couldn't do if the address rotated; `AddressType` isn't printed by this BlueZ version,* also confirmed it advertises while disconnected (RSSI −27 dBm), (c) build a correlator script joining `homelab/telemetry/pm1630/data` readings with nearest-in-time `homelab/telemetry/ble/meraki` observations by MAC. Step-by-step plan in Meraki Integration Architecture §8. This is homelab-side testing work in support of this project — see that project's `homelab_build_plan_v5.md` and `ip-vlan-plan.md` for the corresponding homelab-side test item.
10. **Meraki AP density/layout for real hospital deployment** — even if the homelab test validates the pattern, a hospital deployment needs enough Meraki APs distributed through the X-ray department for meaningful RSSI differentiation. Not yet scoped — depends on actual department layout. See Meraki Integration Architecture §7 (≥ 3-AP rule, shielding, supported hardware).
11. **Run `meraki_inventory.py`** — read-only Dashboard API inventory (added 2026-09-26). Not yet run.
12. ~~Collector mobility decision~~ — **resolved 2026-09-26:** phone app is the collector for worn units; `pi-pm1630` is a fixed area monitor.
13. **Meraki MQTT broker leg over TLS** — switch the MR33 → mosquitto stream to TLS/8883 with the AD CS CA (§5). Homelab-owned config change; not started.
14. **MR33 beacon advertising + scanning coexistence test** — added 2026-09-26, blocking for app checkpoints. See Meraki §8 step 2.
15. **Mobile app build** (iOS + Android): BLE collector → local dose-rate/cumulative alarms → local buffer → GCP ingest → checkpoints → exposure map. Not started. Measure the device's actual notify cadence as part of the collector work.
16. **Organisation/threshold model** — per-organisation alarm thresholds set by RSO/admin; user + supervisor/RSO escalation via n8n; which default investigation levels to ship (if any) needs a radiation-safety source, not a guess.
17. **GCP ingest on the "Location and scanning" stack** — Cloud Run endpoint + Firebase Auth + Pub/Sub → Firestore/BigQuery schema for the sample record (Mobile App section). Not started.
18. **Flight handling** — auto-flag flight segments (GNSS altitude/speed), label as lower-bound, verify offline buffering through a real flight.
19. **Collector log polish (low priority)** — catch bleak's connect `TimeoutError` and log one line instead of a full traceback; behaviour unchanged (still exits for systemd retry).
20. **Handoff-doc corrections to pass back to ONC HQ Lab** — PM1630 is `Trusted: yes` / `Paired: no` (not paired); Pi reachable as `pi-pm1630.local` / `{{PI_PM1630_IP}}`, `pi-pm1630.lab.local` unconfirmed; collector now retains telemetry and has a stall watchdog (2026-09-26).
21. **Root cause of the Sep 24 BLE drop** — unknown; the watchdog makes it recoverable, but watch the journal for repeat `BLE disconnected` / connect-timeout patterns.
22. **Power-loss resilience (homelab-owned, optional)** — repeated site outages risk SD-card corruption (already happened once in Sep). Options if it becomes a problem: small UPS / pass-through power bank, read-only root (overlay FS), spare pre-flashed card. Persistent journal (capped) would also preserve evidence across reboots. Not pursued as of 2026-10-07 by user's choice.
23. **Real-time dashboard (GCP)** — added 2026-10-08, prioritised before items 11/14/15. Phases P0–P6 in the Real-Time Dashboard section. Not started.
24. **Homelab request: Proxmox LXC for `gcp-bridge` + read-only MQTT user `gcp-bridge`** (read on `homelab/telemetry/pm1630/#`, `homelab/status/pm1630`, `homelab/telemetry/ble/meraki` and `/#`) — homelab-owned, needed for dashboard phase P3.
