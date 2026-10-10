"""Chat and voice assistant. Providers only draft proposals; nothing here moves the hand.

A proposal reaches the simulation only through the confirm route, which reuses the
ordinary simulation pose and macro routes. There is no hardware command type.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError



def env_file() -> Path:
    """backend/.env by default; tests point HANDOS_ENV_FILE at a scratch file."""
    return Path(os.environ.get("HANDOS_ENV_FILE") or Path(__file__).resolve().parents[1] / ".env")


load_dotenv(env_file())

PROPOSAL_TTL_S = 60
PROVIDER_TIMEOUT_S = 15
MACROS_MARKER = "SAVED_MACROS_JSON:"
SAVE_STEPS_MAX = 30
STEP_MS_MIN = 100
STEP_MS_MAX = 10000
TEXT_ONLY = "Nothing was moved."
MAX_AUDIO_BYTES = 4 * 1024 * 1024  # About four minutes of browser Opus; demo clips are seconds long.
AUDIO_TYPES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav", "audio/aac", "audio/flac", "audio/opus"}


class SaveStep(BaseModel):
    positions: list[int] | None = None
    duration_ms: int | None = None


class ModelOutput(BaseModel):
    """The JSON contract every provider must answer with."""
    reply: str = Field(default="", max_length=600)
    action: Literal["none", "pose", "macro", "save_macro", "stop"]
    positions: list[int] | None = None
    label: str | None = Field(default=None, max_length=60)
    macro_id: str | None = None
    name: str | None = Field(default=None, max_length=60)
    steps: list[SaveStep] | None = None
    append_to_macro_id: str | None = None


OUTPUT_SCHEMA = {"type": "object", "required": ["reply", "action"], "properties": {
    "reply": {"type": "string"},
    "action": {"type": "string", "enum": ["none", "pose", "macro", "save_macro", "stop"]},
    "positions": {"type": "array", "items": {"type": "integer"}},
    "label": {"type": "string"},
    "macro_id": {"type": "string"},
    "name": {"type": "string"},
    "steps": {"type": "array", "items": {"type": "object", "properties": {
        "positions": {"type": "array", "items": {"type": "integer"}},
        "duration_ms": {"type": "integer"},
    }}},
    "append_to_macro_id": {"type": "string"},
}}


@dataclass(frozen=True)
class Command:
    type: Literal["pose", "macro", "save_macro", "stop"]
    positions: dict[int, int] | None = None
    label: str = ""
    macro_id: str = ""
    name: str = ""
    steps: list[dict] | None = None
    append_to: dict | None = None

    def public(self) -> dict:
        if self.type == "pose":
            return {"type": "pose", "positions": self.positions, "label": self.label}
        if self.type == "save_macro":
            return {"type": "save_macro", "name": self.name, "steps": self.steps, "append_to": self.append_to}
        return {"type": self.type, "macro_id": self.macro_id, "name": self.name}


def system_prompt(servo_names: dict[int, str], macros: list[dict]) -> str:
    servos = "\n".join(f"  {sid}: {name}" for sid, name in servo_names.items())
    return f"""You are the assistant for handoff, a six-servo smart glove studio running in SIMULATION.
You never move anything yourself. You draft at most one proposal; a human must click Confirm.
Servos, in this exact order (IDs 1-6):
{servos}
A position is an integer percentage: 0 = finger fully open/relaxed, 100 = fully flexed.
Answer ONLY with one JSON object, no prose around it:
{{"reply": short friendly sentence, "action": "none"|"pose"|"macro"|"save_macro"|"stop",
  "positions": six integers 0-100 in servo ID order (only for "pose"),
  "label": short pose name (only for "pose"),
  "macro_id": id of a saved macro (only for "macro"),
  "name": macro name, 1-60 characters (only for "save_macro"),
  "steps": 1-{SAVE_STEPS_MAX} objects {{"positions": six integers 0-100, "duration_ms": {STEP_MS_MIN}-{STEP_MS_MAX}}} (only for "save_macro"),
  "append_to_macro_id": id of a saved macro to extend (optional, only for "save_macro")}}
