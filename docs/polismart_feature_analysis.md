# Polismart Feature & Architecture Analysis

**Purpose:** reference notes extracted from a static analysis of Polismart v5.1.140 (Android), for use as feature inspiration and technical background while designing an original, proprietary radiation-instrument companion app (relevant to the PM1630 work already in this project). This document describes *what the app does* and *how its pieces fit together* at a functional level — it does not include any of Polismart's decompiled source code, and none should be copied into a new app. Everything below should be treated as a reference for independent, original implementation, not as a source to port.

App identity: package `psa.polismart`, developer Polimaster, min SDK 24 / target SDK 34, built with Firebase (Analytics, Crashlytics, Cloud Messaging), Google Play Services (Auth, Maps, Drive), and a preference-screen-driven UI.

---

## 1. Product shape

Polismart is a **multi-device companion app for a family of Polimaster radiation-detection instruments** (dosimeters, personal radiation detectors, spectroscopic detectors, area/backpack detectors). One app supports ~20 distinct hardware models by loading a per-device capability profile and swapping in a per-device communication protocol. The app has five primary modes, reachable from a bottom/tab navigation: **Dashboard, Track, History, World Map, Settings.**

This "one app, many device profiles" pattern is the single biggest architectural takeaway: rather than hard-coding UI/logic per device, the app reads a device capability descriptor (ranges, units, which features are supported) and adapts the UI, then dispatches to a protocol class selected by device ID.

---

## 2. Feature inventory by area

### 2.1 Device connection & pairing
- Bluetooth Classic + Bluetooth LE scanning for nearby instruments (`BleDevicesScanner`, background `BleDevicesScannerService`, foreground `PolismartBluetoothService`).
- "Paired devices" vs "Available devices" list distinction on the connect screen.
- Auto-reconnect / "search for other devices" while connected.
- Per-device info panel: Model, Serial Number, Hardware version, Firmware version, Battery level, with a "Reset Detector to Factory Defaults" action.
- Firmware-version compatibility check with a prompt to update the app if the connected firmware isn't supported.

### 2.2 Dashboard (live measurement)
- Two swipeable primary readouts: **Dose Rate** and **Dose** (accumulated), each with its own settings sub-screen.
- Unit systems: µSv/h, mSv/h, Sv/h (or µR/h, mR/h, R/h) for rate; µSv, mSv, Sv (or µR, mR, R) for accumulated dose. User-selectable Sievert vs Roentgen family and Metric vs Imperial.
- Statistical error / sigma display next to the live reading.
- Accumulation timer (days/hours/minutes) shown next to Dose, where the instrument supports it.
- Reset actions: reset dose, reset dose-rate statistics, restore thresholds to default — scoped per-mode.
- Two-tier alarm thresholds (1st/2nd) for both Dose and Dose Rate, each independently configurable, with colored indicator (orange = 1st, red = 2nd) and its own sound-alarm toggle.
- Neutron channel support for capable devices: separate Count Rate (Neutrons), gamma/neutron channel enable switches, combined gamma+neutron alarm messaging.
- **Search mode** for capable instruments (isotope/source search): live search readout, calibration state (CALIBRATED/UNCALIBRATED/CALIBRATION), identification-in-progress state, and a nuclide category classification shown as short codes: IND (industrial), MED (medical), NORM (naturally occurring), SNM (special nuclear material).
- Chemical/gas channel readout for devices that support it (BLOWING / CALIBRATED / ERROR / SEARCH / SWITCH OFF states, GV/L units, min/avg/max stats) — this is a distinct "chem" sensor path separate from the radiation dashboard.
- Compass / directional indicator on the dashboard (bearing to source, "Estimation of distance to source and its activity" for capable devices).
- Signal-strength / device-in-front indicator UI.
- Waypoint capture ("Add Waypoint") from the live dashboard, tagged with current GPS position and current reading.
- Device-specific settings screens (e.g. a dedicated PM1630 general-settings screen, a dedicated PM3100 mode-settings screen) layered on top of the shared dashboard.
- Per-device sound settings: sound frequency (1000–10000 Hz, custom dialog widget), sound level (0–100 slider), silence mode, "sound off by shake" gesture, and a distinct button-click sound toggle.
- Light-signaling (LED alarm) toggle, where hardware supports it.

