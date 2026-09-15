"""Unified AI provider module with multi-provider support and fallback.

Supports: dashscope, zhipu (智谱), minimax, mistralai
"""
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from webhook_utils import safe_print

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.json"


def load_config(path: Path) -> Dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def get_provider_config(cfg: Dict, provider: str) -> Optional[Dict]:
    """Get provider configuration from config."""
    providers = cfg.get("providers", {})
    return providers.get(provider.lower())


def get_provider_api_key(cfg: Dict, provider: str) -> str:
    """Get API key for a provider."""
    prov = get_provider_config(cfg, provider)
    if prov:
        return str(prov.get("api_key", "")).strip()
    return ""


def get_provider_vision_model(cfg: Dict, provider: str) -> str:
    """Get vision model for a specific provider."""
    prov = get_provider_config(cfg, provider)
    if prov:
        return str(prov.get("vision_model", "")).strip()
    return ""


def get_provider_text_model(cfg: Dict, provider: str) -> str:
    """Get text model for a specific provider."""
    prov = get_provider_config(cfg, provider)
    if prov:
        return str(prov.get("text_model", "")).strip()
    return ""


def get_primary_provider(cfg: Dict) -> str:
    """Get primary AI provider from config."""
    return str(cfg.get("ai_primary_provider", "")).strip().lower()


def get_fallback_providers(cfg: Dict) -> List[str]:
    """Get fallback AI providers from config (supports multiple)."""
    fallback = cfg.get("ai_fallback_providers")
    if isinstance(fallback, list):
        return [str(p).strip().lower() for p in fallback if p]
    if fallback is not None:
        single = str(fallback).strip().lower()
        return [single] if single else []
    # Support old single provider format for backward compatibility
    single = str(cfg.get("ai_fallback_provider", "")).strip().lower()
    return [single] if single else []


# =============================================================================
# Provider implementations
# =============================================================================

def _call_dashscope(api_key: str, model: str, messages: Sequence[Dict], is_vision: bool = False):
    """Call DashScope API."""
    import dashscope

    dashscope.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"

    if is_vision:
        return dashscope.MultiModalConversation.call(
            api_key=api_key,
            model=model,
            messages=list(messages),
        )
    else:
        # Convert messages format for text models
        formatted_messages = [
            {"role": item["role"], "content": [{"text": item["content"]}]}
            for item in messages
        ]
        return dashscope.Generation.call(
            api_key=api_key,
            model=model,
            messages=formatted_messages,
            result_format="message",
        )


def _call_zhipu(api_key: str, model: str, messages: Sequence[Dict], is_vision: bool = False):
    """Call Zhipu (智谱) API."""
    from zhipuai import ZhipuAI

    client = ZhipuAI(api_key=api_key)

    # Convert to zhipu format
    formatted_messages = [
        {"role": item["role"], "content": item["content"]}
        for item in messages
    ]

    if is_vision:
        # Vision models need special handling
        # Convert to zhipu's multimodal format
        content = []
        for msg in messages:
            if isinstance(msg.get("content"), list):
                for item in msg["content"]:
                    if isinstance(item, dict):
                        if "image" in item:
                            # Handle base64 image
                            content.append({"type": "image_url", "image_url": {"url": item["image"]}})
                        elif "text" in item:
                            content.append({"type": "text", "text": item["text"]})
            elif isinstance(msg.get("content"), str):
                content.append({"type": "text", "text": msg["content"]})

        formatted_messages = [{"role": msg["role"], "content": content}]

    return client.chat.completions.create(
        model=model,
        messages=formatted_messages,
    )


def _call_minimax(api_key: str, model: str, messages: Sequence[Dict], is_vision: bool = False):
    """Call MiniMax API."""
    import openai

    client = openai.OpenAI(
        api_key=api_key,
        base_url="https://api.minimax.chat/v1",
    )

    # Convert messages format for minimax
    formatted_messages = [
        {"role": item["role"], "content": item["content"]}
        for item in messages
    ]

    return client.chat.completions.create(
        model=model,
        messages=formatted_messages,
    )


def _call_mistralai(api_key: str, model: str, messages: Sequence[Dict], is_vision: bool = False):
    """Call Mistral AI API."""
    from mistralai.client import Mistral

    client = Mistral(api_key=api_key)

    if is_vision:
        # For vision models, pass image directly in image_url
        content_parts = []
        for msg in messages:
            if isinstance(msg.get("content"), list):
                for item in msg["content"]:
                    if isinstance(item, dict):
                        if "image" in item:
                            # Handle base64 image - pass directly as data URL
                            image_b64 = item["image"]
                            if image_b64.startswith("data:image"):
                                # Extract base64 part
                                image_b64 = image_b64.split(",", 1)[1]
                            # Use data URL format for direct base64
                            content_parts.append({
                                "type": "image_url",
                                "image_url": f"data:image/jpeg;base64,{image_b64}"
                            })
                        elif "text" in item:
                            content_parts.append({"type": "text", "text": item["text"]})
            elif isinstance(msg.get("content"), str):
                content_parts.append({"type": "text", "text": msg["content"]})

        formatted_messages = [{"role": msg.get("role", "user"), "content": content_parts}]
    else:
        # Convert messages format for text models
        formatted_messages = [
            {"role": item["role"], "content": item["content"]}
            for item in messages
        ]

    return client.chat.complete(
        model=model,
        messages=formatted_messages,
        stream=False,
    )


