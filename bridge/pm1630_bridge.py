#!/usr/bin/env python3
"""
pm1630_bridge.py - lab -> GCP bridge for the PM1630 (runs in the gcp-bridge LXC, systemd pm1630-bridge).

Subscribes (read-only MQTT user) to the collector's topics on the homelab broker and forwards to
Cloud Run pm1630-ingest over outbound HTTPS:
  - every BATCH_SECONDS: one POST /v1/batch with all readings received in that window
  - collector online/offline (LWT status topic) and "no data" stalls: POST /v1/event immediately
Everything goes through a local SQLite queue first, so internet/GCP outages are buffered and
replayed oldest-first. Meraki forwarding (FORWARD_MERAKI) is reserved for phase P7 and is off.
"""
import json
import logging
import os
import signal
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pm1630-bridge")

MQTT_HOST = os.environ.get("MQTT_HOST", "{{LAB_BROKER_IP}}")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "gcp-bridge")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
TOPIC_DATA = os.environ.get("TOPIC_DATA", "homelab/telemetry/pm1630/data")
TOPIC_STATUS = os.environ.get("TOPIC_STATUS", "homelab/status/pm1630")
INGEST_URL = os.environ.get("INGEST_URL", "").rstrip("/")
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "")
DEVICE_ID = os.environ.get("DEVICE_ID", "pm1630-{{PM1630_SERIAL}}")
BATCH_SECONDS = int(os.environ.get("BATCH_SECONDS", "60"))
STALE_SECONDS = int(os.environ.get("STALE_SECONDS", "180"))
QUEUE_DB = os.environ.get("QUEUE_DB", "/var/lib/pm1630-bridge/queue.db")
QUEUE_MAX = int(os.environ.get("QUEUE_MAX", "10080"))  # ~7 days of 60 s batches
FORWARD_MERAKI = os.environ.get("FORWARD_MERAKI", "false").lower() == "true"  # P7, not implemented yet


def utcnow_iso():
    return datetime.now(timezone.utc).isoformat()


