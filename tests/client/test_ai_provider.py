"""tests for client/src/ai_provider.py — OpenAI 兼容 provider：配置解析、双协议载荷、文本提取、fallback。"""

import json

import pytest
import requests

import ai_provider


def _cfg(**kwargs):
    base = {"providers": {}}
    base.update(kwargs)
    return base


def _entry(**kwargs):
    entry = {
        "api_key": "k",
        "base_url": "https://api.example.com/v1",
        "wire_api": "chat",
        "text_model": "text-m",
        "vision_model": "vision-m",
    }
    entry.update(kwargs)
    return entry


# ---------------------------------------------------------------------------
# 配置解析
# ---------------------------------------------------------------------------

def test_get_provider_config_case_insensitive():
    """provider 名大小写不敏感，自定义名字也能查到。"""
    cfg = _cfg(providers={"My-Custom": _entry()})
    assert ai_provider.get_provider_config(cfg, "my-custom") == ai_provider.get_provider_config(cfg, "MY-CUSTOM")


def test_get_provider_config_missing():
    """未配置的 provider 返回 None。"""
    assert ai_provider.get_provider_config(_cfg(), "nope") is None


def test_field_accessors():
    """api_key / base_url / 模型的读取；wire_api 缺省回退 chat。"""
    cfg = _cfg(providers={"p": _entry()})
    assert ai_provider.get_provider_api_key(cfg, "p") == "k"
    assert ai_provider.get_provider_base_url(cfg, "p") == "https://api.example.com/v1"
    assert ai_provider.get_provider_text_model(cfg, "p") == "text-m"
    assert ai_provider.get_provider_vision_model(cfg, "p") == "vision-m"
    assert ai_provider.get_provider_wire_api(cfg, "p") == "chat"
    no_wire = _cfg(providers={"p": _entry(wire_api="")})
    assert ai_provider.get_provider_wire_api(no_wire, "p") == "chat"


def test_get_fallback_providers_list():
    """fallback 列表原样返回，空项被过滤。"""
    cfg = _cfg(ai_fallback_providers=["A", "", " B "])
    assert ai_provider.get_fallback_providers(cfg) == ["A", "B"]


def test_get_fallback_providers_legacy_single():
    """兼容路径：配置里只有旧版单数键 ai_fallback_provider 时也能读出。"""
    assert ai_provider.get_fallback_providers(_cfg(ai_fallback_provider="Zhipu")) == ["Zhipu"]


def test_get_fallback_providers_string_value():
    """新键误写成字符串时按单个 provider 名处理，而不是静默忽略。"""
    assert ai_provider.get_fallback_providers(_cfg(ai_fallback_providers="Zhipu")) == ["Zhipu"]


def test_get_fallback_providers_empty():
    """无 fallback 配置时返回空列表。"""
    assert ai_provider.get_fallback_providers(_cfg()) == []


# ---------------------------------------------------------------------------
# 载荷构造（chat / responses 两种 wire 协议）
# ---------------------------------------------------------------------------

def test_build_chat_payload_text_only():
    """字符串 content 原样透传。"""
    payload = ai_provider.build_chat_payload("m", [{"role": "user", "content": "hello"}])
    assert payload == {"model": "m", "messages": [{"role": "user", "content": "hello"}]}


def test_build_chat_payload_vision_parts():
    """内部 {image}/{text} 部件转为 OpenAI image_url/text 格式。"""
    messages = [{"role": "user", "content": [{"image": "data:image/jpeg;base64,AA"}, {"text": "hi"}]}]
    payload = ai_provider.build_chat_payload("vm", messages)
    parts = payload["messages"][0]["content"]
    assert parts[0] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AA"}}
    assert parts[1] == {"type": "text", "text": "hi"}


