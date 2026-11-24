"""Bounded Edge speech synthesis and an atomic, size-limited local MP3 cache."""
import asyncio
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from threading import BoundedSemaphore, Lock, RLock

from flask import current_app

VOICES = (
    {"id": "en-US-AriaNeural", "name": "Aria · 女声", "accent": "en-US"},
    {"id": "en-US-GuyNeural", "name": "Guy · 男声", "accent": "en-US"},
    {"id": "en-GB-SoniaNeural", "name": "Sonia · 女声", "accent": "en-GB"},
    {"id": "en-GB-RyanNeural", "name": "Ryan · 男声", "accent": "en-GB"},
)
MAX_AUDIO_BYTES = 8 * 1024 * 1024
FILE_LOCK = RLock()
KEY_LOCKS = [Lock() for _ in range(32)]
PROVIDER_SLOTS = BoundedSemaphore(2)
CACHE_EPOCH = 0


class SpeechError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


def cache_root():
    return Path(current_app.config.get("TTS_CACHE_DIR") or
                Path(current_app.config["DATABASE"]).parent / "tts_cache")


def cache_limit():
    return current_app.config.get("TTS_CACHE_MAX_BYTES", 128 * 1024 * 1024)


def cache_key(text, voice):
    source = json.dumps(["edge-mp3-v1", voice, text.strip()], ensure_ascii=False)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def audio_path(key):
    if not re.fullmatch(r"[a-f0-9]{64}", key):
        raise ValueError("音频标识无效。")
    return cache_root() / (key + ".mp3")


def cache_files():
    return [p for p in cache_root().glob("*.mp3")
            if re.fullmatch(r"[a-f0-9]{64}\.mp3", p.name) and p.is_file() and not p.is_symlink()]


def cache_stats():
    with FILE_LOCK:
        files = cache_files()
        return {"files": len(files), "bytes": sum(p.stat().st_size for p in files), "limit_bytes": cache_limit()}


def clear_cache():
    global CACHE_EPOCH
    with FILE_LOCK:
        CACHE_EPOCH += 1
        for path in cache_files():
            try:
                path.unlink()
            except PermissionError:
                pass  # A browser may still be reading this file on Windows.
        return cache_stats()


def synthesize(text, voice):
    try:
        import edge_tts
    except ImportError:
        raise SpeechError("缺少朗读依赖，请在 PyCharm 终端运行 python -m pip install -r requirements.txt。") from None

    async def collect():
        speech = edge_tts.Communicate(text, voice, connect_timeout=10, receive_timeout=15)
        chunks, size = [], 0
        async for item in speech.stream():
            if item["type"] == "audio":
                size += len(item["data"])
                if size > MAX_AUDIO_BYTES:
                    raise SpeechError("这一段音频过长，请选择较短的句子。", 413)
                chunks.append(item["data"])
        return b"".join(chunks)

    async def bounded():
        return await asyncio.wait_for(collect(), timeout=45)

    try:
        data = asyncio.run(bounded())
    except SpeechError:
        raise
    except Exception:
        raise SpeechError("暂时无法生成语音，请检查网络后重试；已缓存的音频仍可播放。") from None
    if len(data) < 128 or not (data.startswith(b"ID3") or (data[0] == 255 and data[1] & 224 == 224)):
        raise SpeechError("语音服务没有返回有效音频，请稍后重试。")
    return data


def get_audio(text, voice):
    key = cache_key(text, voice)
    path = audio_path(key)
    with KEY_LOCKS[int(key[:2], 16) % len(KEY_LOCKS)]:
        with FILE_LOCK:
            if path.is_file() and not path.is_symlink() and path.stat().st_size >= 128:
                path.touch()
                return {"key": key, "cached": True, "bytes": path.stat().st_size}
            epoch = CACHE_EPOCH
        from .data_management import offline
        if offline():
            raise SpeechError("离线模式下只能播放已缓存的音频。", 409)
        if not PROVIDER_SLOTS.acquire(timeout=5):
            raise SpeechError("正在准备其他音频，请稍后重试。", 429)
        try:
            data = synthesize(text.strip(), voice)
        finally:
            PROVIDER_SLOTS.release()
        if not data or len(data) > min(MAX_AUDIO_BYTES, cache_limit()):
            raise SpeechError("音频大小超出缓存限制，请选择较短的内容。", 413)
        with FILE_LOCK:
            if epoch != CACHE_EPOCH:
                raise SpeechError("缓存已清理，本次生成已取消，请重新播放。", 409)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(data)
                os.replace(temporary, path)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()
            files = sorted(cache_files(), key=lambda p: p.stat().st_mtime_ns)
            total = sum(p.stat().st_size for p in files)
            for old in files:
                if total <= cache_limit():
                    break
                if old != path:
                    size = old.stat().st_size
                    try:
                        old.unlink()
                        total -= size
                    except PermissionError:
                        pass
            if total > cache_limit():
                path.unlink()
                raise SpeechError("缓存正被占用，请停止其他朗读后重试。", 409)
        return {"key": key, "cached": False, "bytes": len(data)}
