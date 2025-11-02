# pyOBDui

pyOBDui is a Python application for real-time vehicle monitoring that combines
`python-OBD` for data acquisition, SQLite for persistence, and a PyQt-based UI.

## Project Structure

```
pyOBDui/
├── src/
│   └── pyobdui/
│       ├── configs/
│       ├── obd_connection/
│       ├── ui/
│       ├── db/
│       └── common/
├── docs/
└── data/
```

## Getting Started

1. Ensure Python 3.10+ is installed.
2. Create a virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```

3. Prepare your Bluetooth ELM327 adapter using the steps in [Bluetooth Setup](#bluetooth-setup-linux).
4. Run the application entry point once implemented:

   ```bash
   python -m pyobdui.main
   ```

5. Follow the CLI prompts to select or create a vehicle configuration. When
   creating a configuration, the tool will attempt to probe your adapter for
   supported PIDs and persist the result as a JSON file under `data/configs/`.
6. After configuration, the PyQt monitoring UI launches. Close the window to
   shut down the telemetry poller and background services.

## Bluetooth Setup (Linux)

These steps assume a typical USB Bluetooth controller and a classic ELM327
adapter. Adjust the MAC address for your device.

1. Pair and trust the adapter:

   ```bash
   bluetoothctl
   [bluetooth]# scan on                          # wait until you see the adapter
   [bluetooth]# pair 00:1D:A5:68:98:8A           # replace with your adapter MAC
   [bluetooth]# trust 00:1D:A5:68:98:8A
   [bluetooth]# connect 00:1D:A5:68:98:8A        # optional; some adapters disconnect automatically
   [bluetooth]# quit
   ```

2. Bind the serial port (RFCOMM). Channel 1 works for most adapters—scan with
   `sdptool browse <MAC>` if yours differs:

   ```bash
   sudo rfcomm release 0            # ignore the error if it is not bound yet
   sudo rfcomm bind 0 00:1D:A5:68:98:8A 1
   ls -l /dev/rfcomm0               # confirms the device exists
   ```

   Re-run the bind command after every reboot or if the adapter is unplugged.

3. Grant your user access to the serial device:

   ```bash
   sudo usermod -a -G dialout $USER   # add once; log out/in afterwards
   newgrp dialout                     # or run this in the current shell
   sudo chown $USER:$USER /dev/rfcomm0   # optional per-session ownership tweak
   ```

4. Verify python-OBD can see the adapter:

   ```bash
   python - <<'PY'
   import obd
   print(obd.scan_serial())
   PY
   ```

   The list should include `/dev/rfcomm0`. If it does not, repeat the binding
   step or ensure no other device (e.g. a phone) is connected to the adapter.

See additional documentation in this directory for setup guidance, architecture
overviews, and troubleshooting.