def test_build_responses_payload_text_only():
    """responses 协议：字符串 content 转 input_text。"""
    payload = ai_provider.build_responses_payload("m", [{"role": "user", "content": "hello"}])
    assert payload == {"model": "m", "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}]}


def test_build_responses_payload_vision_parts():
    """responses 协议：图片转 input_image。"""
    messages = [{"role": "user", "content": [{"image": "data:image/jpeg;base64,AA"}, {"text": "hi"}]}]
    payload = ai_provider.build_responses_payload("vm", messages)
    parts = payload["input"][0]["content"]
    assert parts[0] == {"type": "input_image", "image_url": "data:image/jpeg;base64,AA"}
    assert parts[1] == {"type": "input_text", "text": "hi"}


# ---------------------------------------------------------------------------
# 响应文本提取
# ---------------------------------------------------------------------------

def test_extract_text_chat_string():
    """chat 协议：常规字符串 content。"""
    data = {"choices": [{"message": {"content": "  done  "}}]}
    assert ai_provider.extract_text(data, "chat") == "done"


def test_extract_text_chat_parts():
    """chat 协议：content 为部件列表时拼接文本。"""
    data = {"choices": [{"message": {"content": [{"type": "text", "text": "a"}, "b"]}}]}
    assert ai_provider.extract_text(data, "chat") == "a\nb"


def test_extract_text_chat_no_choices():
    """chat 协议：无 choices 报错。"""
    with pytest.raises(RuntimeError):
        ai_provider.extract_text({"error": "x"}, "chat")


def test_extract_text_responses_output_text():
    """responses 协议：优先 output_text 捷径。"""
    assert ai_provider.extract_text({"output_text": " ok "}, "responses") == "ok"


def test_extract_text_responses_nested():
    """responses 协议：从 output[].content[] 里找文本。"""
    data = {"output": [{"content": [{"type": "output_text", "text": "nested"}]}]}
    assert ai_provider.extract_text(data, "responses") == "nested"


def test_extract_text_responses_empty():
    """responses 协议：完全没有文本时报错。"""
    with pytest.raises(RuntimeError):
        ai_provider.extract_text({"output": []}, "responses")


# ---------------------------------------------------------------------------
# call_provider（HTTP 层，mock requests.post）
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code = status
        self._data = data or {}
        self.text = text

    def json(self):
        return self._data


def test_call_provider_chat_endpoint_and_auth(monkeypatch):
    """chat 协议打到 /chat/completions，带 Bearer 头，base_url 末尾斜杠被去掉。"""
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, payload=json, headers=headers, timeout=timeout)
        return _FakeResp(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    out = ai_provider.call_provider(_entry(base_url="https://api.example.com/v1/"), "m", [{"role": "user", "content": "hi"}], 30)
    assert out == "ok"
    assert seen["url"] == "https://api.example.com/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer k"
    assert seen["timeout"] == 30
    assert seen["payload"]["model"] == "m"


def test_call_provider_responses_endpoint(monkeypatch):
    """wire_api=responses 时打到 /responses 并用 responses 载荷。"""
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, payload=json)
        return _FakeResp(200, {"output_text": "ok"})

    monkeypatch.setattr(requests, "post", fake_post)
    out = ai_provider.call_provider(_entry(wire_api="responses"), "m", [{"role": "user", "content": "hi"}], 30)
    assert out == "ok"
    assert seen["url"] == "https://api.example.com/v1/responses"
    assert "input" in seen["payload"] and "messages" not in seen["payload"]


def test_call_provider_http_error_includes_body(monkeypatch):
    """非 200 抛出带状态码和正文摘要的错误。"""
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResp(429, {}, text="rate limited"))
    with pytest.raises(RuntimeError, match="HTTP 429"):
        ai_provider.call_provider(_entry(), "m", [{"role": "user", "content": "hi"}], 30)


def test_call_provider_unsupported_wire_api():
    """未知 wire_api 直接报错。"""
    with pytest.raises(RuntimeError, match="wire_api"):
        ai_provider.call_provider(_entry(wire_api="graphql"), "m", [{"role": "user", "content": "hi"}], 30)


def test_call_provider_missing_base_url():
    """缺 base_url 报错。"""
    with pytest.raises(RuntimeError, match="base_url"):
        ai_provider.call_provider(_entry(base_url=""), "m", [{"role": "user", "content": "hi"}], 30)


# ---------------------------------------------------------------------------
# call_ai fallback 链
# ---------------------------------------------------------------------------

