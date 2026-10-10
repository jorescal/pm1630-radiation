#!/usr/bin/env python3
"""
meraki_inventory.py - READ-ONLY Meraki Dashboard API inventory for the PM1630
positioning design (see pm1630-radiation-sensor.md, "Meraki Integration
Architecture" section).

Makes GET requests only. Prints, per wireless network:
  - each MR AP: name, model, MAC, firmware, lat/lng, floorPlanId, tags
  - network Bluetooth settings (scanningEnabled / eslEnabled)
  - configured MQTT brokers (name/host/port/security mode only - no secrets)
  - Location & Scanning settings (analytics / Scanning API)
  - floor plans (name + id)
  - Bluetooth clients seen in the last 24 h, filtered for "PM1630"

Use an API key from a READ-ONLY dashboard admin account.
Requires: python3, requests  (pip install requests)
"""
import getpass
import json
import time

import requests

BASE = "https://api.meraki.com/api/v1"
KEY = getpass.getpass("Meraki API key (read-only admin recommended): ")

S = requests.Session()
S.headers.update({"Authorization": f"Bearer {KEY}", "Accept": "application/json"})


def get(path, **params):
    """GET with basic 429/Retry-After handling. Returns parsed JSON, or
    {'_error': <status>} for non-2xx responses so one missing feature
    doesn't abort the whole inventory."""
    for _ in range(6):
        r = S.get(BASE + path, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", "2")))
            continue
        if not r.ok:
            return {"_error": r.status_code, "path": path}
        return r.json()
    return {"_error": "rate-limited", "path": path}


def as_list(x):
    return x if isinstance(x, list) else []


orgs = get("/organizations")
if isinstance(orgs, dict):
    raise SystemExit(f"Could not list organizations: {orgs}")

for org in orgs:
    print(f"\n=== ORG {org['name']} ({org['id']})")
    for net in as_list(get(f"/organizations/{org['id']}/networks", perPage=1000)):
        if "wireless" not in net.get("productTypes", []):
            continue
        nid = net["id"]
        print(f"\n--- NETWORK {net['name']} ({nid})")

        for d in as_list(get(f"/networks/{nid}/devices")):
            if not d.get("model", "").startswith("MR"):
                continue
            print(
                f"  AP name={d.get('name')!r} model={d.get('model')} mac={d.get('mac')} "
                f"fw={d.get('firmware')} lat={d.get('lat')} lng={d.get('lng')} "
                f"floorPlanId={d.get('floorPlanId')} tags={d.get('tags')}"
            )

        print("  bluetooth settings:", json.dumps(get(f"/networks/{nid}/wireless/bluetooth/settings")))

        brokers = get(f"/networks/{nid}/mqttBrokers")
        if isinstance(brokers, dict):
            print("  mqtt brokers:", json.dumps(brokers))
        for b in as_list(brokers):
            sec = b.get("security") or {}
            mode = sec.get("mode") if isinstance(sec, dict) else sec
            print(f"  mqtt broker: name={b.get('name')} host={b.get('host')} port={b.get('port')} security={mode}")

        print("  locationScanning:", json.dumps(get(f"/networks/{nid}/locationScanning")))

        fps = get(f"/networks/{nid}/floorPlans")
        print("  floor plans:", [(f.get("name"), f.get("floorPlanId")) for f in as_list(fps)] or fps)

        bt = get(f"/networks/{nid}/bluetoothClients", timespan=86400, perPage=1000)
        if isinstance(bt, dict):
            print("  bluetoothClients:", json.dumps(bt))
            continue
        hits = [c for c in bt if "PM1630" in f"{c.get('deviceName') or ''} {c.get('name') or ''}".upper()]
        print(f"  bluetoothClients (24h, first page): {len(bt)} total, PM1630 matches: {len(hits)}")
        for c in hits:
            print("    ", json.dumps({k: c.get(k) for k in
                                      ("mac", "deviceName", "name", "manufacturer", "lastSeen", "seenByDeviceMac")}))
