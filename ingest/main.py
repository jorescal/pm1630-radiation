"""
pm1630-ingest - Cloud Run service (project {{GCP_PROJECT_ID}}, us-central1).

Receives 60 s batches of PM1630 readings from collectors (gcp-bridge in the lab now,
phones later), then:
  - publishes the full batch to Pub/Sub `pm1630-readings` -> BigQuery pm1630.raw_batches
  - updates Firestore (database `pm1630`): devices/{id} live state + minutes/{yyyyMMddHHmm}
  - evaluates thresholds from Firestore config/thresholds (none configured = no radiation alarms)
  - writes alarms to Firestore alarms/{id} and publishes them to `pm1630-alarms`

Auth: per-collector bearer token from Secret Manager (env TOKEN_<COLLECTOR>), each collector
limited to the device ids in env DEVICES_<COLLECTOR> (separator "|").
Decode values are spot-validated, not calibrated - see pm1630-radiation-sensor.md.
"""
import hmac
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request
from google.cloud import firestore, pubsub_v1

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pm1630-ingest")

PROJECT = os.environ["GCP_PROJECT"]
FS_DB = os.environ.get("FIRESTORE_DB", "pm1630")
TOPIC_READINGS = os.environ.get("TOPIC_READINGS", "pm1630-readings")
TOPIC_ALARMS = os.environ.get("TOPIC_ALARMS", "pm1630-alarms")
MAX_SKEW_S = int(os.environ.get("MAX_SKEW_S", "300"))
MINUTE_TTL_DAYS = int(os.environ.get("MINUTE_TTL_DAYS", "30"))

# TOKEN_PI_PM1630=<secret>  ->  collector "pi-pm1630"
TOKENS = {k[len("TOKEN_"):].lower().replace("_", "-"): v.strip()
          for k, v in os.environ.items() if k.startswith("TOKEN_") and v.strip()}
ALLOWED = {c: {d.strip() for d in os.environ.get("DEVICES_" + c.upper().replace("-", "_"), "").split("|") if d.strip()}
           for c in TOKENS}

app = Flask(__name__)
publisher = pubsub_v1.PublisherClient()
fs = firestore.Client(project=PROJECT, database=FS_DB)
TOPIC_R = publisher.topic_path(PROJECT, TOPIC_READINGS)
TOPIC_A = publisher.topic_path(PROJECT, TOPIC_ALARMS)

_threshold_cache = {"at": 0.0, "doc": {}}


def authenticate():
    h = request.headers.get("Authorization", "")
    if not h.startswith("Bearer "):
        return None
    tok = h[7:].strip()
    for collector, secret in TOKENS.items():
        if hmac.compare_digest(tok.encode(), secret.encode()):
            return collector
    return None


def to_dt(v):
    if isinstance(v, (int, float)):
        if v > 1e14:
            return datetime.fromtimestamp(v / 1e6, timezone.utc)
        if v > 1e11:
            return datetime.fromtimestamp(v / 1e3, timezone.utc)
        return datetime.fromtimestamp(v, timezone.utc)
    dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def us(dt):
    return int(dt.timestamp() * 1_000_000)


def thresholds_for(device):
    """config/thresholds: {"default": {...}, "devices": {"<id>": {...}}}.
    Keys: rate_uSvh_max (number). Missing/None = no radiation alarm (never invented here)."""
    if time.time() - _threshold_cache["at"] > 60:
        snap = fs.collection("config").document("thresholds").get()
        _threshold_cache["doc"] = (snap.to_dict() or {}) if snap.exists else {}
        _threshold_cache["at"] = time.time()
    doc = _threshold_cache["doc"]
    out = dict(doc.get("default") or {})
    out.update((doc.get("devices") or {}).get(device) or {})
    return out


def raise_alarm(device, collector, a_type, ts, value=None, threshold=None, details=None):
    alarm_id = uuid.uuid4().hex
    rec = {"alarm_id": alarm_id, "ts": ts, "device_id": device, "type": a_type, "value": value,
           "threshold": threshold, "source": collector, "details": details}
    fs.collection("alarms").document(alarm_id).set({**rec, "acknowledged": False, "acknowledged_by": None})
    publisher.publish(TOPIC_A, json.dumps({**rec, "ts": us(ts)}).encode()).result(timeout=30)
    log.warning("ALARM %s device=%s value=%s threshold=%s", a_type, device, value, threshold)
    return alarm_id


@app.get("/health")   # note: Cloud Run's front end reserves /healthz, so it never reaches the app
def health():
    return jsonify(ok=True)


