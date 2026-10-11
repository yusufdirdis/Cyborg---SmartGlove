# Online hardware bridge: drive the real glove from givemeahand.fit

**Status:** implemented on `feat/onlineHardware` (relay, bridge, arm page, website banner, `tests/test_bridge.py`). The supervised off-body glove test is still to do.
**Branch:** `feat/onlineHardware`.
**Date:** 2026-10-11.

## 1. Goal and decisions

People using the public site at https://givemeahand.fit, including remote judges and teammates, should be able to move the **real** glove. The glove is plugged into the owner's laptop by USB. The hosted API on Railway can't reach it.

Decisions already made by the owner:

| Topic | Decision |
|---|---|
| Who can drive | Remote visitors, not only people at the laptop. |
| Gate | **Both** a local *arm* switch on the laptop **and** an operator code for hardware actions. The code is created at the laptop each time it's armed, and the person handling the glove gives it to the remote user. Viewing, simulation and AI stay public. |
| Transport | A bridge on the laptop that connects **outbound** to Railway. No inbound ports and no tunnel. |
| Scope of this document | Design only. Implementation starts after review. |

What doesn't change:
- AI and Live never get hardware tools.
- Vision never moves anything.
- Calibration edits stay local-only.
- The physical power cutoff remains the real emergency stop.

## 2. Architecture

```
Visitor browser ──HTTPS──► Railway API (cloud mode) ──WSS /ws/bridge──► Laptop bridge ──HTTP 127.0.0.1──► Laptop local API ──USB──► Glove
                           holds no USB, forwards                       (outbound conn,                   (existing routes and
                           allow-listed hardware calls                   arm and operator check)           gates, writes on)
```

