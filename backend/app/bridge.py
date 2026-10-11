"""Laptop side of the online hardware bridge.

Run on the laptop the glove is plugged into, next to the normal local API:

    uvicorn app.main:app --host 127.0.0.1 --port 8000      # local API, writes on, same SUPABASE_DB_URL
    python -m app.bridge                                    # this bridge

It connects out to the hosted API (HANDOFF_BRIDGE_URL, default wss://api.givemeahand.fit/ws/bridge)
with HANDOFF_BRIDGE_TOKEN, and serves the arm page at http://127.0.0.1:8765 for the person at the glove.

Remote requests are accepted only while armed at the laptop and only with the operator code shown
on that page. Accepted requests are replayed to the local API, so every existing hardware gate
(calibration limits, health re-reads, one-use previews, the stop latch) still runs. The physical
power cutoff remains the real emergency stop.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

ARM_SECONDS = 30 * 60
MAX_WRONG_CODES = 5
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L lookalikes
HEARTBEAT_S = 5.0
HEARTBEAT_TIMEOUT_S = 15.0
LOCAL_TIMEOUT_S = 12.0
STOP_PATH = "/api/control/stop"

# (method, path pattern, needs arm + operator code). Everything else is refused here, whatever Railway sends.
ALLOW = [
    ("GET", re.compile(r"^/api/hardware/status$"), False),
    ("GET", re.compile(r"^/api/hardware/control$"), False),
    ("POST", re.compile(r"^/api/control/stop$"), False),
    ("POST", re.compile(r"^/api/hardware/(position|group)/(preview|execute)$"), True),
    ("POST", re.compile(r"^/api/hardware/live$"), True),
    ("POST", re.compile(r"^/api/hardware/torque/enable$"), True),
    ("POST", re.compile(r"^/api/macros/[0-9a-fA-F-]{1,64}/hardware/run$"), True),
]


def _rule(method: str, path: str):
    return next(((m, p, armed) for m, p, armed in ALLOW if m == method and p.match(path)), None)


@dataclass
class ArmState:
    """Local arm switch and the operator code. Only the person at the laptop changes it."""
    armed_until: float | None = None
    code: str | None = None
    session: str | None = None
    wrong_codes: int = 0
    reason: str = "Not armed yet."
    log: deque = field(default_factory=lambda: deque(maxlen=20))
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def armed(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        if self.armed_until is not None and now >= self.armed_until:
            self._disarm("Arm time ran out.")
        return self.armed_until is not None

    def arm(self, seconds: int = ARM_SECONDS) -> None:
        with self.lock:
            self.armed_until = time.time() + seconds
            self.code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
            self.session = secrets.token_hex(4)
            self.wrong_codes = 0
            self.reason = "Armed at the glove."

    def extend(self, seconds: int = ARM_SECONDS) -> bool:
        with self.lock:
            if self.armed_until is None:
                return False
            self.armed_until = max(self.armed_until, time.time()) + seconds
            return True

    def _disarm(self, reason: str) -> None:
        self.armed_until = None
        self.code = None  # any code a remote user has stops working
        self.session = None
        self.wrong_codes = 0
        self.reason = reason

    def disarm(self, reason: str = "Disarmed at the glove.") -> None:
        with self.lock:
            self._disarm(reason)

    def check_code(self, supplied: str) -> str | None:
        """None when the code is right; otherwise the refusal reason."""
        with self.lock:
            if self.code is None:
                return "Remote control is not armed at the glove."
            if supplied and hmac.compare_digest(supplied.strip().upper().encode(), self.code.encode()):
                self.wrong_codes = 0
                return None
            self.wrong_codes += 1
            if self.wrong_codes >= MAX_WRONG_CODES:
                self._disarm("Too many wrong operator codes. Re-arm at the glove for a new code.")
                return "Too many wrong operator codes. Ask the person at the glove to re-arm."
            return "Ask the person at the glove for the operator code."

    def public(self) -> dict:
        armed = self.armed()
        return {"armed": armed, "armed_until": self.armed_until if armed else None, "session": self.session if armed else None}

    def note(self, method: str, path: str, status: int) -> None:
        self.log.appendleft({"at": time.time(), "method": method, "path": path, "status": status})


def handle(state: ArmState, method: str, path: str, operator_code: str, call_local) -> tuple[int, object]:
    """Decide on one relayed request. call_local(method, path, body) -> (status, body) hits 127.0.0.1 only."""
    rule = _rule(method, path)
    if rule is None:
        return 403, {"detail": "This action is not available from the website."}
    if path == STOP_PATH:
        state.disarm("Stopped from the website.")
        return call_local(method, path)
    if rule[2]:
        if not state.armed():
            return 403, {"detail": "Remote control is not armed at the glove. Ask the person at the glove to arm it."}
        refused = state.check_code(operator_code)
        if refused:
            return 403, {"detail": refused, "operator_code_required": True}
    return call_local(method, path)


class LocalAPI:
    """The laptop's own API. Requests never leave the machine."""

    def __init__(self, base: str):
        parts = urlsplit(base)
        if parts.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise SystemExit("The bridge only talks to a local API (127.0.0.1).")
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, body=None) -> tuple[int, object]:
        import httpx

        try:
            response = httpx.request(method, self.base + path, json=body, timeout=LOCAL_TIMEOUT_S)
        except httpx.HTTPError as exc:
            return 502, {"detail": f"The local glove API is not reachable ({type(exc).__name__}). Start it on the laptop."}
        try:
            payload = response.json()
        except ValueError:
            payload = {"detail": response.text[:300]}
        return response.status_code, payload

    def reachable(self) -> bool:
        return self.call("GET", "/api/health")[0] == 200


