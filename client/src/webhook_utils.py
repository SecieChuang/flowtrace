"""Shared webhook and utility functions for Flowtrace notification scripts."""
import base64
import copy
import hashlib
import hmac
import json
import re
import sys
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Sequence
from urllib.parse import urlparse

import requests

JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def safe_print(text: Any = "") -> None:
    output = str(text)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        sys.stdout.write(output + "\n")
    except UnicodeEncodeError:
        sys.stdout.buffer.write((output + "\n").encode(encoding, errors="replace"))


def parse_json_response(response_text: str) -> Dict[str, Any]:
    text = str(response_text or "").strip()
    if not text:
        raise RuntimeError("AI returned an empty response")

    match = JSON_BLOCK_RE.search(text)
    candidates = [match.group(1)] if match else []
    candidates.append(text)

    if "{" in text and "}" in text:
        candidates.append(text[text.find("{"):text.rfind("}") + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise RuntimeError("AI did not return valid JSON")


def is_feishu_webhook(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.endswith("open.feishu.cn") and "/open-apis/bot/" in parsed.path


def mask_webhook_url(url: str) -> str:
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if path_parts:
        token = path_parts[-1]
        if len(token) > 8:
            path_parts[-1] = f"{token[:4]}...{token[-4:]}"
        else:
            path_parts[-1] = "***"
    masked_path = "/" + "/".join(path_parts) if path_parts else parsed.path
    return f"{parsed.scheme}://{parsed.netloc}{masked_path}"


def build_feishu_signature(secret: str, timestamp: int) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _validate_webhook_response(url: str, response: requests.Response) -> None:
    if response.status_code >= 400:
        raise RuntimeError(f"Webhook request failed: status={response.status_code}")

    if is_feishu_webhook(url):
        try:
            body = response.json()
        except Exception as exc:
            raise RuntimeError("Feishu webhook returned a non-JSON response") from exc
        code = body.get("code")
        if code != 0:
            message = body.get("msg") or body.get("message") or "unknown error"
            raise RuntimeError(f"Feishu webhook rejected message: code={code}, message={message}")


def send_webhook(
    url: str,
    payload: Dict[str, Any],
    timeout_seconds: int,
    secret: str = "",
    retry_count: int = 2,
    retry_delay_seconds: float = 2.0,
    user_agent: str = "FlowtraceBot/1.0",
    feishu_card_builder: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> None:
    if retry_count < 0:
        raise RuntimeError("webhook retry_count must be greater than or equal to 0")
    if retry_delay_seconds < 0:
        raise RuntimeError("webhook retry_delay_seconds must be greater than or equal to 0")

    if is_feishu_webhook(url) and feishu_card_builder:
        base_payload = feishu_card_builder(payload)
    else:
        base_payload = payload

    request_headers = {
        "User-Agent": (str(user_agent or "").strip() or "FlowtraceBot/1.0"),
        "Content-Type": "application/json",
    }
    masked_url = mask_webhook_url(url)
    last_error: Exception | None = None

    for attempt in range(retry_count + 1):
        request_payload = copy.deepcopy(base_payload)
        if is_feishu_webhook(url) and secret:
            timestamp = int(datetime.now().timestamp())
            request_payload["timestamp"] = str(timestamp)
            request_payload["sign"] = build_feishu_signature(secret, timestamp)

        try:
            response = requests.post(url, json=request_payload, timeout=timeout_seconds, headers=request_headers)
            _validate_webhook_response(url, response)
            return
        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
            if attempt >= retry_count:
                break
            delay = retry_delay_seconds * (2 ** attempt)
            safe_print(f"WEBHOOK_RETRY|url={masked_url}|attempt={attempt + 1}|next_delay={delay:.1f}s|reason={exc}")
            time.sleep(delay)

    raise RuntimeError(f"Webhook delivery failed after {retry_count + 1} attempts: {last_error}")