Prefer "macro" when a saved macro matches the request. Use "stop" when the user asks to stop, halt or freeze.
"Save as X" or "make an N-step Y" means "save_macro"; to save the pose or movement you last proposed, reuse its exact positions in the steps.
Use "none" for questions or anything that is not a hand gesture. Never claim the hand already moved or that anything was saved; say it is ready for confirmation.
{MACROS_MARKER} {json.dumps(macros)}"""


def _key(name: str) -> str | None:
    return os.environ.get(name) or None


class Provider:
    name = ""
    label = ""
    key_env: str | None = None
    model_env = ""
    default_model = ""

    @property
    def model(self) -> str:
        return os.environ.get(self.model_env) or self.default_model

    def available(self) -> bool:
        return self.key_env is None or _key(self.key_env) is not None

    def complete(self, system: str, messages: list[dict]) -> str:
        raise NotImplementedError


class GeminiProvider(Provider):
    name, label, key_env, model_env, default_model = "gemini", "Gemini", "GEMINI_API_KEY", "GEMINI_MODEL", "gemini-3.8-flash"

    def complete(self, system, messages):
        from google import genai

        client = genai.Client(api_key=_key(self.key_env))
        transcript = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
        interaction = client.interactions.create(
            model=self.model, system_instruction=system, input=transcript, timeout=PROVIDER_TIMEOUT_S,
            response_format={"type": "text", "mime_type": "application/json", "schema": OUTPUT_SCHEMA})
        return interaction.output_text

    def transcribe(self, audio: bytes, mime_type: str, vocabulary: str) -> str:
        import base64

        from google import genai

        client = genai.Client(api_key=_key(self.key_env))
        prompt = ("Transcribe this short voice command exactly, in English. Output only the spoken words, "
                  "with no quotes, labels or commentary. Output nothing if there is no speech. "
                  f"Words the speaker is likely to use: {vocabulary}.")
        interaction = client.interactions.create(
            model=self.model, timeout=PROVIDER_TIMEOUT_S,
            input=[{"type": "text", "text": prompt},
                   {"type": "audio", "data": base64.b64encode(audio).decode("ascii"), "mime_type": mime_type}])
        return interaction.output_text or ""


class OpenRouterProvider(Provider):
    name, label, key_env, model_env, default_model = "openrouter", "OpenRouter", "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "~openai/gpt-sol-latest"

    def complete(self, system, messages):
        from openai import OpenAI

        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=_key(self.key_env), timeout=PROVIDER_TIMEOUT_S)
        completion = client.chat.completions.create(
            model=self.model, messages=[{"role": "system", "content": system}, *messages],
            extra_headers={"X-OpenRouter-Title": "handoff"})
        return completion.choices[0].message.content or ""


class HuggingFaceProvider(Provider):
    name, label, key_env, model_env, default_model = "huggingface", "Hugging Face", "HF_TOKEN", "HF_MODEL", "openai/gpt-oss-120b:fastest"

    def complete(self, system, messages):
        from huggingface_hub import InferenceClient

        client = InferenceClient(api_key=_key(self.key_env), timeout=PROVIDER_TIMEOUT_S)
        completion = client.chat_completion(messages=[{"role": "system", "content": system}, *messages], model=self.model)
        return completion.choices[0].message.content or ""


STOP_WORDS = {"stop", "halt", "freeze", "emergency"}
FILLER_WORDS = {"hand", "sign", "the", "a", "an", "my", "pose", "gesture"}


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


class OfflineProvider(Provider):
    """Deterministic keyword matcher so the demo works without network or keys."""
    name, label, default_model = "offline", "Offline (keywords)", "keyword-matcher"

    def complete(self, system, messages):
        words = _words(messages[-1]["content"])
        macros = json.loads(system.split(MACROS_MARKER, 1)[1]) if MACROS_MARKER in system else []
        if words & STOP_WORDS:
            return json.dumps({"reply": "Stopping now.", "action": "stop"})
        matches = [m for m in macros if (keys := _words(m["name"]) - FILLER_WORDS) and keys <= words]
        if matches:
            best = max(matches, key=lambda m: len(m["name"]))
            return json.dumps({"reply": f"{best['name']} is ready for your confirmation.", "action": "macro", "macro_id": best["id"]})
        for keys, value, label in (({"fist", "close", "grab", "grip"}, 100, "Closed fist"), ({"open", "relax", "release"}, 0, "Open hand")):
            if words & keys:
                return json.dumps({"reply": f"{label} is ready for your confirmation.", "action": "pose", "positions": [value] * 6, "label": label})
        names = ", ".join(m["name"] for m in macros) or "none saved"
        return json.dumps({"reply": f"Offline mode understands saved macro names ({names}), open, fist, and stop.", "action": "none"})


PROVIDERS: dict[str, Provider] = {p.name: p for p in (GeminiProvider(), OpenRouterProvider(), HuggingFaceProvider(), OfflineProvider())}


def provider_list() -> list[dict]:
    return [{"name": p.name, "label": p.label, "available": p.available(), "model": p.model} for p in PROVIDERS.values()]


def parse_output(text: str, servo_ids: list[int], macros: list[dict]) -> tuple[str, Command | None]:
    """Turn raw provider text into a reply and an optional validated command."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    try:
        output = ModelOutput.model_validate(json.loads(cleaned[start:end + 1] if start >= 0 else cleaned))
    except (ValueError, ValidationError):
        return f"I couldn't turn that into a safe action, so no move was proposed. {TEXT_ONLY}", None
    reply = output.reply.strip() or "Okay."
    if output.action == "none":
        return reply, None
    if output.action == "stop":
        return reply, Command(type="stop")
    if output.action == "pose":
        values = output.positions or []
        if len(values) != len(servo_ids) or any(not 0 <= v <= 100 for v in values):
            return f"That pose needs six values from 0 to 100, so no move was proposed. {TEXT_ONLY}", None
        return reply, Command(type="pose", positions=dict(zip(servo_ids, values)), label=(output.label or "Custom pose").strip()[:60])
    if output.action == "save_macro":
        name = (output.name or "").strip()
        if not name:
            return f"A saved macro needs a name, so nothing was saved. {TEXT_ONLY}", None
        raw_steps = output.steps or []
        if not 1 <= len(raw_steps) <= SAVE_STEPS_MAX:
            return f"That save needs 1 to {SAVE_STEPS_MAX} steps, so nothing was saved. {TEXT_ONLY}", None
        steps = []
        for step in raw_steps:
            values = step.positions or []
            if len(values) != len(servo_ids) or any(not 0 <= v <= 100 for v in values):
                return f"Every saved step needs six values from 0 to 100, so nothing was saved. {TEXT_ONLY}", None
            if step.duration_ms is None or not STEP_MS_MIN <= step.duration_ms <= STEP_MS_MAX:
                return f"Every saved step needs a duration from {STEP_MS_MIN} to {STEP_MS_MAX} ms, so nothing was saved. {TEXT_ONLY}", None
            steps.append({"positions": dict(zip(servo_ids, values)), "duration_ms": step.duration_ms})
        append_to = None
        if output.append_to_macro_id:
            target = next((m for m in macros if m["id"] == output.append_to_macro_id), None)
            if target is None:
                return f"I couldn't find that saved macro, so nothing was saved. {TEXT_ONLY}", None
            existing = target.get("steps")
            if existing is not None and existing + len(steps) > SAVE_STEPS_MAX:
                return f"That macro already has {existing} steps, so nothing was saved. {TEXT_ONLY}", None
            append_to = {"macro_id": target["id"], "name": target["name"]}
        return reply, Command(type="save_macro", name=name, steps=steps, append_to=append_to)
    macro = next((m for m in macros if m["id"] == output.macro_id), None)
    if macro is None:
        return f"I couldn't find that saved macro, so no move was proposed. {TEXT_ONLY}", None
    return reply, Command(type="macro", macro_id=macro["id"], name=macro["name"])


