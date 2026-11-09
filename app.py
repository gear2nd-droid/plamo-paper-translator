from __future__ import annotations

import argparse
import atexit
import base64
import json
import mimetypes
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from paper_core import (
    LlamaTranslator,
    Segment,
    bilingual_html,
    bilingual_markdown,
    document_to_dict,
    extract_document,
    neighboring_context,
    parse_glossary,
    segments_for_mode,
)


def _configured_frozen_user_dir(executable: Path) -> Path | None:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        return None
    config_path = Path(local_app_data) / "PLaMoPaperTranslator" / "launcher-config.json"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        configured_executable = Path(str(payload["executable"])).resolve()
        app_home = Path(str(payload["app_home"])).resolve()
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return app_home if configured_executable == executable.resolve() and app_home.is_dir() else None


APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
if os.environ.get("PLAMO_PAPER_HOME"):
    USER_DIR = Path(os.environ["PLAMO_PAPER_HOME"])
elif getattr(sys, "frozen", False):
    USER_DIR = _configured_frozen_user_dir(Path(sys.executable)) or Path(sys.executable).resolve().parent
else:
    USER_DIR = APP_DIR
APP_URL = "http://127.0.0.1:7865/"
NATIVE_HOST_NAME = "jp.gear2.plamo_paper_translator"
CHROME_EXTENSION_ID = "cpiiadekehcofkocnlplcaolgioefcjd"
CHROME_EXTENSION_ORIGIN = f"chrome-extension://{CHROME_EXTENSION_ID}/"
STATIC_DIR = APP_DIR / "static"
DATA_DIR = USER_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
JOB_DIR = DATA_DIR / "jobs"
ASSET_DIR = DATA_DIR / "assets"
RUNTIME_DIR = USER_DIR / "runtime"
MODEL_DIR = USER_DIR / "models"
for directory in (DATA_DIR, UPLOAD_DIR, JOB_DIR, ASSET_DIR):
    directory.mkdir(parents=True, exist_ok=True)

documents: dict[str, dict[str, Any]] = {}
jobs: dict[str, dict[str, Any]] = {}
state_lock = threading.RLock()
translator = LlamaTranslator()


def _read_native_message(stream: Any) -> dict[str, Any]:
    header = stream.read(4)
    if len(header) != 4:
        raise ValueError("Native Messagingのヘッダーを読み取れませんでした。")
    message_length = struct.unpack("=I", header)[0]
    if message_length > 64 * 1024 * 1024:
        raise ValueError("Native Messagingのメッセージが大きすぎます。")
    payload = stream.read(message_length)
    if len(payload) != message_length:
        raise ValueError("Native Messagingのメッセージが途中で終了しました。")
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Native Messagingのリクエスト形式が不正です。")
    return value


