# Supplied servo scripts

These are the two Python scripts Yusuf sent by message, saved under descriptive filenames. Their contents were copied without changing their control logic. They are reference tools, not connected to the handoff app, and the app remains in simulation mode.

| File | Purpose |
|---|---|
| `servo_test.py` | Discover servo IDs and read status; also contains movement tests. |
| `multi_servo.py` | Queue and send position commands to several servos. |

Both scripts require the `feetech_tuna` Python module and a serial port argument. A copy of that library is now included under `backend/feetech_tuna`. The scripts currently target IDs 1–8, while the glove design uses IDs 1–6. They accept raw 0–4095 positions without the glove's measured mechanical limits. Do not use their movement commands on a worn or tensioned glove.

The app's **Devices** page now offers a read-only status check for IDs 1–6. If you need to run the original terminal script, stop the HandOff API first so both programs do not open the same serial port. From the project root, use:

```bash
cd hardware/reference
PYTHONPATH=../../backend ../../backend/.venv/bin/python servo_test.py /dev/cu.YOUR_PORT
```

At the `servo-test>` prompt, use `status` or `status 1`, then `exit`. Find the Mac serial port with `ls /dev/cu.usb*` after connecting the controller. Opening this script pings and reads servo status; it does not send a movement command until one is entered.

The scripts are saved so their communication code can be reviewed and adapted behind handoff's future hardware adapter. Physical movement still needs verified ID mapping, measured limits for all six servos, and a tested independent power stop.
