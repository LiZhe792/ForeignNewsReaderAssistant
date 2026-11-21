"""DeepSeek API transport and local configuration; secrets never reach the browser."""

import json
import http.client
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from threading import Lock

from flask import current_app

MODELS = ("deepseek-flash", "deepseek-v4-pro")
API_ROOT = "https://api.deepseek.com"
CONFIG_LOCK = Lock()


class AIError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def _path():
    return Path(current_app.config.get("AI_SETTINGS_FILE") or
                Path(current_app.config["DATABASE"]).with_name("deepseek.json"))


def _stored():
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def credentials():
    stored = _stored()
    env_key = current_app.config.get("DEEPSEEK_API_KEY", os.environ.get("DEEPSEEK_API_KEY", ""))
    key = env_key or stored.get("api_key", "")
    model = stored.get("model", MODELS[0])
    if model not in MODELS:
        model = MODELS[0]
    if not isinstance(key, str) or (key and not re.fullmatch(r"[A-Za-z0-9_.-]{8,512}", key)):
        raise AIError("DeepSeek 密钥格式无效，请在本机重新配置。", 400)
    return key, model, "environment" if env_key else "local" if key else "none"


def public_config():
    key, model, source = credentials()
    return {"configured": bool(key), "model": model, "models": list(MODELS), "key_source": source}


def save_config(data):
    if set(data) - {"api_key", "model", "clear_key"}:
        raise ValueError("不支持的 AI 配置。")
    with CONFIG_LOCK:
        _, current_model, source = credentials()
        stored = _stored()
        key = data.get("api_key", "")
        if not isinstance(key, str) or (key and not re.fullmatch(r"[A-Za-z0-9_.-]{8,512}", key)):
            raise ValueError("请填写有效的 DeepSeek API 密钥。")
        clear = data.get("clear_key", False)
        if type(clear) is not bool or (clear and key):
            raise ValueError("请选择保存新密钥或移除密钥。")
        if source == "environment" and (key or clear):
            raise ValueError("当前使用 DEEPSEEK_API_KEY 环境变量，请在 PyCharm 运行配置中修改或移除。")
        model = data.get("model", current_model)
        if model not in MODELS:
            raise ValueError("请选择支持的 DeepSeek 模型。")
        result = {"model": model, "api_key": "" if clear else key or stored.get("api_key", "")}
        target = _path()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                             prefix=".deepseek-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(result, stream, ensure_ascii=False)
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
    return public_config()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(path, key, payload=None):
    """Only contact the fixed official API, once; never log credentials/provider bodies."""
    encoded = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(API_ROOT + path, data=encoded, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept": "application/json",
    })
    deadline = time.monotonic() + 120
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=60) as response:
            chunks, total = [], 0
            while True:
                if time.monotonic() > deadline:
                    raise AIError("DeepSeek 响应超时，请稍后手动重试。", 504)
                chunk = response.read1(min(65536, 1_000_001 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > 1_000_000:
                    raise AIError("AI 返回内容过大，请缩小问题范围后重试。")
        result = json.loads(b"".join(chunks))
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except urllib.error.HTTPError as exc:
        exc.close()
        messages = {401: "DeepSeek 密钥无效，请检查本机 AI 配置。", 402: "DeepSeek 账户余额不足，请前往官方控制台查看。",
                    403: "当前 DeepSeek 账户无权使用该接口或模型。", 429: "DeepSeek 请求过于频繁，请稍后再试。"}
        raise AIError(messages.get(exc.code, f"DeepSeek 服务暂不可用（HTTP {exc.code}），请稍后重试。"),
                      exc.code if exc.code in (401, 402, 403, 429) else 502) from None
    except (TimeoutError, urllib.error.URLError, OSError, http.client.HTTPException):
        raise AIError("无法连接 DeepSeek，请检查网络与代理设置后重试。", 503) from None
    except (ValueError, UnicodeError):
        raise AIError("DeepSeek 返回格式异常，请稍后重试。") from None


def check_connection():
    key, model, _ = credentials()
    if not key:
        raise AIError("请先在本机配置 DeepSeek API 密钥。", 400)
    result = request_json("/models", key)
    entries = result.get("data", [])
    if not isinstance(entries, list):
        raise AIError("DeepSeek 模型列表格式异常，请稍后重试。")
    available = {entry["id"] for entry in entries if isinstance(entry, dict) and isinstance(entry.get("id"), str)}
    if model not in available:
        raise AIError("密钥已连接，但当前模型不可用，请选择其他模型。", 400)
    return {"ok": True, "model": model}


def generate(messages, config):
    key, model, _ = config
    result = request_json("/chat/completions", key, {
        "model": model, "messages": messages, "stream": False,
        "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"},
        "temperature": 0.4, "max_tokens": 3072,
    })
    try:
        choice = result["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise AIError("AI 回答未完整返回，请缩小问题范围后手动重试。")
        content = choice["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError()
        answer = json.loads(content)
        if not isinstance(answer, dict):
            raise ValueError()
        return answer
    except (KeyError, IndexError, TypeError, ValueError):
        raise AIError("AI 未返回有效回答，请稍后手动重试。") from None
