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
TEXT_ONLY = "Nothing was moved."
MAX_AUDIO_BYTES = 4 * 1024 * 1024  # About four minutes of browser Opus; demo clips are seconds long.
AUDIO_TYPES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav", "audio/aac", "audio/flac", "audio/opus"}


class ModelOutput(BaseModel):
    """The JSON contract every provider must answer with."""
    reply: str = Field(default="", max_length=600)
    action: Literal["none", "pose", "macro", "stop"]
    positions: list[int] | None = None
    label: str | None = Field(default=None, max_length=60)
    macro_id: str | None = None


OUTPUT_SCHEMA = {"type": "object", "required": ["reply", "action"], "properties": {
    "reply": {"type": "string"},
    "action": {"type": "string", "enum": ["none", "pose", "macro", "stop"]},
    "positions": {"type": "array", "items": {"type": "integer"}},
    "label": {"type": "string"},
    "macro_id": {"type": "string"},
}}


@dataclass(frozen=True)
class Command:
    type: Literal["pose", "macro", "stop"]
    positions: dict[int, int] | None = None
    label: str = ""
    macro_id: str = ""
    name: str = ""

    def public(self) -> dict:
        if self.type == "pose":
            return {"type": "pose", "positions": self.positions, "label": self.label}
        return {"type": self.type, "macro_id": self.macro_id, "name": self.name}


def system_prompt(servo_names: dict[int, str], macros: list[dict]) -> str:
    servos = "\n".join(f"  {sid}: {name}" for sid, name in servo_names.items())
    return f"""You are the assistant for handoff, a six-servo smart glove studio running in SIMULATION.
You never move anything yourself. You draft at most one proposal; a human must click Confirm.
Servos, in this exact order (IDs 1-6):
{servos}
A position is an integer percentage: 0 = finger fully open/relaxed, 100 = fully flexed.
Answer ONLY with one JSON object, no prose around it:
{{"reply": short friendly sentence, "action": "none"|"pose"|"macro"|"stop",
  "positions": six integers 0-100 in servo ID order (only for "pose"),
  "label": short pose name (only for "pose"),
  "macro_id": id of a saved macro (only for "macro")}}
Prefer "macro" when a saved macro matches the request. Use "stop" when the user asks to stop, halt or freeze.
Use "none" for questions or anything that is not a hand gesture. Never claim the hand already moved; say it is ready for confirmation.
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