### 2.3 Track (route recording)
- GPS track recording synced with the connected instrument's readings, independent Start/Continue/Stop controls.
- Recording granularity by **distance** ("every N meters") and/or **time** ("every H:MM:SS"), independently switchable, with a time+distance combined picker widget.
- Track-scoped dose-rate alarm thresholds (can reuse or override the dashboard thresholds) with per-threshold sound alarm toggle.
- Track summary stats stored per track: alarms count, date range, avg/max speed, distance, dose, min/max dose rate, min/max altitude, uploaded flag.
- Save Track dialog (name/date) at the end of a recording session.
- Auto-save waypoints during tracking (period configurable in seconds).

### 2.4 History
- Calendar/day-based history browser of stored measurement events, separate list of recorded Tracks per day.
- History detail views: per-waypoint info, per-track info (with an embedded mini-map and an embedded graph view of the track's readings over time).
- Graph viewer for plotting recorded dose/dose-rate/chemical values over time (`GraphViewer`, `GraphViewerChemical`).
- Filtering: date-from / date-to, device filter ("devices only"), "alarm only" filter, sortable by time / alarm / name.
- Deletion at three granularities: delete a day, delete a track, delete history-from-a-day — each with a confirmation dialog, and a guard that stops any in-progress track recording before a bulk history delete.
- Auto-load history toggle (pull device-stored history automatically on connect) plus a manual "Load History" action; recording-mode selector (Cyclic vs Linear) with a configurable recording interval.
- "Clear history after sync" toggle — device-side history is wiped after successful pull, to manage the instrument's limited onboard memory.
- Data export: to CSV, GPX, or KML, and a "share selected" flow that can generate/email a report of selected sets, or hand off to any installed share target.
- Import data from a previously exported file.
- Photo attachment to waypoints (camera capture or gallery pick, stored as a blob, shown on the waypoint detail screen).
- Google Drive integration (`PolismartDriveActivity` / `PolismartDriveService`, `PolismartDriveFragment`) for browsing/saving files.

### 2.5 World Map
- Map view of all recorded waypoints (Google Maps: Normal / Hybrid / Satellite view toggle, current-location button, track overlay button).
- World-map-scoped filter screen: filter by distance-from-current-location (with a preset distance picker: 1/2/3/5/10/15/20/30/50 km), by time window, "alert only" filter.
- Configurable indication thresholds for both dose and count-rate (cps), independent of the dashboard/track thresholds, used to color-code map markers.
- Tabular ("table") alternate view of the same waypoint set alongside the map view.
- Per-waypoint detail screen reachable from the map, including its own photo and mini-map sub-views.

### 2.6 Networked data-sharing integrations
Two optional cloud back-ends, each with its own login/status/logout preference screen:

- **NPNET** (Polimaster's own network service) — server-address field (defaults to a `polimaster.us` endpoint), login dialog, and an **upload-mode multi-select**: Auto Upload / Manually Upload / Auto-upload of alarm events only, each independently enable-able.
- **RadResponder** (a US national radiation-data-sharing network, `net.radresponder.androidsdk`) — OAuth2 authorization-code flow (`/oauth/authorize`, `/oauth/token`) against a configurable server address (defaults to a demo endpoint), REST endpoints for `/api/v2/fieldsurvey`, `/api/v2/observation`, `/api/v2/event`. Upload-mode selector (single-select here: auto / manual / auto-alarm), event selector, "send log" diagnostic action, "manually upload" action, separate demo vs production wrapper classes.
- Local notifications toggle, independent of either cloud integration, for on-device alarm alerts.
- App-level "feedback / report an issue" entry point and a bundled Terms & Conditions + Help document (localized HTML shown in an in-app WebView/PDF viewer).

### 2.7 App-wide settings
- Language selector: English, Japanese, Chinese, Russian, French, German — full localization, not just unit labels.
- Reading system (Imperial/Metric) and dose unit family (Roentgen/Sievert) as separate, independent preferences.
- Configurable start screen (which of the 5 modes opens on launch).
- Global "sound off by shake" gesture.
- Import data / reset history entry points live at the app level in addition to the History screen.

### 2.8 Cross-cutting / platform features
- Alarm handling as a first-class concept: a dedicated full-screen `AlarmActivity`, distinct messages for gamma-only, neutron-only, and combined gamma+neutron search-mode alarms, and a vibration + sound + visual (light) alarm stack.
- PDF viewer activity (for reports/help material).
- Location-permission and Bluetooth-permission just-in-time rationale dialogs, with a `LocationReceiver` for background location updates while tracking.
- File-provider-based sharing so exported files can be handed to email/other apps without exposing raw file paths.
- Crash reporting (Crashlytics) and product analytics (Firebase Analytics) baked in.

---

## 3. Multi-device architecture (the core reusable idea)

Two things drive the "one app, many instruments" design, and both are good patterns to replicate on your own terms:

**A. A device capability descriptor**, loaded per connected instrument, that tells the UI layer what to show/hide/enable. Structurally it looks like this (id and name are the two fields your app would map to its own device list; everything else is per-model capability flags/ranges):

```
Instrument (id, name, type: SPRD | EPD | DM | PRD | CGD | BRD)
 ├─ Synchronization: Yes/No           (does the instrument clock-sync with the phone)
 ├─ History: Yes/No, default/min/max, units, type   (onboard history buffer support)
 └─ Detector (name, type: gamma | neutron)  — one or more per instrument
     ├─ Search: Yes/No, min/max, units        (source-search mode)
     ├─ Sigma: Yes/No, default/min/max         (statistical error display)
     ├─ DoseRate: Yes/No, min/max, units
     ├─ Dose: Yes/No, min/max, units
     ├─ Spectrum: Yes/No, channel count, min/max   (spectroscopy)
     ├─ DoseRateThreshold1 / 2: Yes/No, default/min/max, units
     └─ DoseThreshold1 / 2: Yes/No, default/min/max, units
```

Device "type" codes observed: **SPRD** (spectroscopic personal radiation detector), **EPD** (electronic personal dosimeter — this is the PM1630's category), **DM** (dosimeter), **PRD** (personal radiation detector), **CGD** (chemical/gas detector), **BRD** (backpack radiation detector).

Full extracted capability table (35 device profiles) is in the appendix below — the PM1630 entry is highlighted since it's directly relevant to this project's existing work.

**B. A per-device protocol class**, selected by device ID/model, that implements the actual read/write wire format against a shared base interface. Observed protocol classes (one per device family, sharing a `BasePolismartProtocol` interface): default, PM1211, PM1401GNBT, PM1605BT, **PM1630**, PM1703BT and its GN/MA/MO variants (I/II, BT/MBT), PM1704A and its M/GN/GNM variants, PM1904A, PM2012MB, PM3100. This is a clean **strategy pattern** worth reusing: one interface, one implementation class per device family, selected at connect-time from the device's advertised identity.

### BLE transport details (for interoperability with owned hardware)
The app communicates over Bluetooth LE using a small set of custom 128-bit GATT UUIDs built from an ASCII pattern (hex-encoded text embedded in the UUID), alongside standard Device Information Service characteristics:

- Custom service/characteristic UUIDs decode to ASCII tags: `Instrument_data`, `Instrument_hist`, `Instrument_sett`, `Instrument_spec`, and a vendor tag `Polimaster_UUID`.
- Standard BLE Device Information Service (`0000180a-...`) and its Manufacturer Name (`2a29`), Model Number (`2a24`), Serial Number (`2a25`), Hardware Revision (`2a27`), Firmware Revision (`2a26`) characteristics, plus the standard Client Characteristic Configuration descriptor (`2902`) for enabling notifications.

If you own PM-series hardware and are building your own client for it (as the existing `pm1630_read.py` / `pm1630_decoded.py` scripts in this project already do), this GATT layout is the relevant starting point for locating the right service/characteristic to subscribe to. The actual byte-level packet format per device is implemented in each protocol class and wasn't extracted here — treat the existing PM1630 reverse-engineering work in this project as the source of truth for that device's wire format.

---

## 4. Local data model

The app's on-device SQLite schema (table names and columns only — this reflects generic domain concepts, not creative/proprietary logic, and is a reasonable starting model for your own schema):

- **Devices** — dvID, dvName, dvModel, dvModelID, dvSerial, dvHistoryUpgradeTime
- **Datablock** (a single measurement sample) — timestamp, dose, doseRate, doseTime, stat (sigma), statTime, flags, batteryLevel, markExcessDose/DoseRate/N (alarm flags), eventType, isHistory, countRateG/N/Search/FN, chemicalMode, statG/statN, foreign keys to Devices and SpectralData
- **SpectralData** — sdID, sdData (BLOB — raw spectrum bytes)
- **NuclideLibrary** — NlID, NlSubID, NlName, NlType, NlPriority (isotope identification reference data)
- **Results** — links a Device + NuclideLibrary entry (identification results)
- **Track** — trID, trName, alarms count, dateFrom/dateTo, speedAvg/Max, distance, dose, doseRateMin/Max, altitudeMax/Min, uploaded flag
- **Waypoint** — wpID, wpName, wpNamePhoto, altitude, latitude, longitude, variation, autosaved flag, uploaded flag, wpPhoto (BLOB), foreign keys to Datablock and Track

This maps cleanly onto the feature list above: Dashboard writes Datablock rows, Track groups them and adds route stats, History/World Map read them back with filters, and Search-mode identification results reference the NuclideLibrary.

---

## 5. Android permissions requested

Bluetooth (scan/connect/admin, legacy + Android 12+ runtime model), Camera, Internet + network state, Post Notifications, Read/Write external storage, Coarse + Fine location, Vibrate, Read phone state, Wake lock, Google account/services permissions, ad-ID/attribution permissions (for Firebase/Play services). Nothing unusual for a Bluetooth + GPS + cloud-sync app; a proprietary app covering the same ground would need essentially the same set minus whatever ad/analytics SDKs you choose not to include.

---

## 6. Suggestions for your own app (prioritization)

Given the PM1630 work already underway in this project, a reasonable build order that captures the most value first:

1. **Core loop**: BLE pairing → live Dashboard (dose + dose rate, thresholds, alarms) → local history storage. This alone matches the instrument's basic function.
2. **Track recording** with the distance/time-interval auto-waypoint pattern — valuable for field/survey use cases and not hard to add once GPS + history exist.
3. **History browser + export** (CSV at minimum; GPX/KML if mapping matters to your use case).
4. **World Map view** — can reuse whatever mapping library you prefer; Google Maps isn't required.
5. **Multi-device support via a capability descriptor + strategy-pattern protocol classes**, *only* if you actually intend to support more than the PM1630 — otherwise this is premature generalization and you can hard-code PM1630's known parameters (from the appendix below) instead.
6. **Cloud sync** (your own backend, not NPNET/RadResponder, unless you specifically want to interoperate with the RadResponder network for regulatory/reporting reasons) — last, since it's the least device-differentiated feature.

Skip or defer: the Chemical/gas channel path and Search-mode isotope identification (both are specific to detector hardware the PM1630 doesn't have, per its capability entry above — `Search: No`, `Spectrum: No`).

---

## Appendix A — Full device capability table (35 profiles)

| ID | Name | Type | Detectors | Clock Sync | Onboard History | Spectrum | Search Mode |
|---|---|---|---|---|---|---|---|
| 0x00000010 | PM1704A | SPRD | CsI(gamma) | No | Yes | Yes | Yes |
| 0x00000011 | PM1704A-M | SPRD | CsI+GM(gamma) | No | Yes | Yes | Yes |
| 0x00000012 | PM1704A-GN | SPRD | CsI(gamma), LiI(neutron) | No | Yes | Yes | Yes |
| 0x00000013 | PM1704A-GNM | SPRD | CsI+GM(gamma), LiI(neutron) | No | Yes | Yes | Yes |
| 0x00000004 | PM1904A | EPD | GM(gamma) | Yes | Yes | No | No |
| 0x00000002 | PM1605BT | DM | GM(gamma) | Yes | Yes | No | No |
| 0x00000020 | PM1703MO1BT | PRD | CsI+GM(gamma) | No | Yes | No | No |
| 0x00000021 | PM1401GNB | PRD | CsI(gamma), He-3(neutron) | No | Yes | No | Yes |
| 0x00000022 | PM1703BT | PRD | CsI(gamma), LiI(neutron) | No | Yes | No | Yes |
| 0x00000008 | PM1211 | DM | GM(gamma) | Yes | Yes | No | No |
| 0x00000006 | PM2012MB | CGD | GM(gamma) | No | Yes | No | No |
| 0x00000023 | PM1703MA-IIBT | PRD | CsI(gamma) | No | Yes | No | No |
| 0x00000024 | PM1703MA-IIMBT | PRD | CsI+GM(gamma) | No | Yes | No | No |
| 0x00000025 | PM1703GNA-IIBT | PRD | CsI(gamma), LiI(neutron) | No | Yes | No | Yes |
| 0x00000026 | PM1703GNA-IIMBT | PRD | CsI+GM(gamma), LiI(neutron) | No | Yes | No | Yes |
| 0x00000027 | PM1703MO-IIBT | PRD | CsI+GM(gamma) | No | Yes | No | No |
| 0x00000028 | PM1703MA-IIBT | PRD | CsI(gamma) | No | Yes | No | No |
| 0x00000029 | PM1703MA-IIMBT | PRD | CsI+GM(gamma) | No | Yes | No | No |
| 0x0000002A | PM1703GNA-IIBT | PRD | CsI(gamma), LiF(neutron) | No | Yes | No | Yes |
| 0x0000002B | PM1703GNA-IIMBT | PRD | CsI+GM(gamma), LiF(neutron) | No | Yes | No | Yes |
| 0x0000002C | PM1703MO-IIBT | PRD | CsI+GM(gamma) | No | Yes | No | No |
| 0x00000030 | PM1703MA-IIBT | PRD | CsI(gamma) | No | Yes | No | No |
| 0x00000031 | PM1703MA-IIMBT | PRD | CsI+GM(gamma) | No | Yes | No | No |
| 0x00000032 | PM1703GNA-IIBT | PRD | CsI(gamma), LiF(neutron) | No | Yes | No | Yes |
| 0x00000033 | PM1703GNA-IIMBT | PRD | CsI+GM(gamma), LiF(neutron) | No | Yes | No | Yes |
| 0x00000034 | PM1703MO-IIBT | PRD | CsI+GM(gamma) | No | Yes | No | No |
| 0x00000040 | PM3100G | BRD | CsI(gamma), GM(gamma) | Yes | Yes | Yes | Yes |
| 0x00000041 | PM3100G-Id | BRD | CsI(gamma), GM(gamma) | Yes | Yes | Yes | Yes |
| 0x00000042 | PM3100GN | BRD | CsI(gamma), LiF(neutron), GM(gamma) | Yes | Yes | Yes | Yes |
| 0x00000043 | PM3100GN-Id | BRD | CsI(gamma), LiF(neutron), GM(gamma) | Yes | Yes | Yes | Yes |
| 0x00000044 | PM3100G | BRD | CsI(gamma), GM(gamma) | Yes | Yes | Yes | Yes |
| 0x00000045 | PM3100G-Id | BRD | CsI(gamma), GM(gamma) | Yes | Yes | Yes | Yes |
| 0x00000046 | PM3100GN | BRD | CsI(gamma), LiF(neutron), GM(gamma) | Yes | Yes | Yes | Yes |
| 0x00000047 | PM3100GN-Id | BRD | CsI(gamma), LiF(neutron), GM(gamma) | Yes | Yes | Yes | Yes |
| **0x00000050** | **PM1630** | **EPD** | **GM(gamma)** | **Yes** | **Yes** | **No** | **No** |

### PM1630 detail (directly relevant to this project)

```
Instrument id=0x00000050, name="PM1630", type=EPD
  Synchronization: Yes
  History: default 60s, range 1–65535s
  Detector: GM, type=gamma
    Search: No        Sigma: No        Spectrum: No
    DoseRate: 1.0e-8 – 1.2 Sv/h
    Dose:     1.0e-8 – 10.0 Sv
    DoseRateThreshold1/2: default 1.0 Sv/h, range 1.0e-7 – 1.0 Sv/h
    DoseThreshold1/2:     default 10.0 Sv, range 1.0e-6 – 10.0 Sv
```

This confirms the PM1630 is a straightforward gamma-only electronic personal dosimeter: no search mode, no spectroscopy, no neutron channel — the full feature surface it needs from a companion app is Dashboard (dose + dose rate with 2-tier thresholds), History, Track, and clock sync. That's a meaningfully smaller build than the full Polismart feature set, which mostly exists to serve the higher-end SPRD/PRD/BRD instruments in the lineup.

## Appendix B — Screen inventory (Activities)

Start/connect screen, Dashboard (Dose/Dose Rate tabs), Alarm (full-screen), History (day list, filter, track detail, waypoint detail, sharing dialog), World Map (map + table views, filter, waypoint detail), Track settings, App Settings, PM1630 general settings, PM3100 mode settings, NPNET login/settings, RadResponder login/settings, Google Drive browser, PDF viewer, RadResponder OAuth login (WebView).

## Appendix C — Preference keys reference

The `res/xml/*.xml` preference screens define ~90 distinct preference keys covering app settings, per-device dashboard settings, track settings, history filters, world-map filters, and network settings. These map directly to the feature list in section 2 and are a useful checklist when designing your own settings schema — full key list available on request if useful for a settings-screen build-out.

---

*Source: static analysis of `Polismart_5.1.140` (APK decompiled with apktool for manifest/resources; strings and package structure only — no proprietary source code reproduced). Compiled 2026-08-21.*
