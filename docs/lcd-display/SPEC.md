# 1602 LCD — torque readiness display spec

Design source: the canvas rows “Task 1b · Torque readiness on a 1602 I2C LCD” (boards *1602 LCD · every state*, *custom characters*, *component*) at https://claude.ai/artifact/PK5dQJuLDmaGGFb9KaiP5s. The LCD mirrors the website’s torque readiness strip. **It is display-only: no buttons, it never sends a command.**

## Screen
16 × 2 characters, each 5 × 8 px. Line 1 = the state in words (the screen has no colour to rely on). Line 2 = the progress bar, or a second line of text for error-type states. Text is upper case, padded to 16 characters.

## States (priority top to bottom — the first that applies wins)
| # | State | Line 1 | Line 2 | Bar % | RGB backlight |
|---|---|---|---|---|---|
| 1 | `NODATA` (ESP-side: no message for 5 s) | `NO DATA FROM APP` | `CHECK USB CABLE` | text | blue |
| 2 | `STOPPED` | `STOPPED` | `RESET ON WEBSITE` | text | blue |
| 3 | `LOCKED` (server writes off) | `CONTROL LOCKED` | `ENABLE ON SERVER` | text | blue |
| 4 | `OFFLINE` (no servo reading) | `SERVOS OFFLINE` | `CHECK USB+POWER` | text | blue |
| 5 | `UNKNOWN` (last reading older than 45 s) | `GLOVE UNKNOWN` | `OPEN THE WEBSITE` | text | blue |
| 6 | `ALIGNING` | `ALIGNING` + `.`/`..`/`...` cycling | bar, glove nudges ±1 px at 4 Hz | 83 | green |
| 7 | `NOT_ENABLED n` (last torque attempt failed, shown 10 s) | `NOT ENABLED` | `S{n} MOVING - WAIT` (or `SEE WEBSITE` if n = 0) | text | blue |
| 8 | `TORQUE_ON` (all six torque on) | `TORQUE ON    6/6` | bar | 100 | green |
| 9 | `READY` (torque off, 6 of 6 stationary) | `READY TO HOLD` | bar | 66 | green |
| 10 | `SETTLING n` (torque off, n of 6 stationary) | `SETTLING     n/6` | bar | 34 + n/6·32 | red for n ≤ 3, yellow for n = 4–5 |

Blue = the neutral colour, so red only ever means “still settling”. On a standard (single-colour) 1602 the backlight simply stays on.

RGB values: red `(255, 40, 30)`, yellow `(255, 170, 0)`, green `(40, 200, 70)`, blue `(35, 70, 200)`.

## Line 2 bar
- 80 px wide (16 cells × 5). Glove tip x = `9 + round(pct × 70 / 100)`, glove left = tip − 9 (+ nudge), clamped to 0–70.
- Done part: rows 3–4 solid, ending 2 px before the glove cuff. To-go part: row 4 dotted (cell columns 0, 2, 4). Stage ticks: rows 2–6 at x = 27 and x = 52 (Read | Settle | Hold).
- Glove (10 × 8, back of hand toward you, index finger on top pointing right):
```
...XXX....
..XXXXXXX.
X.XXXXXXXX
X.XXXX....
X.XXXXX...
X.XXXX....
X.XXXXX...
..XXXX....
```
## Custom characters (CGRAM, 8 slots)
| Slot | Name | Bytes (rows 0–7) |
|---|---|---|
| 0 | done | `00 00 00 1F 1F 00 00 00` |
| 1 | to go | `00 00 00 00 15 00 00 00` |
| 2 | done + tick | `00 00 04 1F 1F 04 04 00` |
| 3 | to go + tick | `00 00 04 04 15 04 04 00` |
| 4–6 | glove A/B/C | rewritten each frame from the 80 × 8 bitmap (the glove can straddle 3 cells) |
| 7 | spare | unused |

Redraw only when the state changes (and every 250 ms while aligning). Rewrite slots 4–6, then print line 2 once; never clear the whole screen between frames.

## Truth rules
- Never keep showing READY or TORQUE ON from old data: no message for 5 s → `NODATA`; a backend reading older than 45 s → `UNKNOWN`.
- The settle count is the number of servos reporting `moving == false`; it is never estimated or timed.
