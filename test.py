#!/usr/bin/env python3
import time
import obd

PORT = "/dev/rfcomm0"        # update if different
TIMEOUT = 30                 # seconds when waiting for responses

print(f"Opening OBD port {PORT} ...")
connection = obd.OBD(portstr=PORT, fast=False, timeout=TIMEOUT, baudrate=9600)

print("Connection status:", connection.status())
print("Is connected?", connection.is_connected())

if not connection.is_connected():
    print("No connection. Check ignition power, pairing, or port.")
else:
    print("Adapter protocol:", connection.protocol_name())

    for cmd in [obd.commands.STATUS,
                obd.commands.PROTOCOL_NAME,
                obd.commands.SUPPORTED_PIDS_01_20]:
        print(f"\nQuerying {cmd.name} ...")
        response = connection.query(cmd)
        print("  Response:", response)

    print("\nListening for RPM for a few seconds...")
    for _ in range(5):
        response = connection.query(obd.commands.RPM)
        print("  RPM:", response.value)
        time.sleep(1)

connection.close()
print("Closed connection.")