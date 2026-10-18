from __future__ import annotations

import hashlib
import hmac
import io
import logging
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
from typing import Annotated, Literal

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import UploadFile as StarletteUploadFile


BASE_PATH = os.getenv("BASE_PATH", "/drop").rstrip("/")
PC_BASE_PATH = f"{BASE_PATH}-pc"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
PULL_TOKEN = os.getenv("PULL_TOKEN", "")
PC_PULL_TOKEN = os.getenv("PC_PULL_TOKEN", "")
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
    if PC_PULL_TOKEN and hmac.compare_digest(PC_PULL_TOKEN, PULL_TOKEN):
        raise RuntimeError("PC_PULL_TOKEN must be different from the iPad PULL_TOKEN")


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
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(files)")}
        if "target" not in columns:
            connection.execute("ALTER TABLE files ADD COLUMN target TEXT NOT NULL DEFAULT 'ipad'")


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


def api_auth(request: Request, target: str = "ipad") -> None:
    authorization = request.headers.get("authorization", "")
    supplied = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
    # Existing signed iPad shortcuts may pass their token in the query string.
    if target == "ipad" and not supplied:
        supplied = request.query_params.get("token", "")
    expected = PC_PULL_TOKEN if target == "pc" else PULL_TOKEN
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid token")


def api_target(request: Request) -> str:
    return "pc" if request.url.path.startswith(f"{BASE_PATH}/api/pc/") else "ipad"


def check_target(target: str) -> None:
    if target == "pc" and not PC_PULL_TOKEN:
        raise HTTPException(status_code=503, detail="电脑接收尚未配置，请先设置 PC_PULL_TOKEN 并启动电脑接收程序")


def page_target(request: Request) -> str:
    return "pc" if request.url.path.startswith((f"{PC_BASE_PATH}/", f"{BASE_PATH}/pc/")) else "ipad"


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
    logging.getLogger("uvicorn.error").info("iPad trigger email sent (%s files)", count)


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
@app.get(f"{PC_BASE_PATH}/", response_class=HTMLResponse)
@app.head(f"{BASE_PATH}/")
@app.head(f"{PC_BASE_PATH}/")
def home(request: Request):
    page = "index.html" if valid_session(request.cookies.get(COOKIE_NAME)) else "login.html"
    target = page_target(request)
    prefix = PC_BASE_PATH if target == "pc" else BASE_PATH
    content = (STATIC_DIR / page).read_text(encoding="utf-8")
    content = content.replace("<head>", f'<head><base href="{prefix}/">', 1)
    content = content.replace('data-target="ipad"', f'data-target="{target}"')
    if target == "pc":
        content = content.replace("发到 iPad", "发到电脑")
    content = content.replace('href="static/', f'href="{BASE_PATH}/static/').replace('src="static/', f'src="{BASE_PATH}/static/')
    return HTMLResponse(content, headers={"Cache-Control": "no-store"})


@app.get(f"{BASE_PATH}/pc")
@app.get(f"{BASE_PATH}/pc/")
@app.get(PC_BASE_PATH)
def pc_home_redirect():
    return RedirectResponse(f"{PC_BASE_PATH}/", status_code=307)