async def ask(provider_name: str, message: str, history: list[dict], servo_names: dict[int, str], macros: list[dict]) -> tuple[str, Command | None]:
    """Raises KeyError for an unknown provider. Every other failure degrades to text."""
    provider = PROVIDERS[provider_name]
    if not provider.available():
        return f"{provider.label} has no API key configured on the server. Try another provider or Offline. {TEXT_ONLY}", None
    messages = [{"role": t["role"], "content": t["text"]} for t in history[-10:]] + [{"role": "user", "content": message}]
    try:
        text = await asyncio.wait_for(asyncio.to_thread(provider.complete, system_prompt(servo_names, macros), messages), PROVIDER_TIMEOUT_S + 2)
    except Exception as exc:  # Provider outages must never surface as an action.
        return f"{provider.label} is unavailable right now ({type(exc).__name__}). {TEXT_ONLY}", None
    return parse_output(text, list(servo_names), macros)


class ProposalGone(Exception):
    pass


@dataclass
class _Proposal:
    command: Command
    provider: str
    expires_at: float
    status: str = "pending"


_proposals: dict[str, _Proposal] = {}
_proposals_lock = threading.Lock()


def create_proposal(command: Command, provider: str) -> dict:
    now = time.monotonic()
    proposal_id = uuid4().hex
    with _proposals_lock:
        for key in [k for k, p in _proposals.items() if p.expires_at < now - 600]:
            del _proposals[key]
        _proposals[proposal_id] = _Proposal(command, provider, now + PROPOSAL_TTL_S)
    return {"id": proposal_id, "provider": provider, "expires_in_s": PROPOSAL_TTL_S, "command": command.public()}


