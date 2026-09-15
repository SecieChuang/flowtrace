"""Unified AI provider: any OpenAI-compatible endpoint.

All providers are plain HTTP calls to an OpenAI-compatible API — no vendor SDKs.
Provider names are user-defined; each provider entry carries its own base_url,
models and wire protocol ("chat" = /chat/completions, "responses" = /responses).

Config shape (client/config/config.json):

    "ai_primary_provider": "my-provider",
    "ai_fallback_providers": ["backup-provider"],
    "providers": {
        "my-provider": {
            "base_url": "https://api.example.com/v1",
            "api_key": "...",
            "wire_api": "chat",
            "text_model": "some-text-model",
            "vision_model": "some-vision-model"
        }
    }
"""
import base64
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

from webhook_utils import safe_print

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.json"

WIRE_CHAT = "chat"
WIRE_RESPONSES = "responses"


def load_config(path: Path) -> Dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def get_provider_config(cfg: Dict, provider: str) -> Optional[Dict]:
    """Get provider configuration by name (case-insensitive)."""
    providers = cfg.get("providers", {})
    if not isinstance(providers, dict):
        return None
    name = str(provider).strip().lower()
    for key, value in providers.items():
        if key.strip().lower() == name and isinstance(value, dict):
            return value
    return None


def _provider_field(cfg: Dict, provider: str, field: str) -> str:
    entry = get_provider_config(cfg, provider)
    if entry:
        return str(entry.get(field, "")).strip()
    return ""


def get_provider_api_key(cfg: Dict, provider: str) -> str:
    """Get API key for a provider."""
    return _provider_field(cfg, provider, "api_key")


def get_provider_base_url(cfg: Dict, provider: str) -> str:
    """Get base URL for a provider."""
    return _provider_field(cfg, provider, "base_url")


def get_provider_wire_api(cfg: Dict, provider: str) -> str:
    """Get wire protocol: "chat" (default) or "responses"."""
    return _provider_field(cfg, provider, "wire_api") or WIRE_CHAT


def get_provider_vision_model(cfg: Dict, provider: str) -> str:
    """Get vision model for a specific provider."""
    return _provider_field(cfg, provider, "vision_model")


def get_provider_text_model(cfg: Dict, provider: str) -> str:
    """Get text model for a specific provider."""
    return _provider_field(cfg, provider, "text_model")


def get_primary_provider(cfg: Dict) -> str:
    """Get primary AI provider name from config."""
    return str(cfg.get("ai_primary_provider", "")).strip()


def get_fallback_providers(cfg: Dict) -> List[str]:
    """Get fallback AI provider names from config (supports multiple)."""
    fallback = cfg.get("ai_fallback_providers")
    if isinstance(fallback, list):
        return [str(p).strip() for p in fallback if p]
    if fallback is not None:
        single = str(fallback).strip()
        return [single] if single else []
    # Support old single provider format for backward compatibility
    single = str(cfg.get("ai_fallback_provider", "")).strip()
    return [single] if single else []


# =============================================================================
# Wire protocol payloads (OpenAI-compatible)
# =============================================================================

def _normalize_parts(content) -> List[Dict]:
    """Internal content list ([{"image": data_url}, {"text": ...}]) → uniform parts."""
    parts = []
    for item in content:
        if isinstance(item, dict):
            if "image" in item:
                parts.append({"image": item["image"]})
            elif "text" in item:
                parts.append({"text": item["text"]})
    return parts


def build_chat_payload(model: str, messages: Sequence[Dict]) -> Dict:
    """Build /chat/completions request body."""
    out = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            out.append({"role": msg.get("role", "user"), "content": content})
            continue
        parts = []
        for part in _normalize_parts(content):
            if "image" in part:
                parts.append({"type": "image_url", "image_url": {"url": part["image"]}})
            else:
                parts.append({"type": "text", "text": part["text"]})
        out.append({"role": msg.get("role", "user"), "content": parts})
    return {"model": model, "messages": out}


def build_responses_payload(model: str, messages: Sequence[Dict]) -> Dict:
    """Build /responses request body."""
    items = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            parts = [{"type": "input_text", "text": content}]
        else:
            parts = []
            for part in _normalize_parts(content):
                if "image" in part:
                    parts.append({"type": "input_image", "image_url": part["image"]})
                else:
                    parts.append({"type": "input_text", "text": part["text"]})
        items.append({"role": msg.get("role", "user"), "content": parts})
    return {"model": model, "input": items}


def extract_text(data: Dict, wire_api: str) -> str:
    """Extract plain text from a parsed response body."""
    if wire_api == WIRE_RESPONSES:
        output_text = data.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()
        for item in data.get("output") or []:
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("text"):
                    return str(part["text"]).strip()
        raise RuntimeError("responses API returned no text")

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"chat API returned no choices: {str(data)[:200]}")
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts).strip()
    if content is None:
        raise RuntimeError("chat API returned empty message content")
    return str(content).strip()


