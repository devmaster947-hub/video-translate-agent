"""Loopback-only browser wizard for secure ElevenLabs credential setup."""
from __future__ import annotations

from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import secrets
import sys
import time
from urllib.parse import parse_qs
import webbrowser

from .credentials import CredentialError, credential_storage_name, store_credential_value
from .network import ProviderError, json_response, request


ELEVENLABS_KEYS_URL = "https://elevenlabs.io/app/settings/api-keys"
MAX_REQUEST_BYTES = 16 * 1024


class CredentialSetupError(RuntimeError):
    """Stable setup failure that never contains credential material."""


def verify_elevenlabs_credential(api_key: str, *, session=None) -> None:
    """Verify authentication plus the read scopes used by the workflow, without TTS spend."""
    headers = {"xi-api-key": api_key}
    try:
        models = json_response(request(
            "GET", "https://api.elevenlabs.io/v1/models", session=session,
            headers=headers, attempts=1,
        ))
        voices = json_response(request(
            "GET", "https://api.elevenlabs.io/v2/voices", session=session,
            headers=headers, params={"page_size": 1}, attempts=1,
        ))
    except ProviderError as exc:
        code = str(exc)
        if code in {"REMOTE_HTTP_401", "REMOTE_HTTP_403"}:
            raise CredentialSetupError("CREDENTIAL_PERMISSION_INSUFFICIENT") from None
        raise CredentialSetupError(code) from None
    if not isinstance(models, list) or not isinstance(voices, dict) or not isinstance(voices.get("voices"), list):
        raise CredentialSetupError("INVALID_REMOTE_JSON")


def _headers(handler: BaseHTTPRequestHandler, status: int, content_type: str) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", "no-store, max-age=0")
    handler.send_header("Pragma", "no-cache")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header(
        "Content-Security-Policy",
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'",
    )
    handler.end_headers()


def _setup_page(action: str, csrf: str, error: str | None = None) -> bytes:
    notice = f'<p class="error">{escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>连接 ElevenLabs</title><style>
