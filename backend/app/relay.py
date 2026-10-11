"""Railway side of the online hardware bridge.

The hosted API has no USB glove. When the owner runs `python -m app.bridge` on the laptop,
it connects out to `/ws/bridge` here, and allow-listed hardware requests from the public site
are relayed to it. The bridge, on the laptop, decides whether to accept them: it checks the
local arm switch and the operator code, then replays the request to the laptop's own local
API, where every existing hardware gate still runs. This module only forwards.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
from uuid import uuid4

from fastapi import WebSocket

# (method, path pattern). Anything else is never relayed; the bridge keeps its own copy of this list.
RELAYED = [
    ("GET", re.compile(r"^/api/hardware/status$")),
    ("GET", re.compile(r"^/api/hardware/control$")),
    ("POST", re.compile(r"^/api/hardware/(position|group)/(preview|execute)$")),
    ("POST", re.compile(r"^/api/hardware/live$")),
    ("POST", re.compile(r"^/api/hardware/torque/enable$")),
    ("POST", re.compile(r"^/api/macros/[0-9a-fA-F-]{1,64}/hardware/run$")),
]
REQUEST_TIMEOUT_S = 15.0
HEARTBEAT_TIMEOUT_S = 15.0


def relayed(method: str, path: str) -> bool:
    return any(method == m and pattern.match(path) for m, pattern in RELAYED)


def bridge_token() -> str:
    return os.environ.get("HANDOFF_BRIDGE_TOKEN", "").strip()


class BridgeUnavailable(Exception):
    pass


class BridgeHub:
    """Holds the single connected bridge and the requests waiting for its answers."""

    def __init__(self):
        self.ws: WebSocket | None = None
        self.pending: dict[str, asyncio.Future] = {}
        self.state: dict = {"armed": False, "armed_until": None, "session": None, "local_api": False}
        self.on_change = None  # set by main to broadcast telemetry

    @property
    def online(self) -> bool:
        return self.ws is not None

    def public_status(self) -> dict:
        return {"online": self.online, **(self.state if self.online else {"armed": False, "armed_until": None, "session": None, "local_api": False})}

    async def _changed(self) -> None:
        if self.on_change:
            await self.on_change()

    async def serve(self, ws: WebSocket) -> None:
        """Run one bridge connection until it closes. The first message must carry the token."""
        expected = bridge_token()
        await ws.accept()
        try:
            hello = json.loads(await asyncio.wait_for(ws.receive_text(), 10))
        except Exception:
            await ws.close(code=4400)
            return
        supplied = str(hello.get("token", "")) if isinstance(hello, dict) else ""
        if not expected or hello.get("type") != "hello" or not hmac.compare_digest(supplied.encode(), expected.encode()):
            await ws.close(code=4401)
            return
        if self.ws is not None:
            await ws.close(code=4409)  # one bridge at a time
            return
        self.ws = ws
        self._update_state(hello.get("state") or {})
        await ws.send_text(json.dumps({"type": "welcome"}))
        await self._changed()
        try:
            while True:
                message = json.loads(await asyncio.wait_for(ws.receive_text(), HEARTBEAT_TIMEOUT_S))
                kind = message.get("type")
                if kind == "ping":
                    if isinstance(message.get("state"), dict):
                        before = dict(self.state)
                        self._update_state(message["state"])
                        if before != self.state:
                            await self._changed()
                    await ws.send_text(json.dumps({"type": "pong"}))
                elif kind == "response":
                    future = self.pending.pop(str(message.get("id")), None)
                    if future and not future.done():
                        future.set_result((int(message.get("status", 502)), message.get("body")))
        except Exception:
            pass
        finally:
            if self.ws is ws:
                self.ws = None
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(BridgeUnavailable("The glove disconnected."))
                self.pending.clear()
                await self._changed()

    def _update_state(self, state: dict) -> None:
        self.state = {"armed": bool(state.get("armed")), "armed_until": state.get("armed_until"),
                      "session": state.get("session"), "local_api": bool(state.get("local_api"))}

    async def request(self, method: str, path: str, body, operator_code: str | None,
                      timeout: float | None = None) -> tuple[int, object]:
        ws = self.ws
        if ws is None:
            raise BridgeUnavailable("The glove is offline. The bridge on the laptop is not connected.")
        request_id = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await ws.send_text(json.dumps({"type": "request", "id": request_id, "method": method, "path": path,
                                           "body": body, "operator_code": operator_code or ""}))
            return await asyncio.wait_for(future, timeout or REQUEST_TIMEOUT_S)
        except asyncio.TimeoutError as exc:
            raise BridgeUnavailable("The glove did not respond in time.") from exc
        finally:
            self.pending.pop(request_id, None)

    async def stop(self) -> None:
        """Best effort: the bridge stops and disarms locally. Never raises."""
        if self.ws is None:
            return
        try:
            await self.request("POST", "/api/control/stop", None, None, timeout=5)
        except Exception:
            pass


hub = BridgeHub()