class Bridge:
    def __init__(self, url: str, token: str, local: LocalAPI, state: ArmState | None = None):
        self.url, self.token, self.local = url, token, local
        self.state = state or ArmState()
        self.connected = False
        self.last_error = ""
        self.local_ok = False

    def local_stop(self, reason: str) -> None:
        self.state.disarm(reason)
        self.local.call("POST", STOP_PATH)

    def _status(self) -> dict:
        return {**self.state.public(), "local_api": self.local_ok}

    async def _answer(self, ws, message: dict) -> None:
        method, path = str(message.get("method", "")), str(message.get("path", ""))
        body = message.get("body")

        def call_local(m, p):
            return self.local.call(m, p, body)

        status, payload = await asyncio.to_thread(handle, self.state, method, path, str(message.get("operator_code", "")), call_local)
        self.state.note(method, path, status)
        await ws.send(json.dumps({"type": "response", "id": message.get("id"), "status": status, "body": payload}))

    async def run_once(self) -> None:
        import websockets

        async with websockets.connect(self.url, open_timeout=15, max_size=2**20) as ws:
            self.local_ok = await asyncio.to_thread(self.local.reachable)
            await ws.send(json.dumps({"type": "hello", "token": self.token, "state": self._status()}))
            welcome = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if welcome.get("type") != "welcome":
                raise ConnectionError("The hosted API refused the bridge.")
            self.connected, self.last_error = True, ""

            async def heartbeat():
                while True:
                    await asyncio.sleep(HEARTBEAT_S)
                    self.local_ok = await asyncio.to_thread(self.local.reachable)
                    await ws.send(json.dumps({"type": "ping", "state": self._status()}))

            beat = asyncio.create_task(heartbeat())
            try:
                while True:
                    message = json.loads(await asyncio.wait_for(ws.recv(), HEARTBEAT_TIMEOUT_S))
                    if message.get("type") == "request":
                        asyncio.create_task(self._answer(ws, message))
            finally:
                beat.cancel()

    async def run_forever(self) -> None:
        delay = 2
        while True:
            try:
                await self.run_once()
            except Exception as exc:  # any drop: stop and disarm locally, then retry
                self.last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
            if self.connected or self.state.armed():
                await asyncio.to_thread(self.local_stop, "Connection to the website was lost. Re-arm at the glove.")
            self.connected = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)


# ---------- the local arm page (loopback only) ----------

ARM_PAGE = (Path(__file__).with_name("bridge_page.html"))


def arm_app(bridge: Bridge):
    app = FastAPI(title="handoff bridge", docs_url=None, redoc_url=None, openapi_url=None)
    csrf = secrets.token_urlsafe(24)

    def local_only(request: Request, write: bool = False) -> None:
        client = request.client.host if request.client else ""
        host = (request.headers.get("host") or "").split(":")[0]
        origin = request.headers.get("origin")
        if client not in ("127.0.0.1", "::1", "testclient") or host not in ("127.0.0.1", "localhost", "testserver"):
            raise HTTPException(403, "The arm page is only available on the laptop with the glove.")
        if write:
            if origin and urlsplit(origin).hostname not in ("127.0.0.1", "localhost"):
                raise HTTPException(403, "Cross-site request refused.")
            if not hmac.compare_digest(request.headers.get("x-csrf-token", "").encode(), csrf.encode()):
                raise HTTPException(403, "Reload the arm page.")

    @app.get("/", response_class=HTMLResponse)
    def page(request: Request):
        local_only(request)
        return ARM_PAGE.read_text(encoding="utf-8").replace("{{CSRF}}", csrf)

    @app.get("/state")
    def state(request: Request):
        local_only(request)
        s = bridge.state
        return {**s.public(), "code": s.code if s.armed() else None, "reason": s.reason, "connected": bridge.connected,
                "local_api": bridge.local_ok, "last_error": bridge.last_error, "url": bridge.url, "log": list(s.log),
                "now": time.time()}

    @app.post("/arm")
    def arm(request: Request):
        local_only(request, write=True)
        bridge.state.arm()
        return {"ok": True}

    @app.post("/extend")
    def extend(request: Request):
        local_only(request, write=True)
        if not bridge.state.extend():
            raise HTTPException(409, "Arm first.")
        return {"ok": True}

    @app.post("/disarm")
    def disarm(request: Request):
        local_only(request, write=True)
        bridge.state.disarm()
        return {"ok": True}

    @app.post("/stop")
    def stop(request: Request):
        local_only(request, write=True)
        bridge.local_stop("Stopped at the glove.")
        return {"ok": True}

    return app


async def main_async(args) -> None:
    import uvicorn

    token = os.environ.get("HANDOFF_BRIDGE_TOKEN", "").strip()
    if not token:
        raise SystemExit("Set HANDOFF_BRIDGE_TOKEN (same value as on Railway) in backend/.env first.")
    bridge = Bridge(args.url, token, LocalAPI(args.local))
    server = uvicorn.Server(uvicorn.Config(arm_app(bridge), host="127.0.0.1", port=args.port, log_level="warning"))
    print(f"handoff bridge: arm page at http://127.0.0.1:{args.port}  ->  {args.url}")
    await asyncio.gather(server.serve(), bridge.run_forever())


def main(argv=None) -> None:
    from . import assistant  # noqa: F401  (loads backend/.env)

    parser = argparse.ArgumentParser(description="Relay the website's glove controls to this laptop, while armed.")
    parser.add_argument("--url", default=os.environ.get("HANDOFF_BRIDGE_URL", "wss://api.givemeahand.fit/ws/bridge"))
    parser.add_argument("--local", default=os.environ.get("HANDOFF_LOCAL_API", "http://127.0.0.1:8000"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("HANDOFF_BRIDGE_PORT", "8765")))
    try:
        asyncio.run(main_async(parser.parse_args(argv)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