# =============================================================================
# HTTP call
# =============================================================================

def call_provider(entry: Dict, model: str, messages: Sequence[Dict], timeout_seconds: int) -> str:
    """Call one provider entry and return the response text."""
    base_url = str(entry.get("base_url", "")).strip().rstrip("/")
    if not base_url:
        raise RuntimeError("missing base_url in provider config")
    api_key = str(entry.get("api_key", "")).strip()
    wire_api = str(entry.get("wire_api", "")).strip() or WIRE_CHAT

    if wire_api == WIRE_RESPONSES:
        payload = build_responses_payload(model, messages)
        url = f"{base_url}/responses"
    elif wire_api == WIRE_CHAT:
        payload = build_chat_payload(model, messages)
        url = f"{base_url}/chat/completions"
    else:
        raise RuntimeError(f"unsupported wire_api: {wire_api} (expected chat/responses)")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    resp = requests.post(url, json=payload, headers=headers, timeout=timeout_seconds)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    return extract_text(resp.json(), wire_api)


# =============================================================================
# Main unified call function
# =============================================================================

def call_ai(
    cfg: Dict,
    messages: Sequence[Dict],
    timeout_seconds: int = 60,
    is_vision: bool = False,
) -> str:
    """Call AI API with provider fallback support.

    Args:
        cfg: Configuration dictionary
        messages: Messages to send (text string content, or parts list with text/image)
        timeout_seconds: Request timeout
        is_vision: Whether this is a vision model call

    Returns:
        Response text from the AI

    Raises:
        RuntimeError: If all providers fail
    """
    primary = get_primary_provider(cfg)
    fallbacks = get_fallback_providers(cfg)

    providers_to_try = [primary] + [p for p in fallbacks if p and p != primary]

    errors = []

    for provider in providers_to_try:
        entry = get_provider_config(cfg, provider)
        if not entry:
            errors.append(f"Provider {provider}: not configured in providers")
            continue
        if not get_provider_api_key(cfg, provider):
            errors.append(f"Provider {provider}: no API key")
            continue

        model = get_provider_vision_model(cfg, provider) if is_vision else get_provider_text_model(cfg, provider)
        if not model:
            errors.append(f"Provider {provider}: no {('vision' if is_vision else 'text')} model configured")
            continue

        try:
            safe_print(f"AI_CALL|provider={provider}|model={model}|is_vision={is_vision}")
            return call_provider(entry, model, messages, timeout_seconds)
        except Exception as exc:
            safe_print(f"AI_CALL_FAILED|provider={provider}|error={exc}")
            errors.append(f"Provider {provider} failed: {exc}")
            continue

    raise RuntimeError(f"All AI providers failed: {'; '.join(errors)}")


# =============================================================================
# Provider availability test
# =============================================================================

def test_provider(cfg: Dict, provider: str, model: str, timeout_seconds: int = 30, is_vision: bool = None) -> Tuple[bool, str]:
    """Test if a provider/model combination is available."""
    entry = get_provider_config(cfg, provider)
    if not entry:
        return False, f"Provider {provider} not configured"
    if not get_provider_api_key(cfg, provider):
        return False, "No API key"
    if not get_provider_base_url(cfg, provider):
        return False, "No base_url"

    if is_vision is None:
        vision_model = get_provider_vision_model(cfg, provider)
        is_vision = bool(vision_model and model == vision_model)

    if is_vision:
        # 现场生成一张小测试图（无需随仓库附带测试资产）
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (64, 64), (79, 130, 222)).save(buf, format="JPEG")
        image_data = base64.b64encode(buf.getvalue()).decode("utf-8")
        messages = [{"role": "user", "content": [{"image": f"data:image/jpeg;base64,{image_data}"}, {"text": "What is this?"}]}]
    else:
        messages = [{"role": "user", "content": "Say 'OK' if you can read this."}]

    try:
        result = call_provider(entry, model, messages, timeout_seconds)
        return True, f"OK - {result[:50]}"
    except Exception as e:
        return False, str(e)


def test_all_providers(cfg: Dict, timeout_seconds: int = 30) -> List[Dict[str, Any]]:
    """Test all configured providers and models."""
    results = []

    primary = get_primary_provider(cfg)
    fallbacks = get_fallback_providers(cfg)

    providers_to_test = {primary}
    providers_to_test.update(fallbacks)

    for provider in providers_to_test:
        if not provider:
            continue

        vision_model = get_provider_vision_model(cfg, provider)
        if vision_model:
            success, message = test_provider(cfg, provider, vision_model, timeout_seconds, is_vision=True)
            results.append({
                "provider": provider,
                "model": vision_model,
                "type": "vision",
                "success": success,
                "message": message,
            })

        text_model = get_provider_text_model(cfg, provider)
        if text_model:
            success, message = test_provider(cfg, provider, text_model, timeout_seconds, is_vision=False)
            results.append({
                "provider": provider,
                "model": text_model,
                "type": "text",
                "success": success,
                "message": message,
            })

    return results
