#!/usr/bin/env python3
"""
Magic Route Proxy — HTTP reverse proxy, reads routing decision per-request.
Listens on 127.0.0.1:15666. Only activates for models listed in
activation.model_patterns. Supports base/upgrade with primary/backup.

Designed to transparently proxy streaming (SSE) responses, so clients see
tokens as they arrive.
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import re
from aiohttp import web, ClientSession, ClientTimeout

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "magic_proxy_config.json")
_TD = tempfile.gettempdir()
TARGET_FILE = os.path.join(_TD, "magic_target.json")

_CLIENT = None


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[MagicProxy] config load failed: {e}", flush=True)
        return {}


def read_target():
    tgt = "base"
    try:
        cfg = load_config()
        tgt = cfg.get("routing", {}).get("default_target", "base")
        if os.path.exists(TARGET_FILE):
            with open(TARGET_FILE, encoding="utf-8") as f:
                d = json.load(f)
                val = d.get("target", "")
                if val in ("base", "upgrade"):
                    tgt = val
    except Exception:
        pass
    return tgt


def read_target_level():
    """读 upgrade 层级（1=主云模型，2=备用云模型）；缺失或异常默认 1。"""
    try:
        if os.path.exists(TARGET_FILE):
            with open(TARGET_FILE, encoding="utf-8") as f:
                d = json.load(f)
            lvl = d.get("level", 1)
            if lvl in (1, 2):
                return lvl
    except Exception:
        pass
    return 1


def get_provider(target, level=1):
    cfg = load_config()
    section = cfg.get("base" if target == "base" else "upgrade", {})
    p = section.get("primary") or {}
    b = section.get("backup")
    if target == "upgrade" and level >= 2:
        # level 2 → 互换主备：用 backup 作为 primary，primary 作为备用
        if b:
            return b, p
        # 没有 backup 时退回到 primary
        return p, None
    return p, b


def build_url(provider, request_path):
    base = provider.get("base_url", "").strip()
    if base.endswith("/"):
        base = base[:-1]
    if request_path.startswith("/v1"):
        request_path = request_path[3:]
    return base + request_path


def is_activated(cfg, model_name):
    """Check if model_name is in activation allowlist."""
    act = cfg.get("activation", {})
    patterns = act.get("model_patterns", [])
    mode = act.get("match_mode", "exact")
    if not patterns:
        return True
    if mode == "exact":
        return model_name in patterns
    elif mode == "prefix":
        return any(model_name.startswith(p) for p in patterns)
    elif mode == "regex":
        return any(re.search(p, model_name) for p in patterns)
    return False


# ── Request helpers ────────────────────────────────

def extract_model(body_bytes):
    try:
        if body_bytes:
            d = json.loads(body_bytes)
            if isinstance(d, dict):
                return d.get("model", "")
    except Exception:
        pass
    return ""


def rewrite_model(body_bytes, provider):
    """Rewrite the request body's 'model' to this provider's default_model.

    Required: base uses the local model ID, upgrade uses the cloud model ID.
    Without rewriting, sending the local model ID to the cloud endpoint is
    rejected ("not a valid model ID"). If the provider has no default_model,
    the body is returned unchanged.
    """
    default_model = provider.get("default_model", "")
    if not default_model or not body_bytes:
        return body_bytes
    try:
        d = json.loads(body_bytes)
        if isinstance(d, dict) and d.get("model", "") != default_model:
            d["model"] = default_model
            return json.dumps(d, ensure_ascii=False).encode("utf-8")
    except Exception:
        pass
    return body_bytes


def _stream_headers(headers):
    """Ensure headers look SSE/streamable to the downstream client."""
    headers.setdefault("Content-Type", "text/event-stream")
    headers["Cache-Control"] = "no-cache, no-transform"
    headers["Connection"] = "keep-alive"
    return headers


async def call_upstream(method, url, headers, body_bytes):
    """Open one upstream and return the live response object.

    aiohttp is the client here, so its default 5s read timeout would drop
    slow streaming responses before the body finishes. Use a large timeout so
    streamed tokens can arrive. total=None lets the request run as long as the
    upstream needs; the downstream client still bounds the whole exchange.
    We return the live response (not .read()) so callers can stream SSE
    output through byte-by-byte.
    """
    resp = await _CLIENT.request(
        method, url, headers=headers, data=body_bytes,
        timeout=ClientTimeout(total=None))
    return resp


async def _upstream_request(provider, method, url, body_bytes):
    """Open a live upstream for a given provider spec."""
    ak = provider.get("api_key", "")
    uh = {}
    if ak:
        uh["x-api-key"] = ak
        uh["Authorization"] = f"Bearer {ak}"
    # Some backends require Content-Type: application/json
    uh.setdefault("Content-Type", "application/json")
    # 按该 provider 的 default_model 改写请求里的 model（必需）
    body_bytes = rewrite_model(body_bytes, provider)
    return await call_upstream(method, url, uh, body_bytes)


# ── Request handler ───────────────────────────────

async def handle_request(request):
    cfg = load_config()
    body_bytes = await request.read()

    model_name = extract_model(body_bytes)
    activated = is_activated(cfg, model_name)

    enabled = cfg.get("_meta", {}).get("enabled", True)
    path = request.path

    if path == "/health":
        return web.json_response({"status": "ok", "proxy": "magic-proxy"})

    if not enabled or not activated:
        provider = cfg.get("base", {}).get("primary", {})
        out = await _stream_response(request, {})
        return await _serve(out, request, provider, body_bytes)

    # Activated: read routing decision and pick provider(s).
    target = read_target()
    provider, backup = get_provider(target)
    if not provider:
        provider = cfg.get("base", {}).get("primary", {})

    try:
        upstream = await _upstream_request(provider, request.method,
                                           build_url(provider, path), body_bytes)
        # Read only the first chunk to inspect status/body and detect errors.
        # A full response ends quickly; an SSE stream yields a small JSON
        # header chunk first. We must not consume the whole body here or the
        # subsequent streaming pass would have nothing left to send.
        status, resp_headers, first_chunk = await _drain_first(upstream)
        print(f"[MagicProxy] upstream url={build_url(provider, path)} status={status} first_len={len(first_chunk)}", flush=True)

        # Auto-escalate on context length exceeded (base -> upgrade).
        if (status == 400 or status == 413) and target == "base" and _is_ctx_error(first_chunk):
            print("[MagicProxy] context exceeded, auto-escalating", flush=True)
            try:
                with open(TARGET_FILE, "w", encoding="utf-8") as f:
                    json.dump({"target": "upgrade", "updated_at": time.time(),
                               "reason": "context_overflow"}, f)
            except Exception:
                pass
            upgrade, upgrade_bak = get_provider("upgrade")
            out = await _stream_response(request, resp_headers)
            return await _serve(out, request, upgrade, body_bytes,
                                backup=upgrade_bak, first=first_chunk)

        # 升级目标返回错误（云端不通/被拒/限流等）：先试备用云，没备用才退本地。
        if target == "upgrade" and status >= 400:
            if backup:
                print(f"[MagicProxy] upgrade status={status}，试备用云 {backup.get('name','?')}", flush=True)
                out = await _stream_response(request, {})
                return await _serve(out, request, backup, body_bytes)
            print(f"[MagicProxy] upgrade status={status} 且无备用云，退回本地 base", flush=True)
            try:
                with open(TARGET_FILE, "w", encoding="utf-8") as f:
                    json.dump({"target": "base", "updated_at": time.time(),
                               "reason": "upgrade_failed_fallback"}, f)
            except Exception:
                pass
            base_provider = cfg.get("base", {}).get("primary", {})
            out = await _stream_response(request, {})
            return await _serve(out, request, base_provider, body_bytes)

        out = await _stream_response(request, resp_headers)
        return await _serve(out, request, provider, body_bytes, first=first_chunk)

    except Exception as e:
        print(f"[MagicProxy] Primary {provider.get('name','?')} failed: {e}", flush=True)
        # 升级目标连接异常（云端不可达）：先试备用云，没备用才退本地。
        if target == "upgrade":
            if backup:
                print(f"[MagicProxy] upgrade 连接异常，试备用云 {backup.get('name','?')}", flush=True)
                out = await _stream_response(request, {})
                return await _serve(out, request, backup, body_bytes)
            print("[MagicProxy] upgrade 连接异常且无备用云，退回本地 base", flush=True)
            try:
                with open(TARGET_FILE, "w", encoding="utf-8") as f:
                    json.dump({"target": "base", "updated_at": time.time(),
                               "reason": "upgrade_unreachable_fallback"}, f)
            except Exception:
                pass
            base_provider = cfg.get("base", {}).get("primary", {})
            out = await _stream_response(request, {})
            return await _serve(out, request, base_provider, body_bytes)
        if backup:
            out = await _stream_response(request, {})
            return await _serve(out, request, backup, body_bytes)
        return web.json_response(
            {"error": {"message": str(e), "type": "proxy_error"}}, status=502)


async def _drain_first(upstream):
    """Read only the first byte-chunk of an upstream response.

    Returns (status, headers, first_chunk_bytes). We deliberately read just
    one chunk so the remainder can be streamed separately, which is what
    breaks when you buffer the whole body.
    """
    status = upstream.status
    resp_headers = {k: v for k, v in upstream.headers.items()}
    first_chunk = await upstream.content.readany()
    return status, resp_headers, first_chunk


def _is_ctx_error(body):
    try:
        text = body.decode("utf-8", errors="replace").lower()
    except Exception:
        text = str(body).lower()
    return ("context_length" in text or "maximum context" in text
            or "too many tokens" in text)


async def _stream_response(request, headers):
    out = web.StreamResponse(headers=headers)
    out.headers["Content-Type"] = "text/event-stream"
    out.headers["Cache-Control"] = "no-cache, no-transform"
    out.headers["Connection"] = "keep-alive"
    await out.prepare(request)
    return out


async def _serve(out, request, provider, body_bytes, backup=None, upstream=None, first=b""):
    """Stream a live (or newly-opened) upstream through `out`.

    `first` holds bytes already read via _drain_first so the first chunk is
    sent before the remainder. Falls back to `backup` if the primary dies.
    """
    try:
        if first:
            await out.write(first)
            first = b""
        upstream = upstream or await _upstream_request(
            provider, request.method,
            build_url(provider, request.path), body_bytes)
        async for chunk in upstream.content.iter_chunked(16384):
            await out.write(chunk)
        return out
    except Exception as e:
        print(f"[MagicProxy] {provider.get('name','?')} stream error: {e}", flush=True)
        if backup:
            try:
                resp2 = await _upstream_request(
                    backup, request.method,
                    build_url(backup, request.path), body_bytes)
                out.headers["Content-Type"] = "text/event-stream"
                await out.prepare(request)
                if first:
                    await out.write(first)
                async for chunk in resp2.content.iter_chunked(16384):
                    await out.write(chunk)
                return out
            except Exception as e2:
                print(f"[MagicProxy] backup stream also failed: {e2}", flush=True)
        return out


async def handle_models(request):
    cfg = load_config()
    provider = cfg.get("base", {}).get("primary", {})
    url = build_url(provider, "/v1/models")
    ak = provider.get("api_key", "")
    hdrs = {"x-api-key": ak, "Authorization": f"Bearer {ak}"}
    try:
        async with _CLIENT.get(url, headers=hdrs, timeout=ClientTimeout(total=10)) as resp:
            return web.Response(status=resp.status, body=await resp.read(), content_type="application/json")
    except Exception as e:
        return web.json_response({"error": str(e)}, status=502)


# ── Startup ─────────────────────────────────────────

async def start_proxy():
    global _CLIENT
    _CLIENT = ClientSession()

    app = web.Application()
    app.router.add_route("*", "/health", handle_models)
    app.router.add_route("*", "/v1/models", handle_models)
    app.router.add_route("*", "/{tail:.*}", handle_request)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 15666)
    await site.start()
    cfg = load_config()
    cname = cfg.get("base", {}).get("primary", {}).get("name", "?")
    patterns = cfg.get("activation", {}).get("model_patterns", ["*"])
    print(f"[MagicProxy] started on 127.0.0.1:15666", flush=True)
    print(f"[MagicProxy] base: {cname}, activation patterns: {patterns}", flush=True)

    await asyncio.Event().wait()


def main():
    try:
        asyncio.run(start_proxy())
    except KeyboardInterrupt:
        print("[MagicProxy] stopped", flush=True)


if __name__ == "__main__":
    main()
