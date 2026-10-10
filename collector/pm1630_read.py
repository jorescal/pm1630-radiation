import asyncio
from bleak import BleakScanner, BleakClient

DEVICE_NAME_MATCH = "PM1630"

async def main():
    print(f"Scanning for a device matching '{DEVICE_NAME_MATCH}'...")
    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: d.name and DEVICE_NAME_MATCH in d.name,
        timeout=15.0,
    )
    if device is None:
        print("Device not found. Make sure it's powered on and advertising.")
        return

    print(f"Found: {device.name} ({device.address})")

    async with BleakClient(device) as client:
        print(f"Connected: {client.is_connected}")
        for service in client.services:
            print(f"\nService: {service.uuid} ({service.description})")
            for char in service.characteristics:
                props = ",".join(char.properties)
                print(f"  Char: {char.uuid} [{props}]")
                if "read" in char.properties:
                    try:
                        value = await client.read_gatt_char(char.uuid)
                        print(f"    Value (bytes): {value}")
                        print(f"    Value (hex):   {value.hex()}")
                    except Exception as e:
                        print(f"    Read failed: {e}")

asyncio.run(main())