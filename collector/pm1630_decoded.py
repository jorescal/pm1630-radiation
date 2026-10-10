import asyncio
import struct
from datetime import datetime
from bleak import BleakScanner, BleakClient

DEVICE_NAME_MATCH = "PM1630"
INSTRUMENT_DATA_UUID = "23496e73-7472-756d-656e-745f64617461"

def decode(data: bytearray):
    flags = data[3]
    dose_uSv = struct.unpack_from("<f", data, 4)[0] * 1_000_000
    dose_time_s = struct.unpack_from("<I", data, 8)[0]
    dose_rate_uSvh = struct.unpack_from("<f", data, 12)[0] * 1_000_000
    dr_statistics_pct = data[16]
    battery_pct = data[17]
    return {
        "flags": flags,
        "dose_uSv": round(dose_uSv, 3),
        "dose_time_s": dose_time_s,
        "dose_rate_uSvh": round(dose_rate_uSvh, 4),
        "dr_statistics_pct": dr_statistics_pct,
        "battery_pct": battery_pct,
    }

def handle_notification(sender, data: bytearray):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    d = decode(data)
    print(f"[{ts}] doseRate={d['dose_rate_uSvh']} uSv/h  dose={d['dose_uSv']} uSv  "
          f"battery={d['battery_pct']}%  stat=\u00b1{d['dr_statistics_pct']}%  flags={d['flags']:#04x}")

async def main():
    print(f"Scanning for a device matching '{DEVICE_NAME_MATCH}'...")
    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: d.name and DEVICE_NAME_MATCH in d.name,
        timeout=15.0,
    )
    if device is None:
        print("Device not found.")
        return

    print(f"Found: {device.name} ({device.address})")

    async with BleakClient(device) as client:
        print(f"Connected: {client.is_connected}\n")
        await client.start_notify(INSTRUMENT_DATA_UUID, handle_notification)
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await client.stop_notify(INSTRUMENT_DATA_UUID)

asyncio.run(main())
