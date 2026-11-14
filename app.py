from __future__ import annotations

import argparse
import atexit
import base64
import hashlib
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
import zipfile
from datetime import UTC, datetime
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
    merge_reextracted_segments,
    neighboring_context,
    parse_glossary,
    remap_summary_row_references,
    segments_for_mode,
    strip_script_markup,
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
MAX_CLIPS = 3
CLIP_PARTS = {"source", "translation", "note"}
MAX_PAPER_TAGS = 20
MAX_PAPER_TAG_LENGTH = 40
MAX_PAPER_SUMMARY_LENGTH = 20_000
MAX_TRANSLATION_LENGTH = 50_000
MAX_TRANSLATION_REVISIONS = 20
READING_STATUSES = {"unread", "reading", "completed", "archived"}
MAX_BACKUP_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_BACKUP_EXPANDED_BYTES = 8 * 1024 * 1024 * 1024
MAX_BACKUP_FILES = 100_000
# One version for the app, the Chrome extension and the release.
APP_VERSION = "1.3.1"
NATIVE_HOST_NAME = "jp.gear2.plamo_paper_translator"
CHROME_EXTENSION_ID = "cpiiadekehcofkocnlplcaolgioefcjd"
CHROME_EXTENSION_ORIGIN = f"chrome-extension://{CHROME_EXTENSION_ID}/"
STATIC_DIR = APP_DIR / "static"
DATA_DIR = USER_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
JOB_DIR = DATA_DIR / "jobs"
ASSET_DIR = DATA_DIR / "assets"
CONTROL_FILE = DATA_DIR / "server-control.json"
CONTROL_TOKEN_HEADER = "X-PLaMo-Control-Token"
RUNTIME_DIR = USER_DIR / "runtime"
MODEL_DIR = USER_DIR / "models"
for directory in (DATA_DIR, UPLOAD_DIR, JOB_DIR, ASSET_DIR):
    directory.mkdir(parents=True, exist_ok=True)

documents: dict[str, dict[str, Any]] = {}
jobs: dict[str, dict[str, Any]] = {}
active_retranslations: set[tuple[str, int]] = set()
active_reextractions: set[str] = set()
REEXTRACT_BACKUP_DIR = DATA_DIR / "reextract-backups"
MAX_REEXTRACT_BACKUPS = 3
control_token = ""
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