def _write_native_message(stream: Any, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    stream.write(struct.pack("=I", len(body)))
    stream.write(body)
    stream.flush()


def _app_is_available(timeout: float = 0.7) -> bool:
    try:
        with urllib.request.urlopen(APP_URL + "api/status", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return response.status == 200 and isinstance(payload, dict) and "engine" in payload
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _launch_detached_server() -> None:
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--no-browser"]
    else:
        command = [sys.executable, str(Path(__file__).resolve()), "--no-browser"]
    options: dict[str, Any] = {
        "cwd": str(USER_DIR),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if getattr(sys, "frozen", False):
        options["env"] = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    if os.name == "nt":
        base_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        breakaway_flag = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        try:
            subprocess.Popen(command, creationflags=base_flags | breakaway_flag, **options)
            return
        except OSError:
            subprocess.Popen(command, creationflags=base_flags, **options)
            return
    subprocess.Popen(command, start_new_session=True, **options)


def _is_native_messaging_invocation(arguments: list[str]) -> bool:
    return "--native-messaging" in arguments or CHROME_EXTENSION_ORIGIN in arguments


def _native_messaging_main() -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    response: dict[str, Any]
    try:
        request = _read_native_message(sys.stdin.buffer)
        if request.get("action") != "launch":
            raise ValueError("未対応の操作です。")
        was_running = _app_is_available()
        if not was_running:
            _launch_detached_server()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not _app_is_available():
                time.sleep(0.25)
        available = _app_is_available()
        response = {
            "ok": available,
            "running": available,
            "started": available and not was_running,
            "url": APP_URL,
            "error": "" if available else "アプリの起動を15秒以内に確認できませんでした。",
        }
    except Exception as exc:
        response = {"ok": False, "running": False, "started": False, "url": APP_URL, "error": str(exc)}
    _write_native_message(sys.stdout.buffer, response)


def _safe_name(name: str) -> str:
    name = Path(name).name
    name = re.sub(r"[^\w.()\-\u3040-\u30ff\u3400-\u9fff ]+", "_", name)
    return name[:160] or "document.pdf"


def _find_one(root: Path, pattern: str) -> Path | None:
    candidates = sorted(root.rglob(pattern)) if root.exists() else []
    return candidates[0] if candidates else None


class LlamaProcess:
    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None
        self.log_handle: Any = None
        self.error = ""

    def start(self) -> None:
        if translator.health():
            return
        executable = Path(os.environ.get("PLAMO_LLAMA_SERVER", "")) if os.environ.get("PLAMO_LLAMA_SERVER") else _find_one(RUNTIME_DIR, "llama-server.exe")
        model = Path(os.environ.get("PLAMO_MODEL", "")) if os.environ.get("PLAMO_MODEL") else _find_one(MODEL_DIR, "*.gguf")
        template = _find_one(MODEL_DIR, "chat_template.jinja")
        if not executable or not executable.exists():
            self.error = "llama-server.exe がありません。setup.ps1 を実行してください。"
            return
        if not model or not model.exists():
            self.error = "PLaMo 3 Translate モデルがありません。setup.ps1 を実行してください。"
            return
        command = [
            str(executable), "-m", str(model), "-c", "16384", "--host", "127.0.0.1", "--port", "8041",
            "--alias", "plamo-3-translate", "--no-prefill-assistant", "-ngl", "99",
        ]
        if template and template.exists():
            command.extend(["--chat-template-file", str(template)])
        else:
            command.append("--jinja")
        self.log_handle = (DATA_DIR / "llama-server.log").open("a", encoding="utf-8")
        startup = subprocess.STARTUPINFO() if os.name == "nt" else None
        if startup:
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(USER_DIR),
                stdout=self.log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                startupinfo=startup,
            )
        except OSError as exc:
            self.error = f"翻訳エンジンを起動できません: {exc}"
            return
        for _ in range(180):
            if translator.health():
                self.error = ""
                return
            if self.process.poll() is not None:
                self.error = "翻訳エンジンが起動直後に終了しました。data/llama-server.log を確認してください。"
                return
            time.sleep(1)
        self.error = "モデルの読み込みが3分以内に完了しませんでした。"

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.log_handle:
            self.log_handle.close()


llama_process = LlamaProcess()
atexit.register(llama_process.stop)


def _job_snapshot(job: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in job.items() if key not in {"segments"}}


def _save_job(job: dict[str, Any]) -> None:
    payload = dict(job)
    payload["segments"] = [segment.__dict__ if isinstance(segment, Segment) else segment for segment in job["segments"]]
    (JOB_DIR / f'{job["id"]}.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _asset_path(url: str) -> Path | None:
    match = re.fullmatch(r"/api/assets/([a-f0-9]{32})/([\w.-]+)", urllib.parse.urlparse(url).path)
    if not match:
        return None
    target = (ASSET_DIR / match.group(1) / match.group(2)).resolve()
    root = ASSET_DIR.resolve()
    return target if root in target.parents and target.is_file() else None


def _inline_asset(url: str) -> str:
    target = _asset_path(url)
    if not target:
        return url
    mime_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(target.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _load_saved_job(job_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        return None
    with state_lock:
        if job_id in jobs:
            return jobs[job_id]
    path = JOB_DIR / f"{job_id}.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["segments"] = [
            segment if isinstance(segment, Segment) else Segment(**segment)
            for segment in payload.get("segments", [])
        ]
    except (OSError, ValueError, TypeError):
        return None
    if payload.get("status") in {"queued", "running"}:
        payload["status"] = "failed"
        payload["message"] = "アプリが再起動したため翻訳が中断されました。もう一度翻訳を開始してください。"
        _save_job(payload)
    with state_lock:
        jobs[job_id] = payload
    return payload


def _translate_job(job_id: str) -> None:
    with state_lock:
        job = jobs[job_id]
        job["status"] = "running"
        job["message"] = "翻訳エンジンを確認しています"
    if not translator.health():
        llama_process.start()
    if not translator.health():
        with state_lock:
            job["status"] = "failed"
            job["message"] = llama_process.error or "翻訳エンジンを起動できませんでした。"
            _save_job(job)
        return

    segments: list[Segment] = job["segments"]
    glossary = job["glossary"]
    for index, segment in enumerate(segments):
        with state_lock:
            if job.get("cancelled"):
                job["status"] = "cancelled"
                job["message"] = "キャンセルしました"
                _save_job(job)
                return
            job["message"] = f"{index + 1} / {len(segments)} 行を翻訳中"
            job["completed"] = index
        if segment.kind == "equation" and segment.assets:
            # Preserve the source equation exactly. The bitmap is authoritative;
            # extracted text remains available only for search and copying.
            segment.translation = segment.source
        elif segment.label != "asset":
            context = neighboring_context(segments, index, job.get("context", ""))
            try:
                segment.translation = translator.translate(
                    segment.source,
                    target_lang=job["target_lang"],
                    source_lang=job["source_lang"],
                    form=job["form"],
                    context=context,
                    glossary=glossary,
                )
            except Exception as exc:
                segment.error = str(exc)
        with state_lock:
            job["completed"] = index + 1
            job["progress"] = round((index + 1) / len(segments) * 100, 1)
            if index % 3 == 0:
                _save_job(job)
    with state_lock:
        failed = sum(bool(segment.error) for segment in segments)
        job["status"] = "completed" if failed == 0 else "completed_with_errors"
        job["message"] = "翻訳が完了しました" if failed == 0 else f"翻訳完了（{failed}件のエラーあり）"
        job["progress"] = 100
        _save_job(job)


class Handler(BaseHTTPRequestHandler):
    server_version = "PLaMoPaperTranslator/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": message}, status)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2_000_000:
            raise ValueError("リクエストが大きすぎます。")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/status":
            model = _find_one(MODEL_DIR, "*.gguf")
            self._json(
                {
                    "engine": "ready" if translator.health() else "not_ready",
                    "engine_error": llama_process.error,
                    "model": model.name if model else None,
                    "gpu_recommended": "PLaMo 3 Translate 8B Q4_K_M / RTX 2000 Ada 16GB",
                }
            )
            return
        asset_match = re.fullmatch(r"/api/assets/([a-f0-9]{32})/([\w.-]+)", path)
        if asset_match:
            target = _asset_path(path)
            if not target:
                self._error("画像が見つかりません。", 404)
                return
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, max-age=3600")
            self.end_headers()
            self.wfile.write(body)
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]+)", path)
        if match:
            job = _load_saved_job(match.group(1))
            if not job:
                self._error("ジョブが見つかりません。", 404)
                return
            with state_lock:
                payload = _job_snapshot(job)
                if job["status"].startswith("completed"):
                    payload["segments"] = [segment.__dict__ for segment in job["segments"]]
            self._json(payload)
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]+)/export", path)
        if match:
            job = _load_saved_job(match.group(1))
            if not job:
                self._error("ジョブが見つかりません。", 404)
                return
            fmt = urllib.parse.parse_qs(parsed.query).get("format", ["html"])[0]
            if fmt == "md":
                content = bilingual_markdown(job["title"], job["segments"]).encode("utf-8")
                content_type, ext = "text/markdown; charset=utf-8", "md"
            elif fmt == "json":
                content = json.dumps(
                    document_to_dict(
                        job["title"],
                        job["segments"],
                        source_lang=job["source_lang"],
                        target_lang=job["target_lang"],
                        segmentation_mode=job.get("segmentation_mode", "sentence"),
                    ),
                    ensure_ascii=False,
                    indent=2,
                ).encode("utf-8")
                content_type, ext = "application/json; charset=utf-8", "json"
            else:
                content = bilingual_html(job["title"], job["segments"], asset_resolver=_inline_asset).encode("utf-8")
                content_type, ext = "text/html; charset=utf-8", "html"
            filename = re.sub(r"[^\w\-]+", "_", job["title"])[:60] or "translation"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f'attachment; filename="{filename}.{ext}"')
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        self._serve_static(path)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"/", "/index.html"} else path.lstrip("/")
        target = (STATIC_DIR / relative).resolve()
        if STATIC_DIR.resolve() not in target.parents and target != STATIC_DIR.resolve():
            self._error("Not found", 404)
            return
        if not target.is_file():
            self._error("Not found", 404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", (mimetypes.guess_type(target.name)[0] or "application/octet-stream") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/documents":
            self._upload_document()
            return
        if parsed.path == "/api/jobs":
            self._create_job()
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]+)/notes", parsed.path)
        if match:
            job = _load_saved_job(match.group(1))
            if not job:
                self._error("ジョブが見つかりません。", 404)
                return
            try:
                data = self._read_json()
                index = int(data.get("index", -1))
                note = str(data.get("note", ""))[:10_000]
            except (TypeError, ValueError, json.JSONDecodeError):
                self._error("メモを保存できません。")
                return
            with state_lock:
                if index < 0 or index >= len(job["segments"]):
                    self._error("対訳位置が不正です。")
                    return
                job["segments"][index].note = note
                _save_job(job)
            self._json({"ok": True, "index": index})
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]+)/cancel", parsed.path)
        if match:
            with state_lock:
                job = jobs.get(match.group(1))
                if not job:
                    self._error("ジョブが見つかりません。", 404)
                    return
                job["cancelled"] = True
            self._json({"ok": True})
            return
        self._error("Not found", 404)

    def _upload_document(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 80 * 1024 * 1024:
            self._error("ファイルは80MB以下にしてください。")
            return
        filename = _safe_name(urllib.parse.unquote(self.headers.get("X-Filename", "document.pdf")))
        if Path(filename).suffix.lower() not in {".pdf", ".txt", ".md"}:
            self._error("対応形式は PDF / TXT / Markdown です。")
            return
        document_id = uuid.uuid4().hex
        target = UPLOAD_DIR / f"{document_id}_{filename}"
        with target.open("wb") as handle:
            remaining = length
            while remaining:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                handle.write(chunk)
                remaining -= len(chunk)
        try:
            document_assets = ASSET_DIR / document_id
            segments, pages = extract_document(
                target,
                asset_dir=document_assets,
                asset_url_prefix=f"/api/assets/{document_id}",
            )
        except Exception as exc:
            target.unlink(missing_ok=True)
            shutil.rmtree(ASSET_DIR / document_id, ignore_errors=True)
            self._error(str(exc))
            return
        title = Path(filename).stem
        document = {"id": document_id, "title": title, "filename": filename, "path": str(target), "pages": pages, "segments": segments}
        with state_lock:
            documents[document_id] = document
        self._json(
            {
                "id": document_id,
                "title": title,
                "filename": filename,
                "pages": pages,
                "segment_count": len(segments),
                "characters": sum(len(segment.source) for segment in segments),
                "preview": [segment.__dict__ for segment in segments[:3]],
            },
            HTTPStatus.CREATED,
        )

    def _create_job(self) -> None:
        try:
            data = self._read_json()
        except Exception as exc:
            self._error(f"設定を読み込めません: {exc}")
            return
        with state_lock:
            document = documents.get(str(data.get("document_id", "")))
        if not document:
            self._error("先に論文ファイルを読み込んでください。")
            return
        target_lang = str(data.get("target_lang", "ja"))
        source_lang = str(data.get("source_lang", "auto"))
        if target_lang not in {"ja", "en", "zh", "ko", "de", "fr", "es", "it", "pt", "ru"}:
            self._error("翻訳先言語が不正です。")
            return
        segmentation_mode = str(data.get("segmentation_mode", "sentence"))
        try:
            selected_segments = segments_for_mode(document["segments"], segmentation_mode)
        except ValueError as exc:
            self._error(str(exc))
            return
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "document_id": document["id"],
            "title": str(data.get("title") or document["title"])[:200],
            "status": "queued",
            "message": "翻訳開始を待っています",
            "progress": 0,
            "completed": 0,
            "total": len(selected_segments),
            "segmentation_mode": segmentation_mode,
            "source_lang": source_lang,
            "target_lang": target_lang,
            "form": str(data.get("form", "plain")),
            "context": str(data.get("context", ""))[:4000],
            "glossary": parse_glossary(str(data.get("glossary", ""))),
            "segments": selected_segments,
            "cancelled": False,
        }
        with state_lock:
            jobs[job_id] = job
        threading.Thread(target=_translate_job, args=(job_id,), daemon=True).start()
        self._json(_job_snapshot(job), HTTPStatus.CREATED)


def _port_available(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def main() -> None:
    if _is_native_messaging_invocation(sys.argv[1:]):
        _native_messaging_main()
        return
    parser = argparse.ArgumentParser(description="PLaMo Paper Translator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7865)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-engine", action="store_true")
    args = parser.parse_args()
    if not _port_available(args.port):
        print(f"Port {args.port} is already in use. Open http://127.0.0.1:{args.port}")
        if not args.no_browser:
            webbrowser.open(f"http://127.0.0.1:{args.port}")
        return
    if not args.no_engine:
        threading.Thread(target=llama_process.start, daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"PLaMo Paper Translator: {url}")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        llama_process.stop()


if __name__ == "__main__":
    main()