@app.post("/v1/batch")
def batch():
    collector = authenticate()
    if not collector:
        return jsonify(error="unauthorized"), 401
    body = request.get_json(silent=True) or {}
    device = str(body.get("device_id", "")).strip()
    if not device or device not in ALLOWED.get(collector, set()):
        return jsonify(error="device not allowed for this collector"), 403
    raw = body.get("readings")
    if not isinstance(raw, list) or not raw or len(raw) > 3600:
        return jsonify(error="readings must be a non-empty list (max 3600)"), 400

    now = datetime.now(timezone.utc)
    rows = []
    for r in raw:
        try:
            rows.append({
                "ts": to_dt(r.get("ts", r.get("timestamp"))),
                "dose_rate_uSvh": float(r["dose_rate_uSvh"]),
                "dose_uSv": float(r["dose_uSv"]),
                "dose_time_s": int(r.get("dose_time_s", 0)),
                "dr_statistics_pct": int(r.get("dr_statistics_pct", 0)),
                "battery_pct": int(r.get("battery_pct", 0)),
                "flags": int(r.get("flags", 0)),
            })
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    if not rows:
        return jsonify(error="no valid readings"), 400
    rows.sort(key=lambda x: x["ts"])
    first, last = rows[0], rows[-1]

    # Bridge stamps received_at with its own (NTP) clock; a big gap means the collector clock is off.
    try:
        received_at = to_dt(body["received_at"]) if body.get("received_at") else now
    except (TypeError, ValueError):
        received_at = now
    clock_suspect = abs((received_at - last["ts"]).total_seconds()) > MAX_SKEW_S

    dev_ref = fs.collection("devices").document(device)
    prev = dev_ref.get().to_dict() or {}
    prev_dose, prev_ts = prev.get("last_dose_uSv"), prev.get("last_reading_ts")
    if prev_dose is not None and prev_ts is not None and prev_ts < first["ts"] and last["dose_uSv"] >= prev_dose:
        increment = last["dose_uSv"] - prev_dose          # covers gaps between batches
    else:
        increment = max(0.0, last["dose_uSv"] - first["dose_uSv"])  # first batch / reset / replay
    rates = [r["dose_rate_uSvh"] for r in rows]
    summary = {
        "rate_min_uSvh": min(rates), "rate_mean_uSvh": sum(rates) / len(rates), "rate_max_uSvh": max(rates),
        "dose_increment_uSv": increment, "n_readings": len(rows),
    }

    msg = {"batch_ts": us(last["ts"]), "received_ts": us(now), "device_id": device, "source": collector,
           "readings": [{**r, "ts": us(r["ts"])} for r in rows], **summary}
    publisher.publish(TOPIC_R, json.dumps(msg).encode()).result(timeout=30)

    dev_ref.collection("minutes").document(last["ts"].strftime("%Y%m%d%H%M")).set({
        **summary, "ts": last["ts"], "dose_total_uSv": last["dose_uSv"],
        "battery_pct": last["battery_pct"], "dr_statistics_pct": last["dr_statistics_pct"],
        "clock_suspect": clock_suspect, "expire_at": last["ts"] + timedelta(days=MINUTE_TTL_DAYS),
    })

    is_newest = prev_ts is None or last["ts"] >= prev_ts   # replayed old batches don't overwrite live state
    alarm_state = prev.get("alarm_state", "normal")
    alarm_id = None
    th = thresholds_for(device).get("rate_uSvh_max")
    if is_newest and th is not None:
        if summary["rate_max_uSvh"] > float(th) and alarm_state != "rate_high":
            alarm_id = raise_alarm(device, collector, "rate_high", last["ts"], summary["rate_max_uSvh"], float(th))
            alarm_state = "rate_high"
        elif summary["rate_max_uSvh"] <= float(th) and alarm_state == "rate_high":
            raise_alarm(device, collector, "rate_cleared", last["ts"], summary["rate_max_uSvh"], float(th))
            alarm_state = "normal"
    if is_newest:
        dev_ref.set({
            "last_seen": now, "last_reading_ts": last["ts"], "last_dose_uSv": last["dose_uSv"],
            "last_rate_uSvh": last["dose_rate_uSvh"], "rate_mean_1m_uSvh": summary["rate_mean_uSvh"],
            "battery_pct": last["battery_pct"], "dr_statistics_pct": last["dr_statistics_pct"],
            "collector": collector, "status": "online", "clock_suspect": clock_suspect, "alarm_state": alarm_state,
        }, merge=True)

    return jsonify(ok=True, device_id=device, n=len(rows), dose_increment_uSv=increment,
                   clock_suspect=clock_suspect, alarm_id=alarm_id, replayed=not is_newest)


@app.post("/v1/event")
def event():
    collector = authenticate()
    if not collector:
        return jsonify(error="unauthorized"), 401
    body = request.get_json(silent=True) or {}
    device = str(body.get("device_id", "")).strip()
    e_type = str(body.get("type", "")).strip()
    if not device or device not in ALLOWED.get(collector, set()):
        return jsonify(error="device not allowed for this collector"), 403
    if e_type not in ("collector_online", "collector_offline"):
        return jsonify(error="unknown event type"), 400
    try:
        ts = to_dt(body["ts"]) if body.get("ts") else datetime.now(timezone.utc)
    except (TypeError, ValueError):
        ts = datetime.now(timezone.utc)
    status = "online" if e_type == "collector_online" else "offline"
    fs.collection("devices").document(device).set({"status": status, "status_ts": ts, "collector": collector}, merge=True)
    alarm_id = raise_alarm(device, collector, e_type, ts, details=body.get("details")) if status == "offline" else None
    return jsonify(ok=True, status=status, alarm_id=alarm_id)
