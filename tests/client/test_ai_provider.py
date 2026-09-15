"""tests for client/src/ai_provider.py — provider 选择、fallback 链、超时处理。

各 provider 的 SDK 调用层通过替换 PROVIDER_CALLS 表来 mock，不触网。
"""

import time
from types import SimpleNamespace

import pytest

import ai_provider


def _cfg(primary="", fallbacks=None, providers=None):
    return {
        "ai_primary_provider": primary,
        "ai_fallback_providers": fallbacks or [],
        "providers": providers or {},
    }


def _openai_style_response(text):
    """OpenAI 兼容格式的假响应（zhipu/minimax 解析路径）。"""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


# ---------------------------------------------------------------------------
# 配置解析
# ---------------------------------------------------------------------------

def test_get_fallback_providers_list():
    """fallback 列表原样小写返回，空项被过滤。"""
    cfg = {"ai_fallback_providers": ["Zhipu", "", " MiniMax "]}
    assert ai_provider.get_fallback_providers(cfg) == ["zhipu", "minimax"]


def test_get_fallback_providers_legacy_single():
    """兼容路径：配置里只有旧版单数键 ai_fallback_provider 时也能读出（回归：曾被静默丢弃）。"""
    cfg = {"ai_fallback_provider": "Zhipu"}
    assert ai_provider.get_fallback_providers(cfg) == ["zhipu"]


def test_get_fallback_providers_string_value():
    """新键误写成字符串时按单个 provider 名处理，而不是静默忽略。"""
    cfg = {"ai_fallback_providers": "Zhipu"}
    assert ai_provider.get_fallback_providers(cfg) == ["zhipu"]


def test_get_fallback_providers_empty():
    """无 fallback 配置时返回空列表。"""
    assert ai_provider.get_fallback_providers({}) == []


def test_provider_config_query_case_insensitive():
    """查询侧 provider 名大小写不敏感（config 里的键需为小写）。"""
    cfg = {"providers": {"dashscope": {"api_key": " k ", "text_model": "qwen"}}}
    assert ai_provider.get_provider_api_key(cfg, "DASHSCOPE") == "k"
    assert ai_provider.get_provider_text_model(cfg, "DashScope") == "qwen"


# ---------------------------------------------------------------------------
# call_ai 选择 / fallback 链
# ---------------------------------------------------------------------------

def test_call_ai_no_provider_configured():
    """什么都没配置：抛 RuntimeError，错误信息为空列表拼接。"""
    with pytest.raises(RuntimeError, match="All AI providers failed"):
        ai_provider.call_ai({}, [{"role": "user", "content": "hi"}])


def test_call_ai_no_api_key_skipped(monkeypatch):
    """provider 配了但没有 api_key：跳过并记录 'no API key'，不发起调用。"""
    called = []
    monkeypatch.setitem(ai_provider.PROVIDER_CALLS, "dashscope", lambda *a: called.append(1))
    cfg = _cfg(primary="dashscope", providers={"dashscope": {"api_key": "", "text_model": "qwen"}})
    with pytest.raises(RuntimeError, match="no API key"):
        ai_provider.call_ai(cfg, [{"role": "user", "content": "hi"}])
    assert called == []


def test_call_ai_unsupported_provider():
    """配置了 PROVIDER_CALLS 之外的 provider：报 'not supported'。"""
    cfg = _cfg(primary="magicai", providers={"magicai": {"api_key": "k", "text_model": "m"}})
    with pytest.raises(RuntimeError, match="not supported"):
        ai_provider.call_ai(cfg, [{"role": "user", "content": "hi"}])


def test_call_ai_no_model_configured():
    """有 key 但没配对应类型模型：报 'no text model configured'。"""
    cfg = _cfg(primary="zhipu", providers={"zhipu": {"api_key": "k"}})
    with pytest.raises(RuntimeError, match="no text model configured"):
        ai_provider.call_ai(cfg, [{"role": "user", "content": "hi"}])