def claim_proposal(proposal_id: str) -> Command:
    """Single use. Raises KeyError if unknown and ProposalGone if no longer pending."""
    with _proposals_lock:
        proposal = _proposals[proposal_id]
        if proposal.status != "pending" or time.monotonic() > proposal.expires_at:
            raise ProposalGone("This proposal expired or was already used. Ask again.")
        proposal.status = "confirmed"
        return proposal.command


def cancel_proposal(proposal_id: str) -> None:
    with _proposals_lock:
        proposal = _proposals[proposal_id]
        if proposal.status == "pending":
            proposal.status = "cancelled"


def clear_proposals() -> None:
    with _proposals_lock:
        for proposal in _proposals.values():
            if proposal.status == "pending":
                proposal.status = "cancelled"


# Provider setup from the Settings page. Keys are written to the .env file and the
# process environment; only a masked hint is ever returned to the browser.
CONFIGURABLE = ("gemini", "openrouter", "huggingface")


def _configurable(name: str) -> Provider:
    if name not in CONFIGURABLE:
        raise KeyError(name)
    return PROVIDERS[name]


def settings_list() -> list[dict]:
    result = []
    for name in CONFIGURABLE:
        provider, key = PROVIDERS[name], _key(PROVIDERS[name].key_env)
        result.append({"name": name, "label": provider.label, "key_set": key is not None,
                       "key_hint": f"••••{key[-4:]}" if key and len(key) >= 8 else ("••••" if key else None),
                       "model": provider.model, "default_model": provider.default_model})
    return result


def _write_env(variable: str, value: str | None) -> None:
    from dotenv import set_key, unset_key

    path = env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    if value is None:
        unset_key(path, variable)
        os.environ.pop(variable, None)
    else:
        set_key(path, variable, value, quote_mode="always")
        os.environ[variable] = value


def update_settings(name: str, api_key: str | None, model: str | None) -> None:
    """Raises KeyError for a provider without settings. An empty model resets to the default."""
    provider = _configurable(name)
    if api_key is not None:
        _write_env(provider.key_env, api_key)
    if model is not None:
        _write_env(provider.model_env, model or None)


