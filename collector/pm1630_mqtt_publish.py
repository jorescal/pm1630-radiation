#!/usr/bin/env python3
"""
pm1630_mqtt_publish.py - PM1630 BLE -> MQTT collector (pi-pm1630, systemd unit pm1630-mqtt).

2026-09-26 changes vs the 2026-09-03 Pi port:
  - Exits on BLE disconnect or when no reading arrives for STALL_TIMEOUT_S, so
    systemd (Restart=always) restarts it. Previously a silent BLE drop left the
    process alive with no data for ~49 h (Sep 24 00:18 -> Sep 26).
  - Telemetry published with retain=True (PRTG snapshot-poll requirement).
  - dose_uSv published with 6 decimals (was 3) - background dose increments
    are ~0.00004 uSv/s, so 3 decimals hid them.
  - 'offline' status is published on every exit path.
Decode logic unchanged (spot-validated, not formally verified - see
pm1630-radiation-sensor.md Known limitations).
"""
import asyncio
import getpass
import json
import os
import struct
import sys
import time
from datetime import datetime, timezone

from bleak import BleakScanner, BleakClient
import paho.mqtt.client as mqtt

DEVICE_NAME_MATCH = "PM1630"
INSTRUMENT_DATA_UUID = "23496e73-7472-756d-656e-745f64617461"

MQTT_HOST = "{{LAB_BROKER_IP}}"
MQTT_PORT = 1883
MQTT_USER = "pm1630"
MQTT_TOPIC = "homelab/telemetry/pm1630/data"
MQTT_STATUS_TOPIC = "homelab/status/pm1630"

STALL_TIMEOUT_S = 30   # device notifies ~1/s; 30 s of silence = stalled link

state = {"last_rx": time.monotonic()}


def get_mqtt_password():
    # Under systemd, PM1630_MQTT_PASSWORD comes from EnvironmentFile.
    # Falls back to an interactive prompt only for manual foreground runs.
    password = os.environ.get("PM1630_MQTT_PASSWORD")
    if password:
        return password
    if not sys.stdin.isatty():
        sys.exit("PM1630_MQTT_PASSWORD is not set and there is no TTY to prompt - check EnvironmentFile.")
    return getpass.getpass("MQTT password for user 'pm1630': ")


def decode(data: bytearray):
    flags = data[3]
    dose_uSv = struct.unpack_from("<f", data, 4)[0] * 1_000_000
    dose_time_s = struct.unpack_from("<I", data, 8)[0]
    dose_rate_uSvh = struct.unpack_from("<f", data, 12)[0] * 1_000_000
    dr_statistics_pct = data[16]
    battery_pct = data[17]
    return {
        "flags": flags,
        "dose_uSv": round(dose_uSv, 6),
        "dose_time_s": dose_time_s,
        "dose_rate_uSvh": round(dose_rate_uSvh, 4),
        "dr_statistics_pct": dr_statistics_pct,
        "battery_pct": battery_pct,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def make_mqtt_client(password):
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pi-pm1630-collector")
    except AttributeError:
        client = mqtt.Client(client_id="pi-pm1630-collector")  # paho-mqtt < 2.0 fallback
    client.username_pw_set(MQTT_USER, password)
    # LWT: broker publishes this (retained) if we disconnect ungracefully
    client.will_set(MQTT_STATUS_TOPIC, payload="offline", qos=1, retain=True)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()
    client.publish(MQTT_STATUS_TOPIC, payload="online", qos=1, retain=True)
    return client


def handle_notification(mqtt_client, sender, data: bytearray):
    state["last_rx"] = time.monotonic()
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    d = decode(data)
    print(f"[{ts}] doseRate={d['dose_rate_uSvh']} uSv/h  dose={d['dose_uSv']} uSv  "
          f"battery={d['battery_pct']}%  stat=±{d['dr_statistics_pct']}%  flags={d['flags']:#04x}")
    mqtt_client.publish(MQTT_TOPIC, json.dumps(d), qos=0, retain=True)


def publish_offline(mqtt_client):
    try:
        info = mqtt_client.publish(MQTT_STATUS_TOPIC, payload="offline", qos=1, retain=True)
        info.wait_for_publish(timeout=5)
    except Exception as e:  # broker unreachable etc. - LWT covers it
        print(f"Could not publish offline status: {e}")
    mqtt_client.loop_stop()
    mqtt_client.disconnect()


async def main():
    mqtt_client = make_mqtt_client(get_mqtt_password())
    try:
        print(f"Scanning for a device matching '{DEVICE_NAME_MATCH}'...")
        device = await BleakScanner.find_device_by_filter(
            lambda d, ad: d.name and DEVICE_NAME_MATCH in d.name,
            timeout=15.0,
        )
        if device is None:
            print("Device not found - exiting for systemd restart.")
            return 1

        print(f"Found: {device.name} ({device.address})")
        disconnected = asyncio.Event()

        def on_disconnect(_client):
            print("BLE disconnected - exiting for systemd restart.")
            disconnected.set()

        async with BleakClient(device.address, disconnected_callback=on_disconnect) as client:
            print(f"Connected: {client.is_connected}\n")
            state["last_rx"] = time.monotonic()
            await client.start_notify(
                INSTRUMENT_DATA_UUID,
                lambda sender, data: handle_notification(mqtt_client, sender, data),
            )
            while not disconnected.is_set():
                await asyncio.sleep(1)
                idle = time.monotonic() - state["last_rx"]
                if idle > STALL_TIMEOUT_S:
                    print(f"No data for {idle:.0f} s - exiting for systemd restart.")
                    break
            if client.is_connected:
                try:
                    await client.stop_notify(INSTRUMENT_DATA_UUID)
                except Exception:
                    pass
        return 1  # any exit from the read loop is abnormal for a long-running collector
    finally:
        publish_offline(mqtt_client)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
