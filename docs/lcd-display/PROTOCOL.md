# Serial protocol: backend → ESP

USB serial, **115200 baud, 8N1**, one ASCII line per message ending in `\n`. One direction only: the backend writes, the ESP reads. The ESP never sends commands back.

```
STATE <NAME> [ARG]\n
```
| Message | Meaning |
|---|---|
| `STATE SETTLING 4` | torque off, 4 of 6 servos stationary (ARG 0–5) |
| `STATE READY` | torque off, all six stationary |
| `STATE ALIGNING` | a torque-enable request is in progress |
| `STATE TORQUE_ON` | all six report torque on |
| `STATE NOT_ENABLED 3` | the last torque attempt failed because servo 3 was moving (0 = other reason) |
| `STATE STOPPED` | software stop latched |
| `STATE LOCKED` | `HANDOFF_ENABLE_HARDWARE_WRITES` is not 1 |
| `STATE OFFLINE` | no servo reading |
| `STATE UNKNOWN` | the newest servo reading is older than 45 s |

The backend sends on every change and repeats the current line every 1 s as a heartbeat. The ESP shows `NO DATA FROM APP` if nothing valid arrives for 5 s. Unknown lines are ignored.