- **Laptop local API:** the existing `uvicorn app.main:app` on `127.0.0.1:8000`, with hardware writes on (the local default since main's "Enable local motor writes by default"). It has to use the **same Supabase database** (`SUPABASE_DB_URL`) as Railway, so macro IDs and calibration match between the two.
- **Laptop bridge:** a new `backend/app/bridge.py`, started with `python -m app.bridge`. It connects out to `wss://api.givemeahand.fit/ws/bridge`, receives allow-listed requests, and replays them as HTTP calls to the local API.
  - Because the replay comes from `127.0.0.1` with no `Origin` header, the existing `_local_hardware_request()` accepts it. **For that reason the bridge is where the remote gates are enforced**, on the laptop, before anything reaches the local API.
- **Railway:** in cloud mode, the hardware routes become relays to the bridge, guarded by the operator code. Railway's own process still has writes forced off (`writes_enabled()` is False under `HANDOFF_CLOUD=1`).

Why a relay instead of re-implementing hardware control in the bridge:
- Every existing check runs unchanged, in the process that owns the serial port: calibration limits, servo health re-reads, one-use 30-second preview tokens, the stop latch and `record_move` logging.
- Preview and execute tokens live in the laptop API's memory, so both calls of a pair naturally hit the same process.

## 3. Bridge protocol

- **Connection:** `wss://api.givemeahand.fit/ws/bridge`, authenticated with `HANDOFF_BRIDGE_TOKEN` in the first message. The token is compared in constant time, and the same value is set on Railway and in the laptop's `backend/.env`.
- **One bridge at a time.** A second connection is refused until the first one drops.
- **Messages** (JSON):
  - Railway → bridge: `{"id", "method", "path", "body", "operator_code"}`. Railway passes the visitor's code through; the bridge is the only side that checks it.
  - Bridge → Railway: `{"id", "status", "body"}`.
  - Railway waits up to 15 s for a reply, then answers the visitor with 504 "Glove did not respond".
- **Heartbeat:** a ping every 5 s. Either side treats 15 s of silence as a disconnect.
- **Allow-list.** The bridge refuses anything else with 403, and Railway never sends raw serial data.

  | Method and path | Notes |
  |---|---|
  | `GET /api/hardware/status`, `GET /api/hardware/control` | always allowed (read-only) |
  | `POST /api/control/stop` | always allowed, even when disarmed |
  | `POST /api/hardware/position/preview` and `/execute` | armed only |
  | `POST /api/hardware/group/preview` and `/execute` | armed only |
  | `POST /api/hardware/live` | armed only |
  | `POST /api/hardware/torque/enable` | armed only |
  | `POST /api/macros/{id}/hardware/run` | armed only: whole macros are allowed |
  | jog routes, calibration writes, settings, anything else | **refused** |

## 4. Gates for remote moves

Both gates are enforced **on the laptop by the bridge**, so a compromised Railway can't move the glove.

1. **Local arm page.**
   - The bridge serves a small page at `http://127.0.0.1:8765`, bound to loopback only.
   - It shows:
     - the bridge connection (online/offline to Railway) and whether the laptop API is reachable;
     - servo health;
     - **Arm for 30 minutes**, **Extend** and **Disarm** buttons, plus a big **Stop** button;
     - a countdown;
     - the current **operator code** in large type;
     - a log of the last remote moves.
   - Arming lasts **30 minutes** by default. **Extend** adds another 30 minutes, and **Disarm** ends it at any time.
   - While disarmed, the bridge refuses every move with 403 "Remote control is not armed at the glove".
   - The page has no login. It's reachable only from the laptop itself, it rejects non-loopback clients and foreign `Origin`/`Host` headers, and its buttons send a per-session CSRF token.
2. **Operator code, created at the laptop.**
   - Each time the operator arms, the bridge creates a fresh random 6-character code (no lookalike characters) and shows it on the arm page.
   - The person handling the glove gives it to the remote user in person, by voice on a call, or in chat.
   - The remote user enters it on the website. The site sends it as `X-Handoff-Operator`, and Railway forwards it to the bridge, which checks it in constant time.
   - Disarm, Stop, a heartbeat loss or expiry **invalidate the code**, and re-arming creates a new one.
   - After 5 wrong codes, the bridge disarms and asks the operator to re-arm, which also changes the code.
   - Railway never stores or logs the code, and no Railway setting holds it.
   - Status and Stop need no code.
3. **Existing gates still apply on the laptop:**
   - the local API's health checks, calibration limits and stop latch;
   - preview → confirm for position and group moves, including the off-body / power-cut / cord-path confirmations;
   - "Test on glove" for whole macros, which runs `run_macro()` with its own health checks.

## 5. Stop and failure behaviour

- **Website Stop:**
  - latches the stop on Railway's simulation (as today);
  - is forwarded to the bridge, which calls the laptop's `POST /api/control/stop` (latch plus torque-off attempt);
  - also disarms the bridge and invalidates the operator code.
- **Stop on the arm page:** stops locally right away, even if Railway is unreachable.
- **Connection lost:** if the bridge loses Railway (heartbeat timeout or socket closed), it calls the local stop, disarms, and stays disarmed until someone re-arms it **at the laptop**.
- **Railway restart:** Railway shows the glove offline until the bridge reconnects. A reconnect alone never re-arms.
- **Laptop API down:** the bridge reports "Glove API offline", and moves are refused.
- The physical power cutoff remains the reliable emergency stop. Every message and doc says so.

## 6. Railway changes (cloud mode)

- New `@app.websocket("/ws/bridge")` holding the single bridge connection, a pending-request map and the last reported arm state.
- `_hardware_access(request)` replaces `_local_hardware_request()` for the allow-listed routes. It passes when the request is local (unchanged), **or** when cloud mode is on and a bridge is connected. The armed state and the operator code are both checked on the bridge, so a compromised Railway still can't move a disarmed glove or guess around the code.
- In cloud mode those routes relay the request instead of calling `hardware_control` locally.
- `GET /api/hardware/status` in cloud mode returns the bridge status (`bridge: online/offline`, `armed`, `armed_until`) plus the laptop's servo status when online.
- One new environment variable, `HANDOFF_BRIDGE_TOKEN`, enables relaying. Without it, the current behaviour stays: hardware returns 403 from the cloud. No operator-code variable is needed, because the laptop creates the code.

## 7. Frontend

- A glove banner on the Dashboard, Manual Control and Macro Studio: "Glove offline", "Online, not armed (ask the operator)", or "Armed, 4:12 left".
- An operator-code prompt shown only when a hardware action returns 403 for a missing or expired code: "Ask the person at the glove for the operator code". The code is kept in `sessionStorage`, so it's forgotten when the tab closes, and cleared when the bridge reports a new arm session.
- The existing Check move / Confirm movement / Test on glove flows stay unchanged.

## 8. Logging

- The laptop API already logs every move through `record_move` (`hardware_moves`, `estop_checks`).
- Run the laptop API with `HANDOFF_INSTANCE=bridge` (or log `relayed_from=railway`) so remote moves can be told apart.
- The bridge keeps the last 20 relayed requests on the arm page. Railway doesn't log relayed requests, so the laptop API's rows are the record. The operator code and the bridge token are never logged.

## 9. Test plan

- **Unit tests with a fake bridge** (TestClient WebSocket):
  - auth with a wrong or missing token;
  - a second bridge refused;
  - requests outside the allow-list refused on both sides;
  - operator code checked on the bridge, with Stop and status exempt;
  - the code changes on every arm and is invalid after disarm, Stop, expiry or a heartbeat loss;
  - 5 wrong codes force a disarm;
  - disarmed means 403;
  - 30-minute expiry and extend;
  - the arm page rejects non-loopback clients, foreign `Origin` headers, and requests without a CSRF token;
  - heartbeat timeout triggers the local stop and disarms;
  - Railway Stop reaches the bridge;
  - a 504 when the bridge doesn't answer;
  - the bridge forwards only to `127.0.0.1`.
- **Existing safety tests unchanged:** AI and Live have no hardware tools, vision only records, calibration is locked in the cloud, and the local hardware guard stays.
- **Manual, supervised:**
  1. Local API plus bridge pointed at a second local API (no Railway).
  2. Railway with the glove **off-body** and the power cutoff in reach: arm, move one servo, Stop, pull the network cable, and confirm stop and disarm.
  3. A remote browser on another network.

## 10. Rollout

1. Implement on `feat/onlineHardware`, run the tests, and do the local run-through.
2. Hand off to the friend's agent: set `HANDOFF_BRIDGE_TOKEN` on Railway and redeploy both services.
3. Owner: put the same bridge token in `backend/.env`, start the local API with Supabase, start the bridge, and open `http://127.0.0.1:8765`. Then do the supervised off-body test.
4. Only after that, a wearable demo.

## 11. Decisions from review (2026-10-11)

1. **Whole macros** may be run remotely ("Test on glove"), as well as single and group moves.
2. **The arm window is 30 minutes**, with Extend for another 30. Stop, Disarm, expiry and a heartbeat loss all disarm.
3. **The person handling the glove at the laptop gives the operator code to the remote user.** The code is created fresh at each arm and shown on the local page; it is not a Railway setting.
4. **The arm control is a small local web page** at `http://127.0.0.1:8765`, served by the bridge on loopback only.