def _app_is_available(timeout: float = 1.5) -> bool:
    # /api/ping never touches the translation engine. /api/status does, and on
    # Windows a refused localhost connection takes ~2 s, which made a running
    # app look stopped whenever llama-server was down.
    try:
        with urllib.request.urlopen(APP_URL + "api/ping", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return response.status == 200 and isinstance(payload, dict) and payload.get("app") == "plamo-paper-translator"
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


def _server_is_busy() -> bool:
    with state_lock:
        if active_retranslations or active_reextractions:
            return True
        return any(job.get("status") in {"queued", "running"} for job in jobs.values())


def _write_control_file(port: int) -> str:
    """Record how the native host may ask this server instance to stop."""
    token = secrets.token_urlsafe(32)
    payload = {"pid": os.getpid(), "port": port, "token": token}
    CONTROL_FILE.write_text(json.dumps(payload), encoding="utf-8")
    return token


def _remove_control_file(token: str) -> None:
    try:
        payload = json.loads(CONTROL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    # A newer instance may already have replaced the file; leave it alone.
    if isinstance(payload, dict) and payload.get("token") == token:
        CONTROL_FILE.unlink(missing_ok=True)


def _read_control_file() -> dict[str, Any] | None:
    try:
        payload = json.loads(CONTROL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("token"), str) or not isinstance(payload.get("pid"), int):
        return None
    return payload


def _wait_for_process_exit(pid: int, timeout: float) -> bool:
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        synchronize = 0x00100000
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return True
        try:
            return kernel32.WaitForSingleObject(handle, int(timeout * 1000)) == 0
        finally:
            kernel32.CloseHandle(handle)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.2)
    return False


def _request_server_shutdown() -> str:
    """Ask the running server to stop. Returns "stopped" or "busy"."""
    control = _read_control_file()
    if not control:
        raise ValueError("起動中のアプリに再起動を依頼できません。アプリを一度終了してから、もう一度拡張アイコンを押してください。")
    request = urllib.request.Request(
        APP_URL + "api/control/shutdown",
        data=b"",
        headers={CONTROL_TOKEN_HEADER: control["token"]},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5):
            pass
    except urllib.error.HTTPError as exc:
        if exc.code == HTTPStatus.CONFLICT:
            return "busy"
        raise ValueError(f"アプリの終了を依頼できませんでした（HTTP {exc.code}）。") from exc
    if not _wait_for_process_exit(control["pid"], 30):
        raise ValueError("アプリが30秒以内に終了しませんでした。")
    return "stopped"


def _start_server_and_wait() -> bool:
    _launch_detached_server()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if _app_is_available():
            return True
        time.sleep(0.25)
    return _app_is_available()


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
        action = request.get("action")
        if action not in {"launch", "restart"}:
            raise ValueError("未対応の操作です。")
        was_running = _app_is_available()
        busy = False
        if was_running and action == "restart":
            busy = _request_server_shutdown() == "busy"
        if busy:
            available = True
        elif was_running and action == "launch":
            available = _app_is_available()
        else:
            available = _start_server_and_wait()
        started = available and not busy and (action == "restart" or not was_running)
        response = {
            "ok": available,
            "running": available,
            "started": started,
            "restarted": started and was_running,
            "busy": busy,
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


def _suggest_source_url(filename: str, segments: list[Segment]) -> str:
    filename_arxiv = re.fullmatch(r"(?:arxiv[_-]?)?(\d{4}\.\d{4,5})(?:v\d+)?", Path(filename).stem, re.I)
    sample = "\n".join(segment.source for segment in segments[:20])
    arxiv = filename_arxiv or re.search(r"\barxiv\s*:\s*(\d{4}\.\d{4,5})(?:v\d+)?", sample, re.I)
    if arxiv:
        return f"https://arxiv.org/abs/{arxiv.group(1)}"
    doi = re.search(r"(?:doi\s*:\s*|https?://doi\.org/)(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", sample, re.I)
    if doi:
        return f"https://doi.org/{doi.group(1).rstrip('.,;')}"
    return ""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _find_one(root: Path, pattern: str) -> Path | None:
    candidates = sorted(root.rglob(pattern)) if root.exists() else []
    return candidates[0] if candidates else None


class LlamaProcess:
    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None
        self.log_handle: Any = None
        self.error = ""
        self.start_lock = threading.Lock()

    def start(self) -> None:
        with self.start_lock:
            self._start_locked()

    def _start_locked(self) -> None:
        if translator.health():
            return
        if self.process and self.process.poll() is None:
            for _ in range(180):
                if translator.health():
                    self.error = ""
                    return
                if self.process.poll() is not None:
                    self.error = "翻訳エンジンがモデル読込中に終了しました。data/llama-server.log を確認してください。"
                    return
                time.sleep(1)
            self.error = "モデルの読み込みが3分以内に完了しませんでした。"
            return
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None
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
    job["updated_at"] = _utc_now()
    payload = dict(job)
    payload["segments"] = [segment.__dict__ if isinstance(segment, Segment) else segment for segment in job["segments"]]
    (JOB_DIR / f'{job["id"]}.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_library_metadata(job: dict[str, Any]) -> None:
    job.setdefault("created_at", "")
    job.setdefault("updated_at", job.get("created_at", ""))
    job.setdefault("source_url", "")
    job.setdefault("source_filename", "")
    job.setdefault("file_sha256", "")
    job.setdefault("reading_status", "unread")
    job.setdefault("reading_index", 0)
    job.setdefault("last_opened_at", "")
    if job["reading_status"] not in READING_STATUSES:
        job["reading_status"] = "unread"
    try:
        job["reading_index"] = max(0, int(job["reading_index"]))
    except (TypeError, ValueError):
        job["reading_index"] = 0


def _set_library_metadata(
    job: dict[str, Any],
    *,
    source_url: Any | None = None,
    reading_status: Any | None = None,
    reading_index: Any | None = None,
    mark_opened: bool = False,
) -> dict[str, Any]:
    _normalize_library_metadata(job)
    if source_url is not None:
        if not isinstance(source_url, str):
            raise ValueError("原文URLの形式が不正です。")
        source_url = source_url.strip()
        if len(source_url) > 2_000:
            raise ValueError("原文URLは2,000文字以内にしてください。")
        if source_url:
            parsed = urllib.parse.urlparse(source_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("原文URLは http:// または https:// から始めてください。")
        job["source_url"] = source_url
    if reading_status is not None:
        if reading_status not in READING_STATUSES:
            raise ValueError("読書状態が不正です。")
        job["reading_status"] = reading_status
    if reading_index is not None:
        try:
            index = int(reading_index)
        except (TypeError, ValueError) as exc:
            raise ValueError("読書位置が不正です。") from exc
        total = max(1, len(job.get("segments", [])))
        job["reading_index"] = min(max(index, 0), total - 1)
        if job["reading_status"] == "unread" and index > 0:
            job["reading_status"] = "reading"
    if mark_opened:
        job["last_opened_at"] = _utc_now()
    return {
        "source_url": job["source_url"],
        "reading_status": job["reading_status"],
        "reading_index": job["reading_index"],
        "last_opened_at": job["last_opened_at"],
    }


def _library_item(job: dict[str, Any], query: str = "") -> dict[str, Any]:
    _normalize_library_metadata(job)
    segments = job.get("segments", [])
    total = max(int(job.get("total", 0) or 0), len(segments))
    reading_index = min(int(job.get("reading_index", 0) or 0), max(0, total - 1))
    item = {
        "id": job.get("id", ""),
        "title": job.get("title", "Untitled"),
        "status": job.get("status", "unknown"),
        "reading_status": job.get("reading_status", "unread"),
        "reading_index": reading_index,
        "reading_progress": round(((reading_index + 1) / total) * 100) if total else 0,
        "total": total,
        "tags": job.get("tags", []),
        "summary": job.get("paper_summary", ""),
        "source_url": job.get("source_url", ""),
        "source_filename": job.get("source_filename", ""),
        "created_at": job.get("created_at", ""),
        "updated_at": job.get("updated_at", ""),
        "last_opened_at": job.get("last_opened_at", ""),
        "match": "",
    }
    normalized_query = query.casefold().strip()
    if normalized_query:
        candidates = [
            str(job.get("title", "")),
            " ".join(str(tag) for tag in job.get("tags", [])),
            str(job.get("paper_summary", "")),
        ]
        for segment in segments:
            if isinstance(segment, Segment):
                candidates.extend([segment.source, segment.translation, segment.note])
            elif isinstance(segment, dict):
                candidates.extend([str(segment.get(key, "")) for key in ("source", "translation", "note")])
        for candidate in candidates:
            # Search the visible text: "Ealgo" finds "E_{algo}".
            compact = re.sub(r"\s+", " ", strip_script_markup(candidate)).strip()
            position = compact.casefold().find(normalized_query)
            if position >= 0:
                start = max(0, position - 55)
                end = min(len(compact), position + len(query) + 95)
                item["match"] = ("…" if start else "") + compact[start:end] + ("…" if end < len(compact) else "")
                break
    return item


def _list_library(query: str = "", reading_status: str = "all") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in JOB_DIR.glob("*.json"):
        job = _load_saved_job(path.stem)
        if not job:
            continue
        item = _library_item(job, query)
        if not (item["created_at"] or item["updated_at"] or item["last_opened_at"]):
            item["updated_at"] = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")
        if reading_status != "all" and item["reading_status"] != reading_status:
            continue
        if query and not item["match"]:
            continue
        items.append(item)
    items.sort(key=lambda item: item["last_opened_at"] or item["updated_at"] or item["created_at"], reverse=True)
    return items


def _duplicate_jobs(file_sha256: str) -> list[dict[str, str]]:
    if not file_sha256:
        return []
    matches = []
    for item in _list_library():
        job = _load_saved_job(item["id"])
        if job and job.get("file_sha256") == file_sha256:
            matches.append({"id": str(job["id"]), "title": str(job.get("title", "Untitled"))})
    return matches


def _validated_backup_members(bundle: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, Path]]:
    members: list[tuple[zipfile.ZipInfo, Path]] = []
    infos = bundle.infolist()
    if len(infos) > MAX_BACKUP_FILES:
        raise ValueError("バックアップ内のファイル数が多すぎます。")
    total_size = sum(info.file_size for info in infos)
    if total_size > MAX_BACKUP_EXPANDED_BYTES:
        raise ValueError("展開後のバックアップ容量が大きすぎます。")
    try:
        manifest_info = bundle.getinfo("backup-manifest.json")
        if manifest_info.file_size > 1024 * 1024:
            raise ValueError("バックアップのマニフェストが大きすぎます。")
        manifest = json.loads(bundle.read(manifest_info).decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("PLaMo Paper Translatorのバックアップではありません。") from exc
    if manifest.get("format") != "plamo-paper-translator-library" or manifest.get("version") != 1:
        raise ValueError("未対応のバックアップ形式です。")

    for info in infos:
        if info.is_dir() or info.filename in {"backup-manifest.json", "RESTORE.txt"}:
            continue
        if info.file_size > MAX_BACKUP_UPLOAD_BYTES:
            raise ValueError("バックアップ内に大きすぎるファイルがあります。")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise ValueError("シンボリックリンクを含むバックアップは復元できません。")
        archive_path = Path(info.filename.replace("\\", "/"))
        if archive_path.is_absolute() or ".." in archive_path.parts or len(archive_path.parts) < 3:
            raise ValueError("バックアップ内に不正なパスがあります。")
        if archive_path.parts[0] != "data" or archive_path.parts[1] not in {"jobs", "uploads", "assets"}:
            raise ValueError("バックアップ内に未対応のファイルがあります。")
        relative = Path(*archive_path.parts[1:])
        category = relative.parts[0]
        suffix = relative.suffix.lower()
        if category == "jobs":
            if len(relative.parts) != 2 or suffix != ".json" or not re.fullmatch(r"[a-f0-9]{32}", relative.stem):
                raise ValueError("バックアップ内のジョブ名が不正です。")
            try:
                payload = json.loads(bundle.read(info).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("バックアップ内のジョブJSONが壊れています。") from exc
            if payload.get("id") != relative.stem or not isinstance(payload.get("segments"), list):
                raise ValueError("バックアップ内のジョブデータが不正です。")
        elif category == "uploads":
            if len(relative.parts) != 2 or suffix not in {".pdf", ".txt", ".md"}:
                raise ValueError("バックアップ内の原文ファイル形式が不正です。")
        else:
            if len(relative.parts) != 3 or not re.fullmatch(r"[a-f0-9]{32}", relative.parts[1]):
                raise ValueError("バックアップ内の画像パスが不正です。")
            if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                raise ValueError("バックアップ内の画像形式が不正です。")
        members.append((info, relative))
    return members


def _restore_library_backup(stream: Any) -> dict[str, int]:
    try:
        with zipfile.ZipFile(stream) as bundle:
            members = _validated_backup_members(bundle)
            restored_files = 0
            restored_jobs = 0
            skipped_files = 0
            data_root = DATA_DIR.resolve()
            for info, relative in members:
                target = (DATA_DIR / relative).resolve()
                if data_root not in target.parents:
                    raise ValueError("バックアップの復元先が不正です。")
                if target.exists():
                    skipped_files += 1
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                created = False
                try:
                    with target.open("xb") as destination:
                        created = True
                        with bundle.open(info) as source:
                            shutil.copyfileobj(source, destination, length=1024 * 1024)
                except Exception:
                    if created:
                        target.unlink(missing_ok=True)
                    raise
                restored_files += 1
                if relative.parts[0] == "jobs":
                    restored_jobs += 1
            return {"restored_files": restored_files, "restored_jobs": restored_jobs, "skipped_files": skipped_files}
    except zipfile.BadZipFile as exc:
        raise ValueError("ZIPファイルが壊れているか、バックアップ形式ではありません。") from exc


def _document_is_referenced(document_id: str, *, excluding_job: str) -> bool:
    if any(job.get("document_id") == document_id for job_id, job in jobs.items() if job_id != excluding_job):
        return True
    for path in JOB_DIR.glob("*.json"):
        if path.stem == excluding_job:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            # An unreadable job might still point at the files; keep them.
            return True
        if isinstance(payload, dict) and payload.get("document_id") == document_id:
            return True
    return False


def _delete_library_job(job_id: str) -> dict[str, Any]:
    """Delete a saved translation, plus its source file and images when no other job uses them."""
    with state_lock:
        job = _load_saved_job(job_id)
        if not job:
            raise LookupError("ジョブが見つかりません。")
        if job.get("status") in {"queued", "running"}:
            raise ValueError("翻訳中の論文は削除できません。先に翻訳をキャンセルしてください。")
        if any(key[0] == job_id for key in active_retranslations):
            raise ValueError("再翻訳中の論文は削除できません。完了後にもう一度お試しください。")
        if job_id in active_reextractions:
            raise ValueError("再抽出中の論文は削除できません。完了後にもう一度お試しください。")
        (JOB_DIR / f"{job_id}.json").unlink(missing_ok=True)
        jobs.pop(job_id, None)
        removed_files = 1
        document_id = str(job.get("document_id", ""))
        source_removed = False
        if re.fullmatch(r"[a-f0-9]{32}", document_id) and not _document_is_referenced(document_id, excluding_job=job_id):
            documents.pop(document_id, None)
            for upload in UPLOAD_DIR.glob(f"{document_id}_*"):
                if upload.is_file():
                    upload.unlink(missing_ok=True)
                    removed_files += 1
                    source_removed = True
            asset_root = ASSET_DIR / document_id
            if asset_root.is_dir() and ASSET_DIR.resolve() in asset_root.resolve().parents:
                removed_files += sum(1 for item in asset_root.rglob("*") if item.is_file())
                shutil.rmtree(asset_root)
                source_removed = True
    return {"id": job_id, "removed_files": removed_files, "source_removed": source_removed}


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
        _set_job_summary(
            payload,
            payload.get("tags", []),
            payload.get("paper_summary", ""),
        )
        _normalize_library_metadata(payload)
    except (OSError, ValueError, TypeError):
        return None
    if payload.get("status") in {"queued", "running"}:
        payload["status"] = "failed"
        payload["message"] = "アプリが再起動したため翻訳が中断されました。もう一度翻訳を開始してください。"
        _save_job(payload)
    with state_lock:
        jobs[job_id] = payload
    return payload


def _set_segment_bookmark(job: dict[str, Any], index: int, bookmarked: bool) -> Segment:
    if type(bookmarked) is not bool:
        raise ValueError("ブックマークの値が不正です。")
    segments = job.get("segments", [])
    if index < 0 or index >= len(segments):
        raise IndexError("対訳位置が不正です。")
    segment = segments[index]
    if not isinstance(segment, Segment):
        raise TypeError("対訳データが不正です。")
    segment.bookmarked = bookmarked
    return segment


def _set_segment_clip(job: dict[str, Any], index: int, part: str, clipped: bool) -> Segment:
    if type(clipped) is not bool:
        raise ValueError("クリップの値が不正です。")
    if part not in CLIP_PARTS:
        raise ValueError("クリップ対象が不正です。")
    segments = job.get("segments", [])
    if index < 0 or index >= len(segments):
        raise IndexError("対訳位置が不正です。")
    segment = segments[index]
    if not isinstance(segment, Segment):
        raise TypeError("対訳データが不正です。")
    if clipped and part not in segment.clips:
        clipped_count = sum(len(item.clips) for item in segments if isinstance(item, Segment))
        if clipped_count >= MAX_CLIPS:
            raise ValueError(f"クリップは{MAX_CLIPS}件までです。先に1件外してください。")
    if clipped:
        segment.clips.append(part)
    else:
        segment.clips = [item for item in segment.clips if item != part]
    segment.clipped = bool(segment.clips)
    return segment


def _replace_segment_translation(job: dict[str, Any], index: int, translation: Any, origin: str) -> Segment:
    if not isinstance(translation, str):
        raise ValueError("訳文の形式が不正です。")
    translation = translation.strip()
    if not translation:
        raise ValueError("訳文を空にはできません。")
    if len(translation) > MAX_TRANSLATION_LENGTH:
        raise ValueError(f"訳文は{MAX_TRANSLATION_LENGTH:,}文字以内にしてください。")
    if origin not in {"manual", "model"}:
        raise ValueError("訳文の更新元が不正です。")
    segments = job.get("segments", [])
    if index < 0 or index >= len(segments):
        raise IndexError("対訳位置が不正です。")
    segment = segments[index]
    if not isinstance(segment, Segment):
        raise TypeError("対訳データが不正です。")
    if segment.translation == translation:
        return segment
    if segment.translation:
        segment.translation_revisions.append(
            {
                "translation": segment.translation,
                "changed_at": segment.translation_edited_at or job.get("updated_at", "") or _utc_now(),
                "origin": segment.translation_origin or "model",
            }
        )
        segment.translation_revisions = segment.translation_revisions[-MAX_TRANSLATION_REVISIONS:]
    segment.translation = translation
    segment.translation_edited_at = _utc_now()
    segment.translation_origin = origin
    segment.error = ""
    return segment


def _retranslate_segment(job_id: str, job: dict[str, Any], index: int) -> Segment:
    key = (job_id, index)
    with state_lock:
        if key in active_retranslations:
            raise ValueError("このセグメントは再翻訳中です。")
        if not str(job.get("status", "")).startswith("completed"):
            raise ValueError("翻訳完了後に再翻訳できます。")
        segments = job.get("segments", [])
        if index < 0 or index >= len(segments):
            raise IndexError("対訳位置が不正です。")
        segment = segments[index]
        if not isinstance(segment, Segment):
            raise TypeError("対訳データが不正です。")
        active_retranslations.add(key)
    try:
        if segment.kind == "equation" and segment.assets:
            translated = segment.source
        elif segment.label == "asset":
            translated = segment.translation or segment.source
        else:
            if not translator.health():
                llama_process.start()
            if not translator.health():
                raise RuntimeError(llama_process.error or "翻訳エンジンを起動できませんでした。")
            translated = translator.translate(
                segment.source,
                target_lang=job["target_lang"],
                source_lang=job["source_lang"],
                form=job["form"],
                context=neighboring_context(segments, index, job.get("context", "")),
                glossary=job.get("glossary", []),
            )
        with state_lock:
            updated = _replace_segment_translation(job, index, translated, "model")
            _save_job(job)
        return updated
    finally:
        with state_lock:
            active_retranslations.discard(key)


def _remove_unreferenced_document(document_id: str) -> None:
    """Delete a source file and its images once no saved paper points at them."""
    if not re.fullmatch(r"[a-f0-9]{32}", document_id) or _document_is_referenced(document_id, excluding_job=""):
        return
    documents.pop(document_id, None)
    for upload in UPLOAD_DIR.glob(f"{document_id}_*"):
        if upload.is_file():
            upload.unlink(missing_ok=True)
    asset_root = ASSET_DIR / document_id
    if asset_root.is_dir() and ASSET_DIR.resolve() in asset_root.resolve().parents:
        shutil.rmtree(asset_root, ignore_errors=True)


def _backup_job_before_reextract(job: dict[str, Any]) -> Path:
    REEXTRACT_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = REEXTRACT_BACKUP_DIR / f'{job["id"]}-{stamp}.json'
    payload = dict(job)
    payload["segments"] = [segment.__dict__ if isinstance(segment, Segment) else segment for segment in job["segments"]]
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for old in sorted(REEXTRACT_BACKUP_DIR.glob(f'{job["id"]}-*.json'))[:-MAX_REEXTRACT_BACKUPS]:
        old.unlink(missing_ok=True)
    return target


def _reextract_job(job_id: str) -> dict[str, Any]:
    """Extract a saved paper again with the current extractor, keeping the reader's work.

    The source PDF is copied under a new document id so the old images stay
    valid until the merged job is saved. Rows that are new or changed are
    translated in the background.
    """
    job = _load_saved_job(job_id)
    if not job:
        raise LookupError("ジョブが見つかりません。")
    with state_lock:
        if job.get("status") in {"queued", "running"}:
            raise ValueError("翻訳中の論文は再抽出できません。翻訳の完了かキャンセルを待ってください。")
        if any(key[0] == job_id for key in active_retranslations):
            raise ValueError("再翻訳中の論文は再抽出できません。完了後にもう一度お試しください。")
        if job_id in active_reextractions:
            raise ValueError("この論文はすでに再抽出中です。")
        old_document_id = str(job.get("document_id", ""))
        sources = (
            sorted(path for path in UPLOAD_DIR.glob(f"{old_document_id}_*") if path.is_file())
            if re.fullmatch(r"[a-f0-9]{32}", old_document_id)
            else []
        )
        if not sources:
            raise ValueError("元の論文ファイルが保存されていないため再抽出できません。")
        active_reextractions.add(job_id)
    try:
        source = sources[0]
        new_document_id = uuid.uuid4().hex
        target = UPLOAD_DIR / f"{new_document_id}_{source.name[len(old_document_id) + 1:]}"
        asset_root = ASSET_DIR / new_document_id
        try:
            shutil.copy2(source, target)
            extracted, _pages = extract_document(
                target,
                asset_dir=asset_root,
                asset_url_prefix=f"/api/assets/{new_document_id}",
            )
            fresh = segments_for_mode(extracted, str(job.get("segmentation_mode", "sentence")))
            if not fresh:
                raise ValueError("本文を抽出できませんでした。")
        except Exception:
            target.unlink(missing_ok=True)
            shutil.rmtree(asset_root, ignore_errors=True)
            raise
        with state_lock:
            _backup_job_before_reextract(job)
            old_count = len(job["segments"])
            merged, index_map, counts = merge_reextracted_segments(job["segments"], fresh)
            job["segments"] = merged
            job["document_id"] = new_document_id
            job["total"] = len(merged)
            job["paper_summary"] = remap_summary_row_references(str(job.get("paper_summary", "") or ""), index_map)
            reading_index = int(job.get("reading_index", 0) or 0)
            job["reading_index"] = index_map.get(reading_index, min(reading_index, len(merged) - 1))
            job["cancelled"] = False
            needs_translation = counts["needs_translation"] > 0
            if needs_translation:
                job["status"] = "queued"
                job["message"] = f"再抽出しました。新しい・変わった{counts['needs_translation']}行を翻訳します"
                job["completed"] = 0
                job["progress"] = 0
            else:
                failed = sum(bool(segment.error) for segment in merged)
                job["status"] = "completed" if failed == 0 else "completed_with_errors"
                job["message"] = "再抽出しました" if failed == 0 else f"再抽出しました（{failed}件のエラーあり）"
                job["completed"] = len(merged)
                job["progress"] = 100
            _save_job(job)
            _remove_unreferenced_document(old_document_id)
        if needs_translation:
            threading.Thread(target=_translate_job, args=(job_id, True), daemon=True).start()
        return {"id": job_id, "old_rows": old_count, "new_rows": len(merged), "translating": needs_translation, **counts}
    finally:
        with state_lock:
            active_reextractions.discard(job_id)


def _set_job_summary(job: dict[str, Any], tags: Any, summary: Any) -> tuple[list[str], str]:
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError("タグの形式が不正です。")
    if not isinstance(summary, str):
        raise ValueError("概要の形式が不正です。")
    normalized_tags: list[str] = []
    for raw_tag in tags:
        tag = re.sub(r"\s+", " ", raw_tag).strip()
        if not tag or tag in normalized_tags:
            continue
        if len(tag) > MAX_PAPER_TAG_LENGTH:
            raise ValueError(f"タグは1件{MAX_PAPER_TAG_LENGTH}文字以内にしてください。")
        normalized_tags.append(tag)
    if len(normalized_tags) > MAX_PAPER_TAGS:
        raise ValueError(f"タグは{MAX_PAPER_TAGS}件までです。")
    if len(summary) > MAX_PAPER_SUMMARY_LENGTH:
        raise ValueError(f"概要は{MAX_PAPER_SUMMARY_LENGTH:,}文字以内にしてください。")
    job["tags"] = normalized_tags
    job["paper_summary"] = summary
    return normalized_tags, summary


def _translation_quality_issues(job: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    glossary = job.get("glossary", [])
    target_lang = str(job.get("target_lang", ""))
    source_lang = str(job.get("source_lang", ""))
    for segment in job.get("segments", []):
        if not isinstance(segment, Segment) or segment.label == "asset":
            continue
        if segment.kind == "equation" and segment.assets:
            continue
        source = segment.source.strip()
        translation = segment.translation.strip()
        index = int(segment.index)
        if segment.error or not translation:
            issues.append(
                {
                    "index": index,
                    "type": "missing",
                    "severity": "error",
                    "message": "訳文がありません",
                    "detail": segment.error or "未翻訳のセグメントです。",
                }
            )
            continue
        if (
            target_lang != source_lang
            and segment.kind not in {"citation"}
            and source == translation
            and len(re.findall(r"[A-Za-z]{3,}", source)) >= 3
        ):
            issues.append(
                {
                    "index": index,
                    "type": "unchanged",
                    "severity": "warning",
                    "message": "原文と訳文が同一です",
                    "detail": "固有名詞だけの行でなければ、未翻訳の可能性があります。",
                }
            )
        number_pattern = r"(?<![\w])\d+(?:[.,]\d+)*(?:\s*[%％]|\s*°[CF])?"
        normalize_number = lambda value: re.sub(r"\s+", "", value).replace("％", "%")
        source_numbers = {normalize_number(value) for value in re.findall(number_pattern, source)}
        translation_numbers = {normalize_number(value) for value in re.findall(number_pattern, translation)}
        missing_numbers = sorted(source_numbers - translation_numbers)
        if missing_numbers:
            issues.append(
                {
                    "index": index,
                    "type": "number",
                    "severity": "warning",
                    "message": "数値・単位が訳文に見つかりません",
                    "detail": "、".join(missing_numbers[:8]) + (" ほか" if len(missing_numbers) > 8 else ""),
                }
            )
        if len(source) >= 80 and len(translation) < max(8, int(len(source) * 0.12)):
            issues.append(
                {
                    "index": index,
                    "type": "short",
                    "severity": "warning",
                    "message": "訳文が極端に短い可能性があります",
                    "detail": f"原文 {len(source)}文字 / 訳文 {len(translation)}文字",
                }
            )
        for term in glossary:
            source_term = str(term.get("source", "")).strip()
            target_term = str(term.get("target", "")).strip()
            if source_term and target_term and source_term.casefold() in source.casefold() and target_term not in translation:
                issues.append(
                    {
                        "index": index,
                        "type": "glossary",
                        "severity": "info",
                        "message": "用語集の訳語が見つかりません",
                        "detail": f"{source_term} → {target_term}",
                    }
                )
    return issues


def _export_summary_fields(job: dict[str, Any]) -> dict[str, Any]:
    """The paper notes included at the top of HTML and Markdown exports."""
    return {
        "summary": str(job.get("paper_summary", "") or ""),
        "tags": [str(tag) for tag in job.get("tags", []) if isinstance(tag, str)],
        "source_url": str(job.get("source_url", "") or ""),
        "reading_status": str(job.get("reading_status", "") or ""),
    }


def _translate_job(job_id: str, only_missing: bool = False) -> None:
    """Translate every row, or with ``only_missing`` just rows without a translation."""
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
        if only_missing and (segment.translation.strip() or segment.label == "asset"):
            pass
        elif segment.kind == "equation" and segment.assets:
            # Preserve the source equation exactly. The bitmap is authoritative;
            # extracted text remains available only for search and copying.
            segment.translation = segment.source
            segment.translation_origin = "source"
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
                segment.translation_origin = "model"
                segment.error = ""
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
    server_version = f"PLaMoPaperTranslator/{APP_VERSION}"

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
        if path == "/api/ping":
            self._json({"app": "plamo-paper-translator", "version": APP_VERSION, "pid": os.getpid()})
            return
        if path == "/api/status":
            model = _find_one(MODEL_DIR, "*.gguf")
            self._json(
                {
                    "version": APP_VERSION,
                    "engine": "ready" if translator.health() else "not_ready",
                    "engine_error": llama_process.error,
                    "model": model.name if model else None,
                    "gpu_recommended": "PLaMo 3 Translate 8B Q4_K_M / RTX 2000 Ada 16GB",
                }
            )
            return
        if path == "/api/library":
            parameters = urllib.parse.parse_qs(parsed.query)
            query = parameters.get("q", [""])[0].strip()[:300]
            reading_status = parameters.get("status", ["all"])[0]
            if reading_status not in READING_STATUSES | {"all"}:
                self._error("読書状態が不正です。")
                return
            items = _list_library(query, reading_status)
            self._json({"items": items, "count": len(items), "data_dir": str(DATA_DIR)})
            return
        if path == "/api/library/backup":
            with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as archive:
                with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
                    manifest = {
                        "format": "plamo-paper-translator-library",
                        "version": 1,
                        "created_at": _utc_now(),
                        "job_count": len(list(JOB_DIR.glob("*.json"))),
                        "contents": ["data/jobs", "data/uploads", "data/assets"],
                    }
                    bundle.writestr("backup-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                    bundle.writestr(
                        "RESTORE.txt",
                        "PLaMo Paper Translator library backup\n\n"
                        "アプリを終了し、ZIP内のdataフォルダーをアプリのdataフォルダーへ統合してください。\n"
                        "同名ファイルがある場合は、事前に現在のdataフォルダーもバックアップしてください。\n",
                    )
                    for root in (JOB_DIR, UPLOAD_DIR, ASSET_DIR):
                        if not root.exists():
                            continue
                        for source in root.rglob("*"):
                            if source.is_file():
                                bundle.write(source, Path("data") / source.relative_to(DATA_DIR))
                archive.seek(0, os.SEEK_END)
                size = archive.tell()
                archive.seek(0)
                filename = f"plamo-paper-library-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(size))
                self.end_headers()
                try:
                    shutil.copyfileobj(archive, self.wfile, length=1024 * 1024)
                except (BrokenPipeError, ConnectionResetError):
                    # A browser may cancel a large backup download after the
                    # response has started. The archive itself is still valid,
                    # so avoid printing an alarming server traceback.
                    pass
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]+)/quality", path)
        if match:
            job = _load_saved_job(match.group(1))
            if not job:
                self._error("ジョブが見つかりません。", 404)
                return
            issues = _translation_quality_issues(job)
            self._json(
                {
                    "issues": issues,
                    "count": len(issues),
                    "checked": len(job.get("segments", [])),
                    "errors": sum(issue["severity"] == "error" for issue in issues),
                    "warnings": sum(issue["severity"] == "warning" for issue in issues),
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
                content = bilingual_markdown(job["title"], job["segments"], **_export_summary_fields(job)).encode("utf-8")
                content_type, ext = "text/markdown; charset=utf-8", "md"
            elif fmt == "json":
                export_payload = document_to_dict(
                    job["title"],
                    job["segments"],
                    source_lang=job["source_lang"],
                    target_lang=job["target_lang"],
                    segmentation_mode=job.get("segmentation_mode", "sentence"),
                )
                export_payload["tags"] = job.get("tags", [])
                export_payload["summary"] = job.get("paper_summary", "")
                export_payload["source_url"] = job.get("source_url", "")
                export_payload["reading_status"] = job.get("reading_status", "unread")
                content = json.dumps(export_payload, ensure_ascii=False, indent=2).encode("utf-8")
                content_type, ext = "application/json; charset=utf-8", "json"
            else:
                content = bilingual_html(
                    job["title"],
                    job["segments"],
                    asset_resolver=_inline_asset,
                    static_dir=STATIC_DIR,
                    **_export_summary_fields(job),
                ).encode("utf-8")
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
        if parsed.path == "/api/control/shutdown":
            self._shutdown_for_restart()
            return
        if parsed.path == "/api/library/restore":
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BACKUP_UPLOAD_BYTES:
                self._error("バックアップZIPは2GB以下にしてください。")
                return
            try:
                with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as archive:
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("バックアップの受信が途中で終了しました。")
                        archive.write(chunk)
                        remaining -= len(chunk)
                    archive.seek(0)
                    result = _restore_library_backup(archive)
            except (OSError, ValueError) as exc:
                self._error(str(exc) or "バックアップを復元できません。")
                return
            self._json({"ok": True, **result}, HTTPStatus.CREATED)
            return
        if parsed.path == "/api/documents":
            self._upload_document()
            return
        if parsed.path == "/api/jobs":
            self._create_job()
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]+)/summary", parsed.path)
        if match:
            job = _load_saved_job(match.group(1))
            if not job:
                self._error("ジョブが見つかりません。", 404)
                return
            try:
                data = self._read_json()
                with state_lock:
                    tags, summary = _set_job_summary(job, data.get("tags", []), data.get("summary", ""))
                    metadata = _set_library_metadata(
                        job,
                        source_url=data.get("source_url") if "source_url" in data else None,
                        reading_status=data.get("reading_status") if "reading_status" in data else None,
                    )
                    _save_job(job)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                self._error(str(exc) or "論文のまとめを保存できません。")
                return
            self._json({"ok": True, "tags": tags, "summary": summary, **metadata})
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]+)/metadata", parsed.path)
        if match:
            job = _load_saved_job(match.group(1))
            if not job:
                self._error("ジョブが見つかりません。", 404)
                return
            try:
                data = self._read_json()
                with state_lock:
                    metadata = _set_library_metadata(
                        job,
                        source_url=data.get("source_url") if "source_url" in data else None,
                        reading_status=data.get("reading_status") if "reading_status" in data else None,
                        reading_index=data.get("reading_index") if "reading_index" in data else None,
                        mark_opened=bool(data.get("mark_opened", False)),
                    )
                    _save_job(job)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                self._error(str(exc) or "論文情報を保存できません。")
                return
            self._json({"ok": True, **metadata})
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
        match = re.fullmatch(r"/api/jobs/([a-f0-9]+)/translations", parsed.path)
        if match:
            job = _load_saved_job(match.group(1))
            if not job:
                self._error("ジョブが見つかりません。", 404)
                return
            try:
                data = self._read_json()
                index = int(data.get("index", -1))
                with state_lock:
                    if (match.group(1), index) in active_retranslations:
                        raise ValueError("このセグメントは再翻訳中です。完了後に編集してください。")
                    segment = _replace_segment_translation(job, index, data.get("translation"), "manual")
                    _save_job(job)
            except (IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self._error(str(exc) or "訳文を保存できません。")
                return
            self._json(
                {
                    "ok": True,
                    "index": index,
                    "translation": segment.translation,
                    "translation_origin": segment.translation_origin,
                    "translation_edited_at": segment.translation_edited_at,
                    "translation_revisions": segment.translation_revisions,
                }
            )
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]+)/retranslate", parsed.path)
        if match:
            job = _load_saved_job(match.group(1))
            if not job:
                self._error("ジョブが見つかりません。", 404)
                return
            try:
                data = self._read_json()
                index = int(data.get("index", -1))
                segment = _retranslate_segment(match.group(1), job, index)
            except (IndexError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self._error(str(exc) or "再翻訳できません。")
                return
            self._json(
                {
                    "ok": True,
                    "index": index,
                    "translation": segment.translation,
                    "translation_origin": segment.translation_origin,
                    "translation_edited_at": segment.translation_edited_at,
                    "translation_revisions": segment.translation_revisions,
                }
            )
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]+)/bookmarks", parsed.path)
        if match:
            job = _load_saved_job(match.group(1))
            if not job:
                self._error("ジョブが見つかりません。", 404)
                return
            try:
                data = self._read_json()
                index = int(data.get("index", -1))
                bookmarked = data.get("bookmarked")
                with state_lock:
                    segment = _set_segment_bookmark(job, index, bookmarked)
                    _save_job(job)
            except (IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self._error(str(exc) or "ブックマークを保存できません。")
                return
            self._json({"ok": True, "index": index, "bookmarked": segment.bookmarked})
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]+)/clips", parsed.path)
        if match:
            job = _load_saved_job(match.group(1))
            if not job:
                self._error("ジョブが見つかりません。", 404)
                return
            try:
                data = self._read_json()
                index = int(data.get("index", -1))
                part = data.get("part")
                clipped = data.get("clipped")
                with state_lock:
                    segment = _set_segment_clip(job, index, part, clipped)
                    _save_job(job)
                    clipped_count = sum(len(item.clips) for item in job["segments"])
            except (IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self._error(str(exc) or "クリップを保存できません。")
                return
            self._json(
                {
                    "ok": True,
                    "index": index,
                    "part": part,
                    "clipped": part in segment.clips,
                    "clips": segment.clips,
                    "count": clipped_count,
                    "max": MAX_CLIPS,
                }
            )
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/reextract", parsed.path)
        if match:
            # A JSON content type forces a CORS preflight, so other web pages
            # cannot start a re-extraction against this local server.
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
                self._error("Content-Type must be application/json", 415)
                return
            try:
                result = _reextract_job(match.group(1))
            except LookupError as exc:
                self._error(str(exc), 404)
                return
            except ValueError as exc:
                self._error(str(exc), 409)
                return
            except Exception as exc:
                self._error(f"再抽出できません: {exc}", 500)
                return
            self._json({"ok": True, **result})
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

    def _shutdown_for_restart(self) -> None:
        # Only the native launcher can read the per-start token from data/,
        # and the custom header forces a CORS preflight for web pages.
        supplied = self.headers.get(CONTROL_TOKEN_HEADER, "")
        if not control_token or not secrets.compare_digest(supplied, control_token):
            self._error("Forbidden", 403)
            return
        if _server_is_busy():
            self._error("翻訳中のため再起動しませんでした。", HTTPStatus.CONFLICT)
            return
        self._json({"ok": True, "stopping": True}, HTTPStatus.ACCEPTED)
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def do_DELETE(self) -> None:
        # DELETE is never a CORS "simple request", so other web pages cannot
        # trigger it against this local server without a failing preflight.
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})", urllib.parse.urlparse(self.path).path)
        if not match:
            self._error("Not found", 404)
            return
        try:
            result = _delete_library_job(match.group(1))
        except LookupError as exc:
            self._error(str(exc), 404)
            return
        except ValueError as exc:
            self._error(str(exc), 409)
            return
        except OSError as exc:
            self._error(f"論文を削除できません: {exc}", 500)
            return
        self._json({"ok": True, **result})

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
        digest = hashlib.sha256()
        with target.open("wb") as handle:
            remaining = length
            while remaining:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                handle.write(chunk)
                digest.update(chunk)
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
        file_sha256 = digest.hexdigest()
        duplicates = _duplicate_jobs(file_sha256)
        suggested_source_url = _suggest_source_url(filename, segments)
        document = {"id": document_id, "title": title, "filename": filename, "path": str(target), "pages": pages, "segments": segments, "file_sha256": file_sha256}
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
                "duplicates": duplicates,
                "suggested_source_url": suggested_source_url,
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
        created_at = _utc_now()
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
            "tags": [],
            "paper_summary": "",
            "source_url": str(data.get("source_url", "")).strip()[:2_000],
            "source_filename": document["filename"],
            "file_sha256": document.get("file_sha256", ""),
            "reading_status": "unread",
            "reading_index": 0,
            "last_opened_at": "",
            "created_at": created_at,
            "updated_at": created_at,
            "segments": selected_segments,
            "cancelled": False,
        }
        try:
            _set_library_metadata(job, source_url=job["source_url"])
        except ValueError as exc:
            self._error(str(exc))
            return
        with state_lock:
            jobs[job_id] = job
            _save_job(job)
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
    global control_token
    control_token = _write_control_file(args.port)
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
        _remove_control_file(control_token)
        llama_process.stop()


if __name__ == "__main__":
    main()
