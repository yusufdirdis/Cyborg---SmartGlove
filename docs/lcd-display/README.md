# LCD torque-readiness display — handoff

The 16x2 LCD on an ESP mirrors the website’s torque readiness strip: settle count, the glove-marker bar, ready / aligning / torque on, and truthful error and no-data states. It is **display only**: no buttons, and it never sends a command.

| File | What it is |
|---|---|
| `SPEC.md` | Every state, exact screen text, bar geometry, custom characters, backlight colours |
| `PROTOCOL.md` | The one-way serial line format, backend → ESP |
| `firmware/handoff_lcd/handoff_lcd.ino` | Complete ESP sketch (standard 1602 I2C or Grove RGB) |
| `backend/lcd_bridge.py` | Complete backend module → copy to `backend/app/lcd_bridge.py` |
| `backend/main.py.patch` | The three small hooks in `backend/app/main.py` (lifespan task, status reading, torque route) |
| `backend/test_lcd_bridge.py` | Tests → copy to `backend/tests/test_lcd_bridge.py` |
| `WIRING.md` | Parts, wiring, flashing, running |

Verified on 2026-10-11 against `ux/polish-and-fixes` (`8086cf0`): the patch applies cleanly, and the backend suite passes with the bridge added (136 passed, 5 of them new). The firmware has not been compiled or run on hardware here, so flashing it is the first hardware test.

Update 2026-10-11: the sketch compiles with arduino-cli and ESP32 core 3.3.12 for an ESP32-S3 (HW678 / S3-N16R8, `esp32:esp32:esp32s3:FlashSize=16M,PSRAM=opi`, USB CDC on boot off). It was flashed over the COM port and accepted `STATE` lines through the same pyserial open the bridge uses. Two firmware changes: the ALIGNING glove now nudges ±1 px as SPEC.md says (it was 0/+1), and the onboard RGB LED (`STATUS_LED_PIN`, GPIO 48) mirrors the SPEC.md backlight colours, since the bench LCD is a single-colour 1602. See WIRING.md for the HW678 pins and power notes.

Design canvas: https://claude.ai/artifact/PK5dQJuLDmaGGFb9KaiP5s (rows “Task 1b · Torque readiness on a 1602 I2C LCD”).

## Paste this into Claude Code

```text
Add the LCD torque-readiness display described in docs/lcd-display/. Read README.md, SPEC.md and PROTOCOL.md first.

1. Copy docs/lcd-display/backend/lcd_bridge.py to backend/app/lcd_bridge.py and docs/lcd-display/backend/test_lcd_bridge.py to backend/tests/test_lcd_bridge.py.
2. Apply the hooks: `git apply docs/lcd-display/backend/main.py.patch`. If it does not apply cleanly because main.py moved, make the same three edits by hand: (a) in lifespan, start `lcd_bridge.run(lambda: sim.stopped)` as a task when `lcd_bridge.enabled()` and cancel it on shutdown; (b) in `/api/hardware/status`, call `lcd_bridge.record_reading(reading)` before returning it; (c) in `/api/hardware/torque/enable`, call `lcd_bridge.torque_started()` before `enable_torque` and `lcd_bridge.torque_finished(error_text_or_None)` on every exit path.
3. Run `cd backend && python -m pytest -q`. Everything must pass. The bridge is off unless HANDOFF_LCD_PORT is set, so the tests never open a port.
4. Keep the firmware at docs/lcd-display/firmware/handoff_lcd/handoff_lcd.ino as is (I flash it from the Arduino IDE). Only change it if the spec and the code disagree, and tell me.
5. Add a short "LCD display" section to README.md that points to docs/lcd-display/WIRING.md and documents HANDOFF_LCD_PORT.
6. Rules: the bridge must never scan servos itself, open the servo controller port, or call any motor-write function; tests/test_lcd_bridge.py checks this. Never set HANDOFF_ENABLE_HARDWARE_WRITES for testing.
7. Commit on a new branch `feat/lcd-display` from the current branch, push it, and open a PR into main with `gh pr create`. Do not merge it. Report the PR link and the test output.
```
