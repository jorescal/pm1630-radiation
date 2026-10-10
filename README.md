# PM1630 Radiation Monitoring (Real-Time Exposure POC)

A Polimaster PM1630 dosimeter integrated into an IoT pipeline. A BLE GATT collector on a
Raspberry Pi publishes to MQTT, a lab bridge forwards to Cloud Run, and Firestore/BigQuery
feed a real-time dashboard. A mobile app for worn units is planned.

> **Proof of concept, not a validated clinical safety system.** The decode was reverse-engineered
> from the vendor app. See "Known limitations" in `docs/pm1630-radiation-sensor.md`.

> This folder is a **section of the ONC HQ Lab hub** (`sections/radiation-sensor/`) and is also
> published as its own repository through `git subtree`. Identifying values are `{{TOKENS}}`.

## Layout

| Path | What it is |
|---|---|
| `docs/pm1630-radiation-sensor.md` | Technical source of truth: protocol, scripts, data plane, roadmap, dashboard, mobile app, Meraki, open items |
| `docs/homelab-handoff.md` | Hub-side handoff written at the split (2026-09-09) |
| `docs/polismart_feature_analysis.md` | Vendor app feature analysis |
| `collector/` | `pm1630_mqtt_publish.py` (collector of record on `pi-pm1630`) plus retired Mac-era scripts |
| `bridge/` | `gcp-bridge` LXC service (MQTT → Cloud Run), written, not yet deployed |
| `ingest/` | Cloud Run `pm1630-ingest` (deployed 2026-10-08) |
| `tools/meraki_inventory.py` | Read-only Meraki Dashboard API inventory |
| `claude/project-instructions.md` | Instructions currently active in the Claude Project |
| `claude/project-instructions-pending.md` | 2026-09-26 rewrite, **not yet pasted** into the Claude Project |
| `CHANGELOG.md` | Every change to this section |

## Interface with the hub

The hub owns the broker, the Pi hardware and network, and the planned `gcp-bridge` LXC.
This section owns what runs on them. See the hub's `docs/integrations/radiation-sensor.md`.