class Queue:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.db.execute("CREATE TABLE IF NOT EXISTS q (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, body TEXT, created TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS dead (id INTEGER PRIMARY KEY, kind TEXT, body TEXT, created TEXT, status INTEGER, error TEXT)")
        self.db.commit()

    def put(self, kind, body):
        with self.lock:
            self.db.execute("INSERT INTO q (kind, body, created) VALUES (?,?,?)", (kind, json.dumps(body), utcnow_iso()))
            n = self.db.execute("SELECT COUNT(*) FROM q").fetchone()[0]
            if n > QUEUE_MAX:
                self.db.execute("DELETE FROM q WHERE id IN (SELECT id FROM q ORDER BY id LIMIT ?)", (n - QUEUE_MAX,))
                log.warning("queue over %d - dropped %d oldest", QUEUE_MAX, n - QUEUE_MAX)
            self.db.commit()

    def peek(self):
        with self.lock:
            return self.db.execute("SELECT id, kind, body, created FROM q ORDER BY id LIMIT 1").fetchone()

    def done(self, item_id):
        with self.lock:
            self.db.execute("DELETE FROM q WHERE id=?", (item_id,))
            self.db.commit()

    def bury(self, row, status, error):
        with self.lock:
            self.db.execute("INSERT INTO dead VALUES (?,?,?,?,?,?)", (*row, status, error[:500]))
            self.db.execute("DELETE FROM q WHERE id=?", (row[0],))
            self.db.commit()

    def size(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM q").fetchone()[0]


class Bridge:
    def __init__(self, queue, session=None):
        self.q = queue
        self.http = session or requests.Session()
        self.lock = threading.Lock()
        self.buffer = []
        self.last_ts = None           # newest reading timestamp forwarded (dedupe)
        self.last_rx = None           # monotonic time of last live reading
        self.collector_status = None  # last status seen on the LWT topic
        self.stalled = False
        self.backoff_until = 0.0
        self.backoff = 5.0

    # ---- MQTT side -------------------------------------------------------
    def on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", "replace").strip()
            if msg.topic == TOPIC_STATUS:
                self.handle_status(payload, retained=bool(msg.retain))
            elif msg.topic == TOPIC_DATA:
                if msg.retain:
                    return  # retained = last reading before we connected; already forwarded or stale
                self.handle_reading(json.loads(payload))
        except Exception as e:  # never let one bad message kill the MQTT thread
            log.error("bad message on %s: %s", msg.topic, e)

    def handle_reading(self, d):
        ts = d.get("timestamp") or d.get("ts")
        if not ts:
            return
        with self.lock:
            if self.last_ts is not None and ts <= self.last_ts:
                return
            self.last_ts = ts
            self.last_rx = time.monotonic()
            self.buffer.append({
                "ts": ts, "dose_rate_uSvh": d.get("dose_rate_uSvh"), "dose_uSv": d.get("dose_uSv"),
                "dose_time_s": d.get("dose_time_s", 0), "dr_statistics_pct": d.get("dr_statistics_pct", 0),
                "battery_pct": d.get("battery_pct", 0), "flags": d.get("flags", 0),
            })
            if self.stalled:
                self.stalled = False
                self.q.put("event", {"device_id": DEVICE_ID, "type": "collector_online", "ts": utcnow_iso(),
                                     "details": "data resumed"})

    def handle_status(self, payload, retained):
        status = payload.lower()
        if status not in ("online", "offline") or status == self.collector_status:
            return
        first = self.collector_status is None
        self.collector_status = status
        if first and retained and status == "online":
            return  # bridge (re)start: nothing changed as far as the backend knows
        self.q.put("event", {"device_id": DEVICE_ID, "type": f"collector_{status}", "ts": utcnow_iso(),
                             "details": "LWT status topic" + (" (retained)" if retained else "")})
        log.info("collector status -> %s", status)

    # ---- timers ----------------------------------------------------------
    def flush_batch(self):
        with self.lock:
            readings, self.buffer = self.buffer, []
        if readings:
            self.q.put("batch", {"device_id": DEVICE_ID, "received_at": utcnow_iso(), "readings": readings})
        return len(readings)

    def check_stale(self):
        with self.lock:
            if self.stalled or self.last_rx is None:
                return
            if time.monotonic() - self.last_rx > STALE_SECONDS:
                self.stalled = True
                self.q.put("event", {"device_id": DEVICE_ID, "type": "collector_offline", "ts": utcnow_iso(),
                                     "details": f"no readings for > {STALE_SECONDS} s"})
                log.warning("no readings for > %d s - stall event queued", STALE_SECONDS)

    # ---- HTTP side -------------------------------------------------------
    def send_one(self):
        if time.monotonic() < self.backoff_until:
            return False
        row = self.q.peek()
        if not row:
            return False
        item_id, kind, body, _ = row
        url = f"{INGEST_URL}/v1/{'batch' if kind == 'batch' else 'event'}"
        try:
            r = self.http.post(url, data=body, timeout=20, headers={
                "Authorization": f"Bearer {DEVICE_TOKEN}", "Content-Type": "application/json"})
        except requests.RequestException as e:
            return self._fail(f"network: {e}")
        if 200 <= r.status_code < 300:
            self.q.done(item_id)
            self.backoff = 5.0
            if kind == "batch":
                info = r.json()
                log.info("batch sent n=%s inc=%.6f uSv%s%s queue=%d", info.get("n"),
                         info.get("dose_increment_uSv") or 0.0,
                         " REPLAY" if info.get("replayed") else "",
                         " CLOCK?" if info.get("clock_suspect") else "", self.q.size())
            else:
                log.info("event sent %s", json.loads(body).get("type"))
            return True
        if r.status_code in (401, 403, 400, 413):
            log.error("rejected %s (%d): %s - moved to dead table", kind, r.status_code, r.text[:200])
            self.q.bury(row, r.status_code, r.text)
            return True
        return self._fail(f"HTTP {r.status_code}")

    def _fail(self, why):
        self.backoff_until = time.monotonic() + self.backoff
        log.warning("send failed (%s) - retry in %.0f s, queue=%d", why, self.backoff, self.q.size())
        self.backoff = min(self.backoff * 2, 300.0)
        return False


def main():
    missing = [k for k in ("MQTT_PASSWORD", "INGEST_URL", "DEVICE_TOKEN") if not os.environ.get(k)]
    if missing:
        sys.exit(f"missing environment: {', '.join(missing)} (check the EnvironmentFile)")
    if FORWARD_MERAKI:
        log.warning("FORWARD_MERAKI=true but Meraki forwarding is not implemented yet (phase P7) - ignoring")

    bridge = Bridge(Queue(QUEUE_DB))
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="gcp-bridge")
    except AttributeError:
        client = mqtt.Client(client_id="gcp-bridge")
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    client.on_message = bridge.on_message
    client.on_connect = lambda c, u, f, rc, p=None: (log.info("MQTT connected rc=%s", rc),
                                                      c.subscribe([(TOPIC_DATA, 0), (TOPIC_STATUS, 1)]))
    client.reconnect_delay_set(min_delay=2, max_delay=60)
    client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: stop.set())
    signal.signal(signal.SIGINT, lambda *a: stop.set())
    log.info("bridge up: %s:%d -> %s device=%s batch=%ds queue=%d", MQTT_HOST, MQTT_PORT, INGEST_URL,
             DEVICE_ID, BATCH_SECONDS, bridge.q.size())

    next_flush = time.monotonic() + BATCH_SECONDS
    while not stop.is_set():
        if time.monotonic() >= next_flush:
            bridge.flush_batch()
            bridge.check_stale()
            next_flush += BATCH_SECONDS
        while bridge.send_one():   # drain the queue (oldest first) while sends succeed
            pass
        stop.wait(1.0)

    bridge.flush_batch()  # keep the partial window in the queue for next start
    client.loop_stop()
    client.disconnect()
    log.info("bridge stopped, queue=%d", bridge.q.size())


if __name__ == "__main__":
    main()