body{{font:16px system-ui,sans-serif;background:#f5f5f7;color:#1d1d1f;margin:0;padding:32px}}
main{{max-width:680px;margin:auto;background:white;padding:28px;border-radius:16px;box-shadow:0 8px 30px #0001}}
li{{margin:.65em 0}} input{{box-sizing:border-box;width:100%;padding:12px;margin:8px 0 16px}}
button,a.button{{display:inline-block;border:0;border-radius:9px;padding:11px 16px;background:#111;color:white;text-decoration:none}}
button.secondary{{background:#777}} .error{{background:#fff0f0;color:#a40000;padding:12px;border-radius:8px}}
small{{color:#666}}
</style></head><body><main><h1>连接 ElevenLabs</h1>{notice}
<ol><li>打开 <a href="{ELEVENLABS_KEYS_URL}" target="_blank" rel="noopener noreferrer">ElevenLabs API Keys</a> 并登录。</li>
<li>创建名为 <strong>video-translate-agent</strong> 的 Key。</li>
<li>开启 <code>text_to_speech</code>、<code>voices_read</code>、<code>models_read</code>，并设置你能接受的额度上限和可选有效期。</li>
<li>复制新 Key，粘贴到下方。完整 Key 通常只会显示一次。</li></ol>
<form method="post" action="{escape(action, quote=True)}" autocomplete="off">
<input type="hidden" name="csrf" value="{escape(csrf, quote=True)}">
<label for="api_key">ElevenLabs API Key</label>
<input id="api_key" name="api_key" type="password" required autocomplete="off" spellcheck="false">
<button type="submit" name="action" value="connect">连接 ElevenLabs</button>
<button class="secondary" type="submit" name="action" value="cancel" formnovalidate>取消</button>
</form><p><small>Key 只会通过本机 127.0.0.1 传给技能。macOS 保存到仅当前用户可读的本地凭据文件，Windows 保存到 Credential Manager；不会写入任务文件或日志。</small></p>
</main></body></html>""".encode("utf-8")


def _result_page(success: bool) -> bytes:
    title = "连接成功" if success else "已取消"
    detail = "可以关闭此页面并返回视频翻译任务。" if success else "未保存任何 API Key。"
    return (f"<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>{title}</title>"
            f"<body><h1>{title}</h1><p>{detail}</p></body></html>").encode("utf-8")


class _Wizard:
    def __init__(self, *, session=None):
        self.session = session
        self.path_token = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)
        self.result: str | None = None

    @property
    def path(self) -> str:
        return f"/setup/{self.path_token}"

    def handler(self):
        wizard = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def _valid_request(self) -> bool:
                expected_host = f"127.0.0.1:{self.server.server_port}"
                return (self.client_address[0] == "127.0.0.1"
                        and self.headers.get("Host") == expected_host
                        and self.path == wizard.path)

            def _write(self, status: int, body: bytes, content_type="text/html; charset=utf-8"):
                _headers(self, status, content_type)
                self.wfile.write(body)

            def do_GET(self):
                if not self._valid_request():
                    self._write(404, b"Not found", "text/plain; charset=utf-8")
                    return
                self._write(200, _setup_page(wizard.path, wizard.csrf))

            def do_POST(self):
                if not self._valid_request():
                    self._write(404, b"Not found", "text/plain; charset=utf-8")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = -1
                if length < 0 or length > MAX_REQUEST_BYTES:
                    self._write(413, b"Request too large", "text/plain; charset=utf-8")
                    return
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type != "application/x-www-form-urlencoded":
                    self._write(415, b"Unsupported media type", "text/plain; charset=utf-8")
                    return
                form = parse_qs(self.rfile.read(length).decode("utf-8", "strict"), keep_blank_values=True)
                if form.get("csrf", [""])[0] != wizard.csrf:
                    self._write(403, b"Forbidden", "text/plain; charset=utf-8")
                    return
                if form.get("action", [""])[0] == "cancel":
                    wizard.result = "CREDENTIAL_SETUP_CANCELLED"
                    self._write(200, _result_page(False))
                    return
                api_key = form.get("api_key", [""])[0].strip()
                if not api_key:
                    self._write(400, _setup_page(wizard.path, wizard.csrf, "请输入 ElevenLabs API Key。"))
                    return
                try:
                    verify_elevenlabs_credential(api_key, session=wizard.session)
                    store_credential_value("elevenlabs", api_key)
                except CredentialSetupError as exc:
                    messages = {
                        "CREDENTIAL_PERMISSION_INSUFFICIENT": "Key 无效、已过期，或缺少 models_read / voices_read 权限。请检查后重试。",
                        "NETWORK_REQUEST_FAILED": "无法连接 ElevenLabs，请检查网络后重试。",
                        "REMOTE_HTTP_429": "ElevenLabs 暂时限流，请稍后重试。",
                        "INVALID_REMOTE_JSON": "ElevenLabs 返回了无法识别的数据，请稍后重试。",
                    }
                    self._write(400, _setup_page(wizard.path, wizard.csrf, messages.get(str(exc), "验证失败，请检查 Key 和权限后重试。")))
                    return
                except CredentialError:
                    self._write(500, _setup_page(wizard.path, wizard.csrf, "无法写入本机凭据存储，请检查目录或系统凭据服务后重试。"))
                    return
                finally:
                    api_key = ""
                wizard.result = "SUCCESS"
                self._write(200, _result_page(True))

        return Handler


def run_credential_setup(*, timeout_seconds: int = 600, open_browser=True, session=None) -> dict:
    """Run the browser wizard and return a secret-free result."""
    if timeout_seconds < 1 or timeout_seconds > 3600:
        raise ValueError("timeout_seconds must be between 1 and 3600")
    wizard = _Wizard(session=session)
    server = HTTPServer(("127.0.0.1", 0), wizard.handler())
    local_url = f"http://127.0.0.1:{server.server_port}{wizard.path}"
    opened = True
    if open_browser:
        try:
            opened = bool(webbrowser.open(local_url, new=2))
            opened = bool(webbrowser.open(ELEVENLABS_KEYS_URL, new=2)) and opened
        except webbrowser.Error:
            opened = False
    else:
        opened = False
    if not opened:
        print(json.dumps({"error": "BROWSER_OPEN_FAILED", "setup_url": local_url}, ensure_ascii=False), file=sys.stderr, flush=True)
    deadline = time.monotonic() + timeout_seconds
    try:
        while wizard.result is None and time.monotonic() < deadline:
            server.timeout = min(0.5, max(0.01, deadline - time.monotonic()))
            server.handle_request()
    finally:
        server.server_close()
    if wizard.result == "SUCCESS":
        return {"provider": "elevenlabs", "stored": True,
                "access_verified": True, "storage": credential_storage_name()}
    if wizard.result == "CREDENTIAL_SETUP_CANCELLED":
        raise CredentialSetupError("CREDENTIAL_SETUP_CANCELLED")
    raise CredentialSetupError("CREDENTIAL_SETUP_TIMEOUT")