def test_call_ai_primary_success(monkeypatch, capsys):
    """主 provider 直接成功：返回解析后的文本，打印 AI_CALL 日志。"""
    monkeypatch.setitem(
        ai_provider.PROVIDER_CALLS, "zhipu",
        lambda key, model, msgs, is_vision: _openai_style_response(" 你好 "),
    )
    cfg = _cfg(primary="zhipu", providers={"zhipu": {"api_key": "k", "text_model": "glm-4"}})
    result = ai_provider.call_ai(cfg, [{"role": "user", "content": "hi"}])
    assert result == "你好"
    assert "AI_CALL|provider=zhipu|model=glm-4" in capsys.readouterr().out


def test_call_ai_falls_back_when_primary_fails(monkeypatch, capsys):
    """主 provider 抛异常后自动走 fallback，最终返回 fallback 的结果。"""
    calls = []

    def failing(key, model, msgs, is_vision):
        calls.append("primary")
        raise ConnectionError("primary down")

    def working(key, model, msgs, is_vision):
        calls.append("fallback")
        return _openai_style_response("from-fallback")

    monkeypatch.setitem(ai_provider.PROVIDER_CALLS, "zhipu", failing)
    monkeypatch.setitem(ai_provider.PROVIDER_CALLS, "minimax", working)
    cfg = _cfg(
        primary="zhipu",
        fallbacks=["minimax"],
        providers={
            "zhipu": {"api_key": "k1", "text_model": "glm-4"},
            "minimax": {"api_key": "k2", "text_model": "abab6"},
        },
    )
    assert ai_provider.call_ai(cfg, [{"role": "user", "content": "hi"}]) == "from-fallback"
    assert calls == ["primary", "fallback"]
    assert "AI_CALL_FAILED|provider=zhipu" in capsys.readouterr().out


def test_call_ai_all_providers_fail_aggregates_errors(monkeypatch):
    """所有 provider 都失败：RuntimeError 里聚合每个 provider 的失败原因。"""
    monkeypatch.setitem(ai_provider.PROVIDER_CALLS, "zhipu", lambda *a: 1 / 0)
    monkeypatch.setitem(ai_provider.PROVIDER_CALLS, "minimax", lambda *a: (_ for _ in ()).throw(ValueError("bad")))
    cfg = _cfg(
        primary="zhipu",
        fallbacks=["minimax"],
        providers={
            "zhipu": {"api_key": "k1", "text_model": "glm-4"},
            "minimax": {"api_key": "k2", "text_model": "abab6"},
        },
    )
    with pytest.raises(RuntimeError) as ei:
        ai_provider.call_ai(cfg, [{"role": "user", "content": "hi"}])
    msg = str(ei.value)
    assert "Provider zhipu failed" in msg
    assert "Provider minimax failed" in msg


def test_call_ai_fallback_same_as_primary_not_duplicated(monkeypatch):
    """fallback 与 primary 相同时只尝试一次。"""
    calls = []
    monkeypatch.setitem(
        ai_provider.PROVIDER_CALLS, "zhipu",
        lambda *a: calls.append(1) or _openai_style_response("ok"),
    )
    cfg = _cfg(
        primary="zhipu",
        fallbacks=["zhipu"],
        providers={"zhipu": {"api_key": "k", "text_model": "glm-4"}},
    )
    assert ai_provider.call_ai(cfg, [{"role": "user", "content": "hi"}]) == "ok"
    assert len(calls) == 1


def test_call_ai_timeout_raises_runtime_error(monkeypatch):
    """调用超过 timeout_seconds：包装为 RuntimeError('Request timed out...')，并继续走 fallback/失败。"""
    def slow(key, model, msgs, is_vision):
        time.sleep(1.0)
        return _openai_style_response("late")

    monkeypatch.setitem(ai_provider.PROVIDER_CALLS, "zhipu", slow)
    cfg = _cfg(primary="zhipu", providers={"zhipu": {"api_key": "k", "text_model": "glm-4"}})
    with pytest.raises(RuntimeError, match="timed out"):
        ai_provider.call_ai(cfg, [{"role": "user", "content": "hi"}], timeout_seconds=0.1)


