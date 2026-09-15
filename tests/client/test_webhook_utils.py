"""tests for client/src/webhook_utils.py — 飞书签名、URL 校验、JSON 解析容错、发送重试。"""

import base64
import hashlib
import hmac

import pytest
import requests

import webhook_utils
from conftest import FakeResponse

FEISHU_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/abcd1234efgh5678"


def test_feishu_signature_known_vector():
    """签名算法固定向量：HMAC-SHA256(key=f'{ts}\\n{secret}', msg=b'') 的 base64。"""
    assert webhook_utils.build_feishu_signature("my-secret", 1700000000) == (
        "I3DorsRQAITp7wWwxm5u7O9Ca8T+zLwkNSN7C16z0yQ="
    )
    # 与独立计算的参考实现一致
    expect = base64.b64encode(
        hmac.new(b"1600000000\nanother_secret", b"", digestmod=hashlib.sha256).digest()
    ).decode()
    assert webhook_utils.build_feishu_signature("another_secret", 1600000000) == expect


def test_is_feishu_webhook():
    """只有 open.feishu.cn 且路径含 /open-apis/bot/ 的 URL 才识别为飞书。"""
    assert webhook_utils.is_feishu_webhook(FEISHU_URL) is True
    assert webhook_utils.is_feishu_webhook("https://open.feishu.cn/open-apis/bot/v2/hook/x") is True
    assert webhook_utils.is_feishu_webhook("https://example.com/open-apis/bot/v2/hook/x") is False
    assert webhook_utils.is_feishu_webhook("https://open.feishu.cn/other/path") is False
    assert webhook_utils.is_feishu_webhook("https://notfeishu.cn.evil.com/open-apis/bot/x") is False


def test_mask_webhook_url_long_token():
    """长 token 只保留前 4 后 4，中间打码。"""
    masked = webhook_utils.mask_webhook_url(FEISHU_URL)
    assert masked == "https://open.feishu.cn/open-apis/bot/v2/hook/abcd...5678"


def test_mask_webhook_url_short_token():
    """短 token（≤8 字符）整体替换为 ***。"""
    masked = webhook_utils.mask_webhook_url("https://example.com/hook/short")
    assert masked == "https://example.com/hook/***"


def test_parse_json_response_plain():
    """纯 JSON 文本直接解析。"""
    assert webhook_utils.parse_json_response('{"a": 1}') == {"a": 1}


def test_parse_json_response_code_block():
    """从 ```json 代码块中提取 JSON。"""
    text = '这是说明\n```json\n{"mood": "good"}\n```\n结尾'
    assert webhook_utils.parse_json_response(text) == {"mood": "good"}


def test_parse_json_response_embedded_in_prose():
    """JSON 前后夹杂散文时，截取首尾花括号之间的内容解析。"""
    text = 'AI 说： {"score": 8, "note": "x"} 以上。'
    assert webhook_utils.parse_json_response(text) == {"score": 8, "note": "x"}


def test_parse_json_response_empty_raises():
    """空响应抛出 RuntimeError。"""
    with pytest.raises(RuntimeError, match="empty"):
        webhook_utils.parse_json_response("   ")


def test_parse_json_response_invalid_raises():
    """完全无法解析的文本抛出 RuntimeError。"""
    with pytest.raises(RuntimeError, match="valid JSON"):
        webhook_utils.parse_json_response("sorry, I cannot help")


def test_parse_json_response_non_dict_raises():
    """合法 JSON 但不是对象（数组）时也抛出 RuntimeError。"""
    with pytest.raises(RuntimeError):
        webhook_utils.parse_json_response("[1, 2, 3]")


def test_send_webhook_success_posts_payload(monkeypatch):
    """非飞书 URL 成功发送：POST 一次，payload 原样透传，带 UA/Content-Type 头。"""
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append({"url": url, "json": json, "timeout": timeout, "headers": headers})
        return FakeResponse(200)

    monkeypatch.setattr(requests, "post", fake_post)
    webhook_utils.send_webhook("https://example.com/hook/x", {"msg_type": "text"}, 5, retry_delay_seconds=0)
    assert len(calls) == 1
    assert calls[0]["json"] == {"msg_type": "text"}
    assert calls[0]["headers"]["Content-Type"] == "application/json"


def test_send_webhook_feishu_adds_signature(monkeypatch):
    """飞书 URL 且配置了 secret：payload 附加 timestamp 和正确计算的 sign。"""
    captured = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        captured.update(json)
        return FakeResponse(200, {"code": 0})

    monkeypatch.setattr(requests, "post", fake_post)
    webhook_utils.send_webhook(FEISHU_URL, {"msg_type": "text"}, 5, secret="s3cret", retry_delay_seconds=0)
    ts = int(captured["timestamp"])
    assert captured["sign"] == webhook_utils.build_feishu_signature("s3cret", ts)


def test_send_webhook_feishu_code_nonzero_raises(monkeypatch):
    """飞书返回 code != 0 视为拒绝，重试耗尽后抛 RuntimeError 且包含 code。"""
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(200, {"code": 19021, "msg": "sign match fail"}))
    with pytest.raises(RuntimeError, match="19021"):
        webhook_utils.send_webhook(FEISHU_URL, {}, 5, retry_count=0)


def test_send_webhook_http_error_raises(monkeypatch):
    """HTTP >= 400 直接判失败，错误信息包含状态码。"""
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(503))
    with pytest.raises(RuntimeError, match="503"):
        webhook_utils.send_webhook("https://example.com/hook/x", {}, 5, retry_count=0)


def test_send_webhook_retries_then_succeeds(monkeypatch):
    """第一次网络异常、第二次成功：retry_count=1 时总共 POST 两次并最终成功。"""
    attempts = []

    def flaky(url, json=None, timeout=None, headers=None):
        attempts.append(1)
        if len(attempts) == 1:
            raise requests.ConnectionError("boom")
        return FakeResponse(200)

    monkeypatch.setattr(requests, "post", flaky)
    webhook_utils.send_webhook("https://example.com/hook/x", {}, 5, retry_count=1, retry_delay_seconds=0)
    assert len(attempts) == 2


def test_send_webhook_exhausts_retries(monkeypatch):
    """持续失败时共尝试 retry_count+1 次，然后抛出包含次数信息的 RuntimeError。"""
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(500))
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        webhook_utils.send_webhook("https://example.com/hook/x", {}, 5, retry_count=2, retry_delay_seconds=0)


def test_send_webhook_negative_params_rejected():
    """retry_count 或 retry_delay_seconds 为负数时立即抛错，不发请求。"""
    with pytest.raises(RuntimeError, match="retry_count"):
        webhook_utils.send_webhook("https://example.com/x", {}, 5, retry_count=-1)
    with pytest.raises(RuntimeError, match="retry_delay_seconds"):
        webhook_utils.send_webhook("https://example.com/x", {}, 5, retry_delay_seconds=-0.1)
