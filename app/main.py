from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import smtplib
import sqlite3
import ssl
import time
import urllib.parse
from contextlib import asynccontextmanager
from email.message import EmailMessage
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


BASE_PATH = os.getenv("BASE_PATH", "/drop").rstrip("/")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
PULL_TOKEN = os.getenv("PULL_TOKEN", "")
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", str(2 * 1024**3)))
RETENTION_HOURS = int(os.getenv("RETENTION_HOURS", "48"))

DATA_DIR = Path("/data")
FILES_DIR = DATA_DIR / "files"
DB_PATH = DATA_DIR / "drop.sqlite3"
STATIC_DIR = Path(__file__).parent / "static"
COOKIE_NAME = "drop_session"


def required_settings() -> None:
    missing = [name for name, value in {
        "LOGIN_PASSWORD": LOGIN_PASSWORD,
        "SESSION_SECRET": SESSION_SECRET,
        "PULL_TOKEN": PULL_TOKEN,
        "PUBLIC_BASE_URL": PUBLIC_BASE_URL,
    }.items() if not value]
    if missing:
        raise RuntimeError(f"Missing required settings: {', '.join(missing)}")


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    with db() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                mime TEXT NOT NULL,
                size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                acknowledged_at INTEGER
            )
            """
        )


def session_value() -> str:
    timestamp = str(int(time.time()))
    signature = hmac.new(SESSION_SECRET.encode(), timestamp.encode(), hashlib.sha256).hexdigest()
    return f"{timestamp}.{signature}"


def valid_session(value: str | None) -> bool:
    if not value or "." not in value:
        return False
    timestamp, signature = value.split(".", 1)
    try:
        if int(timestamp) < int(time.time()) - 90 * 24 * 3600:
            return False
    except ValueError:
        return False
    expected = hmac.new(SESSION_SECRET.encode(), timestamp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def browser_auth(request: Request) -> None:
    if not valid_session(request.cookies.get(COOKIE_NAME)):
        raise HTTPException(status_code=401, detail="请先登录")


def api_auth(request: Request) -> None:
    authorization = request.headers.get("authorization", "")
    supplied = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
    if not supplied or not hmac.compare_digest(supplied, PULL_TOKEN):
        raise HTTPException(status_code=401, detail="invalid token")


def safe_name(name: str | None) -> str:
    candidate = Path((name or "file").replace("\\", "/")).name.strip()
    candidate = "".join(ch for ch in candidate if ch >= " " and ch not in '<>:"/\\|?*')
    return candidate[:180] or "file"


def cleanup_expired() -> None:
    cutoff = int(time.time()) - RETENTION_HOURS * 3600
    with db() as connection:
        rows = connection.execute(
            "SELECT id, stored_name FROM files WHERE created_at < ? OR acknowledged_at IS NOT NULL", (cutoff,)
        ).fetchall()
        for row in rows:
            (FILES_DIR / row["stored_name"]).unlink(missing_ok=True)
            connection.execute("DELETE FROM files WHERE id = ?", (row["id"],))


def send_trigger_email(count: int, names: list[str]) -> None:
    host = os.getenv("SMTP_HOST", "")
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    recipient = os.getenv("TRIGGER_TO", "")
    if not all((host, user, password, recipient)):
        return

    message = EmailMessage()
    message["From"] = os.getenv("SMTP_FROM", user)
    message["To"] = recipient
    message["Subject"] = os.getenv("TRIGGER_SUBJECT", "DROP_READY")
    shown = ", ".join(names[:5])
    message.set_content(
        f"服务器已收到 {count} 个文件。\n\n{shown}\n\n"
        "这是一封用于唤醒 iPad 快捷指令的自动通知邮件。"
    )
    port = int(os.getenv("SMTP_PORT", "465"))
    use_ssl = os.getenv("SMTP_SSL", "true").lower() in {"1", "true", "yes"}
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context()) as smtp:
            smtp.login(user, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(user, password)
            smtp.send_message(message)


@asynccontextmanager
async def lifespan(_: FastAPI):
    required_settings()
    init_db()
    cleanup_expired()
    yield


app = FastAPI(title="发到 iPad", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount(f"{BASE_PATH}/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get(BASE_PATH, response_class=HTMLResponse)
@app.get(f"{BASE_PATH}/", response_class=HTMLResponse)
def home(request: Request):
    if not valid_session(request.cookies.get(COOKIE_NAME)):
        return FileResponse(STATIC_DIR / "login.html")
    return FileResponse(STATIC_DIR / "index.html")


@app.get(f"{BASE_PATH}/manifest.webmanifest")
def manifest() -> JSONResponse:
    payload = {
        "name": "发到 iPad",
        "short_name": "发到 iPad",
        "description": "把安卓里的照片和文件发到 iPad",
        "id": f"{BASE_PATH}/",
        "start_url": f"{BASE_PATH}/",
        "scope": f"{BASE_PATH}/",
        "display": "standalone",
        "background_color": "#f7f8fc",
        "theme_color": "#2457e6",
        "icons": [
            {"src": f"{BASE_PATH}/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": f"{BASE_PATH}/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
        "share_target": {
            "action": f"{BASE_PATH}/share",
            "method": "POST",
            "enctype": "multipart/form-data",
            "params": {
                "title": "title",
                "text": "text",
                "url": "url",
                "files": [{"name": "files", "accept": ["*/*"]}],
            },
        },
    }
    return JSONResponse(payload, media_type="application/manifest+json")


@app.get(f"{BASE_PATH}/sw.js")
def service_worker() -> FileResponse:
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript", headers={"Service-Worker-Allowed": f"{BASE_PATH}/"})


@app.post(f"{BASE_PATH}/login")
def login(password: Annotated[str, Form()]):
    if not hmac.compare_digest(password, LOGIN_PASSWORD):
        return RedirectResponse(f"{BASE_PATH}/?error=1", status_code=303)
    response = RedirectResponse(f"{BASE_PATH}/", status_code=303)
    response.set_cookie(COOKIE_NAME, session_value(), max_age=90 * 24 * 3600, httponly=True, secure=True, samesite="lax", path=f"{BASE_PATH}/")
    return response


@app.post(f"{BASE_PATH}/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse(f"{BASE_PATH}/", status_code=303)
    response.delete_cookie(COOKIE_NAME, path=f"{BASE_PATH}/")
    return response


async def store_files(files: list[UploadFile]) -> list[dict]:
    if not files:
        raise HTTPException(status_code=400, detail="没有收到文件")
    results: list[dict] = []
    created_paths: list[Path] = []
    db_rows: list[tuple] = []
    try:
        for upload in files:
            item_id = secrets.token_urlsafe(16)
            original_name = safe_name(upload.filename)
            stored_name = item_id
            destination = FILES_DIR / stored_name
            digest = hashlib.sha256()
            size = 0
            with destination.open("xb") as output:
                created_paths.append(destination)
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise HTTPException(status_code=413, detail=f"{original_name} 超过大小限制")
                    digest.update(chunk)
                    output.write(chunk)
            mime = upload.content_type or mimetypes.guess_type(original_name)[0] or "application/octet-stream"
            now = int(time.time())
            db_rows.append((item_id, original_name, stored_name, mime, size, digest.hexdigest(), now))
            results.append({"id": item_id, "name": original_name, "mime": mime, "size": size})
        with db() as connection:
            connection.executemany(
                "INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                db_rows,
            )
        return results
    except Exception:
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise
    finally:
        for upload in files:
            await upload.close()


@app.post(f"{BASE_PATH}/share", response_class=HTMLResponse)
async def share_upload(request: Request, background_tasks: BackgroundTasks, files: Annotated[list[UploadFile], File()]):
    browser_auth(request)
    cleanup_expired()
    stored = await store_files(files)
    background_tasks.add_task(send_trigger_email, len(stored), [item["name"] for item in stored])
    names_json = json.dumps([item["name"] for item in stored], ensure_ascii=False)
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
        "<title>上传完成</title><style>body{font-family:system-ui;margin:0;display:grid;place-items:center;min-height:100vh;background:#f7f8fc}"
        ".box{text-align:center;background:#fff;padding:36px;border-radius:24px;box-shadow:0 12px 36px #20306018}.ok{font-size:64px;color:#17a765}</style>"
        f"<div class='box'><div class='ok'>✓</div><h1>已发到 iPad</h1><p>{len(stored)} 个文件已上传</p><a href='{BASE_PATH}/'>返回</a></div>"
        f"<script>console.log({names_json});setTimeout(()=>history.length>1&&history.back(),2500)</script>"
    )


@app.post(f"{BASE_PATH}/upload")
async def browser_upload(request: Request, background_tasks: BackgroundTasks, files: Annotated[list[UploadFile], File()]):
    browser_auth(request)
    cleanup_expired()
    stored = await store_files(files)
    background_tasks.add_task(send_trigger_email, len(stored), [item["name"] for item in stored])
    return {"ok": True, "files": stored}


@app.get(f"{BASE_PATH}/api/pending")
def pending(request: Request):
    api_auth(request)
    cleanup_expired()
    with db() as connection:
        rows = connection.execute(
            "SELECT id, original_name, mime, size, sha256, created_at FROM files WHERE acknowledged_at IS NULL ORDER BY created_at"
        ).fetchall()
    return [{
        "id": row["id"],
        "name": row["original_name"],
        "mime": row["mime"],
        "size": row["size"],
        "sha256": row["sha256"],
        "created_at": row["created_at"],
        "download_url": f"{PUBLIC_BASE_URL}/api/file/{urllib.parse.quote(row['id'])}",
        "ack_url": f"{PUBLIC_BASE_URL}/api/ack/{urllib.parse.quote(row['id'])}",
    } for row in rows]


def get_row(item_id: str) -> sqlite3.Row:
    with db() as connection:
        row = connection.execute("SELECT * FROM files WHERE id = ? AND acknowledged_at IS NULL", (item_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="file not found")
    return row


@app.get(f"{BASE_PATH}/api/file/{{item_id}}")
def download(item_id: str, request: Request):
    api_auth(request)
    row = get_row(item_id)
    path = FILES_DIR / row["stored_name"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file missing")
    encoded = urllib.parse.quote(row["original_name"])
    return FileResponse(
        path,
        media_type=row["mime"],
        filename=row["original_name"],
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post(f"{BASE_PATH}/api/ack/{{item_id}}")
def acknowledge(item_id: str, request: Request):
    api_auth(request)
    row = get_row(item_id)
    (FILES_DIR / row["stored_name"]).unlink(missing_ok=True)
    with db() as connection:
        connection.execute("DELETE FROM files WHERE id = ?", (item_id,))
    return {"ok": True, "id": item_id}