@app.get(f"{BASE_PATH}/manifest.webmanifest")
@app.get(f"{BASE_PATH}/pc/manifest.webmanifest")
@app.get(f"{PC_BASE_PATH}/manifest.webmanifest")
@app.head(f"{BASE_PATH}/manifest.webmanifest")
@app.head(f"{PC_BASE_PATH}/manifest.webmanifest")
def manifest(request: Request) -> JSONResponse:
    pc = page_target(request) == "pc"
    prefix = PC_BASE_PATH if pc else BASE_PATH
    payload = {
        "name": "发到电脑" if pc else "发到 iPad",
        "short_name": "发到电脑" if pc else "发到 iPad",
        "description": "把安卓里的照片和文件发到电脑" if pc else "把安卓里的照片和文件发到 iPad",
        "id": f"{prefix}/",
        "start_url": f"{prefix}/",
        "scope": f"{prefix}/",
        "display": "standalone",
        "background_color": "#f7f8fc",
        "theme_color": "#2457e6",
        "icons": [
            {"src": f"{BASE_PATH}/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": f"{BASE_PATH}/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
        "share_target": {
            "action": f"{prefix}/share",
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
@app.get(f"{PC_BASE_PATH}/sw.js")
@app.head(f"{BASE_PATH}/sw.js")
@app.head(f"{PC_BASE_PATH}/sw.js")
def service_worker(request: Request) -> FileResponse:
    prefix = PC_BASE_PATH if page_target(request) == "pc" else BASE_PATH
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript", headers={"Service-Worker-Allowed": f"{prefix}/", "Cache-Control": "no-cache"})


@app.post(f"{BASE_PATH}/login")
@app.post(f"{BASE_PATH}/pc/login")
@app.post(f"{PC_BASE_PATH}/login")
def login(request: Request, password: Annotated[str, Form()]):
    prefix = PC_BASE_PATH if page_target(request) == "pc" else BASE_PATH
    if not hmac.compare_digest(password, LOGIN_PASSWORD):
        return RedirectResponse(f"{prefix}/?error=1", status_code=303)
    response = RedirectResponse(f"{prefix}/", status_code=303)
    for cookie_path in (f"{BASE_PATH}/", f"{PC_BASE_PATH}/"):
        response.set_cookie(COOKIE_NAME, session_value(), max_age=90 * 24 * 3600, httponly=True, secure=True, samesite="lax", path=cookie_path)
    return response


@app.post(f"{BASE_PATH}/logout")
@app.post(f"{PC_BASE_PATH}/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse(f"{BASE_PATH}/", status_code=303)
    response.delete_cookie(COOKIE_NAME, path=f"{BASE_PATH}/")
    response.delete_cookie(COOKIE_NAME, path=f"{PC_BASE_PATH}/")
    return response


async def store_files(files: list[UploadFile], target: str = "ipad") -> list[dict]:
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
            db_rows.append((item_id, original_name, stored_name, mime, size, digest.hexdigest(), now, target))
            results.append({"id": item_id, "name": original_name, "mime": mime, "size": size})
        with db() as connection:
            connection.executemany(
                "INSERT INTO files (id, original_name, stored_name, mime, size, sha256, created_at, target) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
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
@app.post(f"{BASE_PATH}/pc/share", response_class=HTMLResponse)
@app.post(f"{PC_BASE_PATH}/share", response_class=HTMLResponse)
async def share_upload(request: Request, background_tasks: BackgroundTasks):
    target = page_target(request)
    browser_auth(request)
    check_target(target)
    form = await request.form(max_part_size=MAX_FILE_BYTES)
    files = [value for _, value in form.multi_items() if isinstance(value, StarletteUploadFile)]
    if not files:
        text = "\n\n".join(str(form[key]).strip() for key in ("title", "text", "url") if key in form and str(form[key]).strip())
        if text:
            files = [UploadFile(filename="分享内容.txt", file=io.BytesIO(text.encode("utf-8")))]
        else:
            return HTMLResponse("<meta charset='utf-8'><p>这次分享没有附带文件或文字，请从相册或文件管理器重新分享。</p>", status_code=400)
    cleanup_expired()
    stored = await store_files(files, target)
    if target == "ipad":
        background_tasks.add_task(send_trigger_email, len(stored), [item["name"] for item in stored])
    label = "电脑" if target == "pc" else "iPad"
    destination = "等待电脑在线后自动保存" if target == "pc" else "等待 iPad 接收"
    prefix = PC_BASE_PATH if target == "pc" else BASE_PATH
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
        "<title>上传完成</title><style>body{font-family:system-ui;margin:0;display:grid;place-items:center;min-height:100vh;background:#f7f8fc}"
        ".box{text-align:center;background:#fff;padding:36px;border-radius:24px;box-shadow:0 12px 36px #20306018}.ok{font-size:64px;color:#17a765}</style>"
        f"<div class='box'><div class='ok'>✓</div><h1>已上传，发往{label}</h1><p>{len(stored)} 个文件，{destination}</p><a href='{prefix}/'>返回</a></div>"
    )


@app.get(f"{BASE_PATH}/share", response_class=HTMLResponse)
@app.get(f"{BASE_PATH}/pc/share", response_class=HTMLResponse)
@app.get(f"{PC_BASE_PATH}/share", response_class=HTMLResponse)
def share_landing(request: Request):
    prefix = PC_BASE_PATH if page_target(request) == "pc" else BASE_PATH
    return RedirectResponse(f"{prefix}/", status_code=303)


@app.post(f"{BASE_PATH}/upload")
@app.post(f"{PC_BASE_PATH}/upload")
async def browser_upload(request: Request, background_tasks: BackgroundTasks, files: Annotated[list[UploadFile], File()], target: Annotated[Literal["ipad", "pc"], Form()] = "ipad"):
    browser_auth(request)
    check_target(target)
    cleanup_expired()
    stored = await store_files(files, target)
    if target == "ipad":
        background_tasks.add_task(send_trigger_email, len(stored), [item["name"] for item in stored])
    return {"ok": True, "files": stored, "target": target}


@app.get(f"{BASE_PATH}/api/pending")
@app.get(f"{BASE_PATH}/api/pc/pending")
def pending(request: Request):
    target = api_target(request)
    api_auth(request, target)
    cleanup_expired()
    with db() as connection:
        rows = connection.execute(
            "SELECT id, original_name, mime, size, sha256, created_at FROM files WHERE acknowledged_at IS NULL AND target = ? ORDER BY created_at, id",
            (target,),
        ).fetchall()
    api_prefix = f"{PUBLIC_BASE_URL}/api/pc" if target == "pc" else f"{PUBLIC_BASE_URL}/api"
    return [{
        "id": row["id"],
        "name": row["original_name"],
        "mime": row["mime"],
        "size": row["size"],
        "sha256": row["sha256"],
        "created_at": row["created_at"],
        "download_url": f"{api_prefix}/file/{urllib.parse.quote(row['id'])}",
        "ack_url": f"{api_prefix}/ack/{urllib.parse.quote(row['id'])}",
    } for row in rows]


def get_row(item_id: str, target: str = "ipad") -> sqlite3.Row:
    with db() as connection:
        row = connection.execute("SELECT * FROM files WHERE id = ? AND acknowledged_at IS NULL AND target = ?", (item_id, target)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="file not found")
    return row


@app.get(f"{BASE_PATH}/api/file/{{item_id}}")
@app.get(f"{BASE_PATH}/api/pc/file/{{item_id}}")
def download(item_id: str, request: Request):
    target = api_target(request)
    api_auth(request, target)
    row = get_row(item_id, target)
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
@app.post(f"{BASE_PATH}/api/pc/ack/{{item_id}}")
def acknowledge(item_id: str, request: Request):
    target = api_target(request)
    api_auth(request, target)
    row = get_row(item_id, target)
    (FILES_DIR / row["stored_name"]).unlink(missing_ok=True)
    with db() as connection:
        connection.execute("DELETE FROM files WHERE id = ?", (item_id,))
    return {"ok": True, "id": item_id}