def clear_key(name: str) -> None:
    provider = _configurable(name)
    _write_env(provider.key_env, None)


async def test_provider(name: str) -> dict:
    """One tiny request through the normal provider path. Never echoes the key."""
    provider = _configurable(name)
    key = _key(provider.key_env)
    if key is None:
        return {"ok": False, "message": "No API key saved.", "latency_ms": None}
    started = time.perf_counter()
    try:
        await asyncio.wait_for(asyncio.to_thread(provider.complete, 'Reply only with {"reply": "ok", "action": "none"}.',
                                                 [{"role": "user", "content": "ping"}]), PROVIDER_TIMEOUT_S + 2)
    except Exception as exc:
        detail = str(exc).replace(key, "••••")[:200]
        return {"ok": False, "message": f"{type(exc).__name__}: {detail}", "latency_ms": None}
    return {"ok": True, "message": f"Connected to {provider.model}", "latency_ms": round((time.perf_counter() - started) * 1000)}


class TranscriptionError(Exception):
    pass


async def transcribe(audio: bytes, mime_type: str, servo_names: dict[int, str], macros: list[dict]) -> str:
    """Speech to text with Gemini. The transcript is plain text: it never moves anything."""
    provider = PROVIDERS["gemini"]
    if not provider.available():
        raise PermissionError("Voice transcription needs a Gemini API key. Add one in Settings.")
    vocabulary = ", ".join([*servo_names.values(), *(m["name"] for m in macros), "open", "fist", "stop"])
    try:
        text = await asyncio.wait_for(asyncio.to_thread(provider.transcribe, audio, mime_type, vocabulary), PROVIDER_TIMEOUT_S + 2)
    except Exception as exc:
        detail = str(exc)
        if key := _key(provider.key_env):
            detail = detail.replace(key, "••••")
        raise TranscriptionError(f"Gemini transcription failed ({type(exc).__name__}: {detail[:160]})") from exc
    return " ".join(text.split())


# Gemini Live. The browser gets a single-use ephemeral token; the real key and the
# prompt and tools stay on the server. Nothing here moves the hand.
GEMINI_LIVE_MODEL_ENV = "GEMINI_LIVE_MODEL"
GEMINI_LIVE_MODEL_DEFAULT = "gemini-3.8-live"
LIVE_API_VERSION = "v1alpha"  # The browser's @google/genai live.connect pins v1alpha for ephemeral tokens.
LIVE_TOKEN_MINUTES = 30
LIVE_NEW_SESSION_WINDOW_S = 60


def live_model() -> str:
    return os.environ.get(GEMINI_LIVE_MODEL_ENV) or GEMINI_LIVE_MODEL_DEFAULT


def live_instruction(servo_names: dict[int, str], macros: list[dict]) -> str:
    servos = "\n".join(f"  {sid}: {name}" for sid, name in servo_names.items())
    saved = ", ".join(f"{m['id']} ({m['name']})" for m in macros) or "none saved"
    return f"""You are the live voice of handoff, a six-servo smart glove studio running in SIMULATION.
You see the user through the camera and hear them through the microphone. Your main job is mirroring:
when the user shows a hand gesture on camera, say what you see and propose the matching pose for the glove.
Servos, in this exact order (IDs 1-6):
{servos}
A position is an integer percentage: 0 = finger fully open/relaxed, 100 = fully flexed.
Saved macros, as id (name): {saved}. Prefer a matching saved macro over a new pose.
When the user asks to save a movement as a macro, call propose_save_macro with its steps; to save what you last proposed, reuse those exact positions.
Only call a tool when the user asks for a move, a mirror or a save. Never call a tool for questions, small talk or descriptions.
Never say or imply the hand already moved or that anything was saved; say it is ready for the user's Confirm click.
When the user says "stop", call stop_hand right away."""


