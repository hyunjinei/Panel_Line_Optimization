"""Optional OpenAI-compatible parser for LLM Connect."""

# [AGENT-ADD] The parser is optional; deterministic parsing remains the safe fallback.
# [AGENT-EDIT] Groq is supported through the OpenAI-compatible chat API.

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Sequence

from .parser import parse_natural_language_request
from .prompts import build_request_parser_prompt
from .schemas import ScheduleEditRequest
from .validation import request_from_dict, validate_request


GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OLLAMA_BASE_URL = "http://localhost:11434/v1"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_OLLAMA_MODEL = "llama3.1:8b"


@dataclass(frozen=True)
class LLMClientConfig:
    provider: str
    api_key: str
    model: str
    base_url: str | None
    api_style: str


def _extract_json_object(text: str) -> dict:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    left = raw.find("{")
    right = raw.rfind("}")
    if left >= 0 and right > left:
        parsed = json.loads(raw[left:right + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("LLM response did not contain a JSON object.")


def _normalize_provider(provider: str | None) -> str:
    raw = provider or os.environ.get("PBS_LLM_PROVIDER") or "openai"
    return str(raw).strip().lower().replace("-", "_")


def _resolve_client_config(
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
) -> LLMClientConfig:
    selected_provider = _normalize_provider(provider)
    env_key = api_key_env or os.environ.get("PBS_LLM_API_KEY_ENV")
    if not env_key:
        if selected_provider == "groq":
            env_key = "GROQ_API_KEY"
        elif selected_provider == "ollama":
            env_key = "OLLAMA_API_KEY"
        else:
            env_key = "OPENAI_API_KEY"
    api_key = os.environ.get(env_key)
    if selected_provider == "ollama" and not api_key:
        api_key = "ollama"
    if not api_key:
        raise RuntimeError(f"{env_key} is not set.")

    provider_base_url = base_url or os.environ.get("PBS_LLM_BASE_URL")
    if not provider_base_url and selected_provider == "groq":
        provider_base_url = GROQ_BASE_URL
    if not provider_base_url and selected_provider == "ollama":
        provider_base_url = OLLAMA_BASE_URL

    if selected_provider == "groq":
        provider_model_env = "GROQ_MODEL"
    elif selected_provider == "ollama":
        provider_model_env = "OLLAMA_MODEL"
    else:
        provider_model_env = "OPENAI_MODEL"
    if selected_provider == "groq":
        selected_model = model or os.environ.get(provider_model_env) or os.environ.get("PBS_LLM_MODEL") or DEFAULT_GROQ_MODEL
    elif selected_provider == "ollama":
        selected_model = model or os.environ.get(provider_model_env) or os.environ.get("PBS_LLM_MODEL") or DEFAULT_OLLAMA_MODEL
    else:
        selected_model = model or os.environ.get(provider_model_env) or os.environ.get("PBS_LLM_MODEL") or DEFAULT_OPENAI_MODEL

    default_style = "chat" if selected_provider in {"groq", "ollama", "openai_compatible"} else "responses"
    api_style = str(os.environ.get("PBS_LLM_API_STYLE") or default_style).strip().lower()
    return LLMClientConfig(
        provider=selected_provider,
        api_key=api_key,
        model=selected_model,
        base_url=provider_base_url,
        api_style=api_style,
    )


def _response_text_from_chat_completion(client: Any, config: LLMClientConfig, prompt: str) -> str:
    messages = [
        {
            "role": "system",
            "content": "Return only one valid JSON object that matches the requested schema.",
        },
        {
            "role": "user",
            "content": prompt,
        },
    ]
    # [AGENT-ADD] Groq and other OpenAI-compatible providers may not expose the
    # newer Responses API, so chat completions are the portable path.
    try:
        response = client.chat.completions.create(
            model=config.model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        message = str(exc).lower()
        if "response_format" not in message and "json" not in message and "unsupported" not in message:
            raise
        response = client.chat.completions.create(
            model=config.model,
            messages=messages,
            temperature=0,
        )
    return str(response.choices[0].message.content or "")


def _response_text_from_responses_api(client: Any, config: LLMClientConfig, prompt: str) -> str:
    response = client.responses.create(
        model=config.model,
        input=prompt,
        temperature=0,
    )
    return str(response.output_text or "")


def parse_with_openai(
    user_request: str,
    *,
    current_sequence: Sequence[int] | None = None,
    model: str | None = None,
    provider: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
) -> ScheduleEditRequest:
    """Parse one Korean edit request through an OpenAI-compatible LLM."""

    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError("openai package is not installed in this environment.") from exc

    config = _resolve_client_config(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key_env=api_key_env,
    )
    client_kwargs: dict[str, Any] = {"api_key": config.api_key}
    if config.base_url:
        client_kwargs["base_url"] = config.base_url
    client = OpenAI(**client_kwargs)
    prompt = build_request_parser_prompt(user_request, current_sequence or [])
    if config.api_style == "chat":
        response_text = _response_text_from_chat_completion(client, config, prompt)
    else:
        response_text = _response_text_from_responses_api(client, config, prompt)
    payload = _extract_json_object(response_text)
    parsed = request_from_dict(payload, raw_request=user_request)
    errors, _ = validate_request(parsed, sequence=current_sequence)
    if errors:
        raise ValueError("LLM parser produced invalid request: " + " / ".join(errors))
    return parsed


def parse_request_auto(
    user_request: str,
    *,
    current_sequence: Sequence[int] | None = None,
    parser_mode: str = "deterministic",
    model: str | None = None,
    provider: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
) -> ScheduleEditRequest:
    """Select deterministic or OpenAI-compatible parser with deterministic fallback."""

    mode = str(parser_mode or "deterministic").strip().lower()
    if mode in {"deterministic", "rule", "rules"}:
        return parse_natural_language_request(user_request)
    if mode in {"llm", "openai", "groq", "ollama", "openai_compatible"}:
        return parse_with_openai(
            user_request,
            current_sequence=current_sequence,
            model=model,
            provider=provider or (mode if mode in {"groq", "ollama", "openai_compatible"} else None),
            base_url=base_url,
            api_key_env=api_key_env,
        )
    if mode == "auto":
        try:
            return parse_with_openai(
                user_request,
                current_sequence=current_sequence,
                model=model,
                provider=provider,
                base_url=base_url,
                api_key_env=api_key_env,
            )
        except Exception:
            return parse_natural_language_request(user_request)
    raise ValueError(f"unknown parser mode: {parser_mode}")
