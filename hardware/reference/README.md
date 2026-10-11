# Supplied servo scripts

These are the two Python scripts Yusuf sent by message, saved under descriptive filenames. Their contents were copied without changing their control logic. They are reference tools, not connected to the handoff app, and the app remains in simulation mode.

| File | Purpose |
|---|---|
| `servo_test.py` | Discover servo IDs and read status; also contains movement tests. |
| `multi_servo.py` | Queue and send position commands to several servos. |

Both scripts require the `feetech_tuna` Python module and a serial port argument. A copy of that library is included under `backend/feetech_tuna`. The scripts accept raw target counts from 0–4000; the servos' configured angle registers read 0–4095. That numeric cap does not establish safe tendon travel. Use the multi-servo command format `sync 1=2000 3=4000 2=1000` to set different targets in one synchronized write. Torque must already be enabled for each servo being moved. Do not use movement commands on a worn or tensioned glove.

The builder identified the glove's physical IDs as 1=pinky, 2=ring, 3=middle, 4=index, 5=thumb flexion, and 6=thumb side-to-side opposition. This differs from the app's first-release mapping and from the scripts' eight-servo assumption.

With the glove above the two-column servo mount in the user's photo, the IDs are arranged top row **4 | 3**, middle row **5 | 2**, bottom row **6 | 1**. The cords are attached to the round servo horns in that photo, so do not use an unloaded bench-jog procedure on the assembled glove.

The app's **Devices** page now offers a read-only status check for IDs 1–6. If you need to run the original terminal script, stop the HandOff API first so both programs do not open the same serial port. From the project root, use:

```bash
cd hardware/reference
PYTHONPATH=../../backend ../../backend/.venv/bin/python servo_test.py /dev/cu.YOUR_PORT
```

At the `servo-test>` prompt, use `status` or `status 1`, then `exit`. Find the Mac serial port with `ls /dev/cu.usb*` after connecting the controller. Opening this script pings and reads servo status; it does not send a movement command until one is entered.

The scripts are saved so their communication code can be reviewed and adapted behind handoff's future hardware adapter. Physical movement still needs verified ID mapping, measured limits for all six servos, and a tested independent power stop.