LIVE_TOOLS = [{"functionDeclarations": [
    {"name": "propose_pose",
     "description": "Propose a six-servo pose for the user to confirm. Nothing moves until the user clicks Confirm.",
     "parameters": {"type": "OBJECT", "properties": {
         "positions": {"type": "ARRAY", "items": {"type": "INTEGER"},
                       "description": "Six integers from 0 to 100 in servo ID order (0 = open, 100 = flexed)."},
         "label": {"type": "STRING", "description": "Short pose name, for example 'Peace sign'."},
     }, "required": ["positions", "label"]}},
    {"name": "propose_macro",
     "description": "Propose a saved macro by id for the user to confirm. Nothing moves until the user clicks Confirm.",
     "parameters": {"type": "OBJECT", "properties": {
         "macro_id": {"type": "STRING", "description": "The id of a saved macro."},
     }, "required": ["macro_id"]}},
    {"name": "propose_save_macro",
     "description": "Propose saving a movement as a macro for the user to confirm. Nothing is saved or moved until the user clicks Confirm.",
     "parameters": {"type": "OBJECT", "properties": {
         "name": {"type": "STRING", "description": "Short macro name, 1-60 characters."},
         "steps": {"type": "ARRAY", "description": "One to 30 steps.",
                   "items": {"type": "OBJECT", "properties": {
                       "positions": {"type": "ARRAY", "items": {"type": "INTEGER"},
                                     "description": "Six integers from 0 to 100 in servo ID order."},
                       "duration_ms": {"type": "INTEGER", "description": "Step duration from 100 to 10000 milliseconds."},
                   }, "required": ["positions", "duration_ms"]}},
         "append_to_macro_id": {"type": "STRING", "description": "Optional id of a saved macro to extend."},
     }, "required": ["name", "steps"]}},
    {"name": "stop_hand",
     "description": "Stop the glove immediately when the user asks to stop."},
]}]


class LiveTokenError(Exception):
    pass


def sdk_live_tools() -> list[dict]:
    """LIVE_TOOLS in the python SDK's snake_case shape, for the locked token constraints."""
    return [{"function_declarations": LIVE_TOOLS[0]["functionDeclarations"]}]


def live_token_config(model: str, now: datetime, servo_names: dict[int, str], macros: list[dict]) -> dict:
    """The full token config. The lock fixes the prompt, the tools and the media settings server-side."""
    return {
        "uses": 1,
        "expire_time": now + timedelta(minutes=LIVE_TOKEN_MINUTES),
        "new_session_expire_time": now + timedelta(seconds=LIVE_NEW_SESSION_WINDOW_S),
        "live_connect_constraints": {"model": model, "config": {
            "session_resumption": {},
            "response_modalities": ["AUDIO"],
            "system_instruction": live_instruction(servo_names, macros),
            "tools": sdk_live_tools(),
            "input_audio_transcription": {},
            "output_audio_transcription": {},
        }},
    }


def _sdk_create_token(key: str, config: dict) -> str:
    """The one google-genai call, kept separate so tests never need the network."""
    from google import genai

    client = genai.Client(api_key=key, http_options={"api_version": LIVE_API_VERSION})
    token = client.auth_tokens.create(config=config)
    return token.name


def create_live_token(servo_names: dict[int, str], macros: list[dict]) -> dict:
    """Mints a single-use ephemeral token for the browser. The key never leaves the server."""
    provider = PROVIDERS["gemini"]
    if not provider.available():
        raise PermissionError("Live voice needs a Gemini API key. Add one in Settings.")
    key = _key(provider.key_env)
    now = datetime.now(timezone.utc)
    try:
        name = _sdk_create_token(key, live_token_config(live_model(), now, servo_names, macros))
    except Exception as exc:
        detail = str(exc)
        if key:
            detail = detail.replace(key, "••••")
        raise LiveTokenError(f"Gemini Live session failed ({type(exc).__name__}: {detail[:160]})") from exc
    return {"token": name, "model": live_model(), "expires_at": (now + timedelta(minutes=LIVE_TOKEN_MINUTES)).isoformat()}
