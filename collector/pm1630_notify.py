import asyncio
from datetime import datetime
from bleak import BleakScanner, BleakClient

DEVICE_NAME_MATCH = "PM1630"
INSTRUMENT_DATA_UUID = "23496e73-7472-756d-656e-745f64617461"

def handle_notification(sender, data: bytearray):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}] {data.hex()}  ({len(data)} bytes)")

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
        print(f"Connected: {client.is_connected}")
        print("Subscribing to Instrument_data. Note the PM1630's own screen reading as values come in. Ctrl+C to stop.\n")
        await client.start_notify(INSTRUMENT_DATA_UUID, handle_notification)
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await client.stop_notify(INSTRUMENT_DATA_UUID)

asyncio.run(main())