def test_call_ai_api_error_status_raises(monkeypatch):
    """SDK 返回 status_code != 200 的响应：按 API error 处理并走失败路径。"""
    bad = SimpleNamespace(status_code=400, code="InvalidApiKey", message="denied")
    monkeypatch.setitem(ai_provider.PROVIDER_CALLS, "dashscope", lambda *a: bad)
    cfg = _cfg(primary="dashscope", providers={"dashscope": {"api_key": "k", "text_model": "qwen"}})
    with pytest.raises(RuntimeError, match="InvalidApiKey"):
        ai_provider.call_ai(cfg, [{"role": "user", "content": "hi"}])


def test_call_ai_vision_uses_vision_model(monkeypatch):
    """is_vision=True 时选用 vision_model 而不是 text_model。"""
    seen = {}

    def spy(key, model, msgs, is_vision):
        seen["model"] = model
        seen["is_vision"] = is_vision
        return _openai_style_response("ok")

    monkeypatch.setitem(ai_provider.PROVIDER_CALLS, "zhipu", spy)
    cfg = _cfg(
        primary="zhipu",
        providers={"zhipu": {"api_key": "k", "text_model": "glm-4", "vision_model": "glm-4v"}},
    )
    ai_provider.call_ai(cfg, [{"role": "user", "content": []}], is_vision=True)
    assert seen == {"model": "glm-4v", "is_vision": True}


# ---------------------------------------------------------------------------
# parse_response 多格式解析
# ---------------------------------------------------------------------------

def test_parse_response_openai_style():
    """OpenAI 兼容格式（choices[0].message.content）取文本。"""
    assert ai_provider.parse_response(_openai_style_response("hi")) == "hi"


def test_parse_response_dashscope_text():
    """DashScope 文本格式：output.choices[0].message.content 为 dict 列表时拼接 text。"""
    resp = SimpleNamespace(
        output=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=[{"text": "a"}, {"text": "b"}]))]
        )
    )
    assert ai_provider.parse_response(resp, is_vision=False) == "a\nb"


def test_parse_response_dashscope_vision():
    """DashScope 视觉格式：content 列表逐项取 text 拼接。"""
    resp = SimpleNamespace(
        output=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=[{"text": "看见"}, {"text": "猫"}]))]
        )
    )
    assert ai_provider.parse_response(resp, is_vision=True) == "看见\n猫"


# ---------------------------------------------------------------------------
# test_provider 可用性探测
# ---------------------------------------------------------------------------

def test_test_provider_no_api_key():
    """test_provider 缺 key 时直接返回 (False, 'No API key')。"""
    ok, msg = ai_provider.test_provider({"providers": {}}, "zhipu", "glm-4")
    assert ok is False
    assert msg == "No API key"


def test_test_provider_unsupported():
    """test_provider 对未知 provider 返回 not supported。"""
    cfg = {"providers": {"magicai": {"api_key": "k"}}}
    ok, msg = ai_provider.test_provider(cfg, "magicai", "m")
    assert ok is False
    assert "not supported" in msg


def test_test_provider_success(monkeypatch):
    """test_provider 成功路径：返回 (True, 'OK - ...')。"""
    monkeypatch.setitem(ai_provider.PROVIDER_CALLS, "zhipu", lambda *a: _openai_style_response("OK"))
    cfg = {"providers": {"zhipu": {"api_key": "k", "text_model": "glm-4"}}}
    ok, msg = ai_provider.test_provider(cfg, "zhipu", "glm-4", is_vision=False)
    assert ok is True
    assert msg.startswith("OK")