PROVIDER_CALLS = {
    "dashscope": _call_dashscope,
    "zhipu": _call_zhipu,
    "minimax": _call_minimax,
    "mistralai": _call_mistralai,
}


def parse_response(response, is_vision: bool = False) -> str:
    """Parse API response to extract text content."""
    # DashScope format
    if hasattr(response, "output"):
        content = response.output.choices[0].message.content
        if is_vision:
            if isinstance(content, list):
                return "\n".join(
                    str(item.get("text", item)) for item in content if item
                ).strip()
        else:
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("text"):
                        parts.append(str(item["text"]))
                    else:
                        parts.append(str(item))
                return "\n".join(parts).strip()
            return str(content).strip()

    # Zhipu/MiniMax format (OpenAI-compatible)
    if hasattr(response, "choices"):
        content = response.choices[0].message.content
        return str(content).strip()

    # Fallback
    return str(response)


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
        messages: Messages to send
        timeout_seconds: Request timeout
        is_vision: Whether this is a vision model call

    Returns:
        Response text from the AI

    Raises:
        RuntimeError: If all providers fail
    """
    # Determine providers to try
    primary = get_primary_provider(cfg)
    fallbacks = get_fallback_providers(cfg)

    providers_to_try = [primary] + [p for p in fallbacks if p and p != primary]

    errors = []

    for provider in providers_to_try:
        api_key = get_provider_api_key(cfg, provider)
        if not api_key:
            errors.append(f"Provider {provider}: no API key")
            continue

        call_func = PROVIDER_CALLS.get(provider)
        if not call_func:
            errors.append(f"Provider {provider}: not supported")
            continue

        # Get model from provider config based on is_vision
        if is_vision:
            actual_model = get_provider_vision_model(cfg, provider)
        else:
            actual_model = get_provider_text_model(cfg, provider)
        if not actual_model:
            errors.append(f"Provider {provider}: no {('vision' if is_vision else 'text')} model configured")
            continue

        try:
            safe_print(f"AI_CALL|provider={provider}|model={actual_model}|is_vision={is_vision}")
            result = _call_with_timeout(call_func, api_key, actual_model, messages, timeout_seconds, is_vision)
            return result
        except Exception as exc:
            error_msg = f"Provider {provider} failed: {exc}"
            safe_print(f"AI_CALL_FAILED|provider={provider}|error={exc}")
            errors.append(error_msg)
            continue

    raise RuntimeError(f"All AI providers failed: {'; '.join(errors)}")


def _call_with_timeout(
    call_func,
    api_key: str,
    model: str,
    messages: Sequence[Dict],
    timeout_seconds: int,
    is_vision: bool,
) -> str:
    """Execute API call with timeout."""
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(call_func, api_key, model, messages, is_vision)
    try:
        response = future.result(timeout=timeout_seconds)
    except FuturesTimeoutError as exc:
        # Python 无法强杀线程，shutdown(wait=False) 让超时真正按时返回，残留线程自行结束
        executor.shutdown(wait=False)
        raise RuntimeError(f"Request timed out after {timeout_seconds}s") from exc
    executor.shutdown(wait=True)

    # Check for errors
    status_code = getattr(response, "status_code", None)
    if status_code and status_code != 200:
        code = getattr(response, "code", "unknown")
        message = getattr(response, "message", "unknown error")
        raise RuntimeError(f"API error: status={status_code}, code={code}, message={message}")

    return parse_response(response, is_vision)


# =============================================================================
# Provider availability test
# =============================================================================

def test_provider(cfg: Dict, provider: str, model: str, timeout_seconds: int = 30, is_vision: bool = None) -> Tuple[bool, str]:
    """Test if a provider/model combination is available."""
    api_key = get_provider_api_key(cfg, provider)
    if not api_key:
        return False, "No API key"

    call_func = PROVIDER_CALLS.get(provider.lower())
    if not call_func:
        return False, f"Provider {provider} not supported"

    # Determine if vision model - use provided value or auto-detect from config
    if is_vision is None:
        vision_model = get_provider_vision_model(cfg, provider)
        is_vision = bool(vision_model and model == vision_model)

    # Build test messages
    if is_vision:
        # 现场生成一张小测试图（无需随仓库附带测试资产）
        import base64
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (64, 64), (79, 130, 222)).save(buf, format="JPEG")
        image_data = base64.b64encode(buf.getvalue()).decode("utf-8")
        messages = [{"role": "user", "content": [{"image": f"data:image/jpeg;base64,{image_data}"}, {"text": "What is this?"}]}]
    else:
        messages = [{"role": "user", "content": "Say 'OK' if you can read this."}]

    try:
        result = _call_with_timeout(call_func, api_key, model, messages, timeout_seconds, is_vision)
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

        # Test provider's vision model
        vision_model = get_provider_vision_model(cfg, provider)
        if vision_model:
            success, message = test_provider(cfg, provider, vision_model, timeout_seconds, is_vision=True)
            results.append({
                "provider": provider,
                "model": vision_model,
                "type": "vision",
                "is_default": True,
                "success": success,
                "message": message,
            })

        # Test provider's text model
        text_model = get_provider_text_model(cfg, provider)
        if text_model:
            success, message = test_provider(cfg, provider, text_model, timeout_seconds, is_vision=False)
            results.append({
                "provider": provider,
                "model": text_model,
                "type": "text",
                "is_default": True,
                "success": success,
                "message": message,
            })

    return results