def test_call_ai_primary_success(monkeypatch):
    """主 provider 成功直接返回，不触碰 fallback。"""
    calls = []
    monkeypatch.setattr(ai_provider, "call_provider", lambda entry, model, messages, t: calls.append(model) or "done")
    cfg = _cfg(ai_primary_provider="p1", ai_fallback_providers=["p2"],
               providers={"p1": _entry(), "p2": _entry()})
    assert ai_provider.call_ai(cfg, [{"role": "user", "content": "hi"}]) == "done"
    assert calls == ["text-m"]


def test_call_ai_fallback_on_failure(monkeypatch):
    """主失败时回落到 fallback provider。"""
    def fake_call(entry, model, messages, t):
        if entry["text_model"] == "bad":
            raise RuntimeError("boom")
        return "fallback-ok"

    monkeypatch.setattr(ai_provider, "call_provider", fake_call)
    cfg = _cfg(ai_primary_provider="p1", ai_fallback_providers=["p2"],
               providers={"p1": _entry(text_model="bad"), "p2": _entry(text_model="good")})
    assert ai_provider.call_ai(cfg, [{"role": "user", "content": "hi"}]) == "fallback-ok"


def test_call_ai_provider_not_configured():
    """主 provider 未在 providers 里配置时直接进入错误列表。"""
    cfg = _cfg(ai_primary_provider="ghost")
    with pytest.raises(RuntimeError, match="not configured"):
        ai_provider.call_ai(cfg, [{"role": "user", "content": "hi"}])


def test_call_ai_no_api_key_skipped(monkeypatch):
    """没配 key 的 provider 被跳过。"""
    monkeypatch.setattr(ai_provider, "call_provider", lambda *a: "ok")
    cfg = _cfg(ai_primary_provider="p1", providers={"p1": _entry(api_key="")})
    with pytest.raises(RuntimeError, match="no API key"):
        ai_provider.call_ai(cfg, [{"role": "user", "content": "hi"}])


def test_call_ai_vision_uses_vision_model(monkeypatch):
    """is_vision=True 时选用 vision_model。"""
    seen = []
    monkeypatch.setattr(ai_provider, "call_provider", lambda entry, model, messages, t: seen.append(model) or "v")
    cfg = _cfg(ai_primary_provider="p1", providers={"p1": _entry()})
    ai_provider.call_ai(cfg, [{"role": "user", "content": "hi"}], is_vision=True)
    assert seen == ["vision-m"]


def test_call_ai_vision_without_vision_model_fails():
    """需要视觉但没配 vision_model 时报清晰错误。"""
    cfg = _cfg(ai_primary_provider="p1", providers={"p1": _entry(vision_model="")})
    with pytest.raises(RuntimeError, match="no vision model"):
        ai_provider.call_ai(cfg, [{"role": "user", "content": "hi"}], is_vision=True)


def test_call_ai_all_failed_aggregates_errors(monkeypatch):
    """全部失败时错误信息聚合各 provider 原因。"""
    monkeypatch.setattr(ai_provider, "call_provider", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    cfg = _cfg(ai_primary_provider="p1", ai_fallback_providers=["p2"],
               providers={"p1": _entry(), "p2": _entry()})
    with pytest.raises(RuntimeError, match="All AI providers failed"):
        ai_provider.call_ai(cfg, [{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# test_provider
# ---------------------------------------------------------------------------

def test_test_provider_success(monkeypatch):
    """探测成功返回 True 和响应摘要。"""
    monkeypatch.setattr(ai_provider, "call_provider", lambda *a: "OK fine")
    cfg = _cfg(providers={"p": _entry()})
    ok, msg = ai_provider.test_provider(cfg, "p", "text-m")
    assert ok and msg.startswith("OK")


def test_test_provider_not_configured():
    """未配置 provider 探测返回 False。"""
    ok, msg = ai_provider.test_provider(_cfg(), "ghost", "m")
    assert not ok and "not configured" in msg


def test_test_provider_no_base_url():
    """缺 base_url 探测返回 False。"""
    cfg = _cfg(providers={"p": _entry(base_url="")})
    ok, msg = ai_provider.test_provider(cfg, "p", "text-m")
    assert not ok and "base_url" in msg
