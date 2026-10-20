"""Windows receiver. Uses only Python's standard library; never embeds credentials."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the receiver token to another location.


def safe_filename(name: str) -> str:
    name = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", name).strip().rstrip(". ")
    name = name[:160].rstrip(". ") or "file"
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]", name.split(".")[0], re.I):
        name = "_" + name
    return name


def file_matches(path: Path, size: int, sha256: str) -> bool:
    if not path.is_file() or path.stat().st_size != size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest() == sha256


class Receiver:
    def __init__(self, config: dict, state_dir: Path, opener=None, notifier=None, clipboard=None):
        url = str(config["server_url"]).rstrip("/")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("server_url 必须是完整 HTTPS 服务地址，包含 /drop")
        self.api = url + "/api/pc"
        self.token = config["token"]
        if not self.token:
            raise ValueError("缺少电脑接收令牌")
        self.destination = Path(config["destination"]).resolve()
        self.destination.mkdir(parents=True, exist_ok=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        self.journal = sqlite3.connect(state_dir / "received.sqlite3")
        self.journal.execute("CREATE TABLE IF NOT EXISTS received (id TEXT PRIMARY KEY, path TEXT NOT NULL)")
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.notifier = notifier
        self.clipboard = clipboard
        self.clipboard_copied = False
        self.newly_saved: list[Path] = []

    def request(self, path: str, method: str = "GET"):
        request = urllib.request.Request(
            self.api + path, method=method,
            headers={"Authorization": "Bearer " + self.token, "User-Agent": "EasyDrop-PC/1"},
            data=b"" if method == "POST" else None,
        )
        return self.opener.open(request, timeout=60)

    def acknowledge(self, item_id: str) -> None:
        try:
            with self.request("/ack/" + urllib.parse.quote(item_id, safe=""), "POST") as response:
                response.read()
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise

    def receive(self, item: dict) -> Path:
        item_id = str(item["id"])
        size = int(item["size"])
        sha256 = str(item["sha256"])
        if size < 0 or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("服务器返回的文件校验信息无效")
        row = self.journal.execute("SELECT path FROM received WHERE id = ?", (item_id,)).fetchone()
        previous = Path(row[0]) if row else None
        if previous and previous.parent == self.destination and file_matches(previous, size, sha256):
            self.acknowledge(item_id)
            return previous

        name = safe_filename(item["name"])
        candidate = self.destination / name
        serial = 0
        while candidate.exists():
            serial += 1
            candidate = self.destination / f"{Path(name).stem} ({serial}){Path(name).suffix}"

        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix=".easydrop-", suffix=".part", dir=self.destination, delete=False) as output:
                temporary = Path(output.name)
                digest = hashlib.sha256()
                received = 0
                with self.request("/file/" + urllib.parse.quote(item_id, safe="")) as source:
                    while chunk := source.read(1024 * 1024):
                        received += len(chunk)
                        if received > size:
                            raise ValueError("下载大小超出服务器记录")
                        digest.update(chunk)
                        output.write(chunk)
                if received != size or digest.hexdigest() != sha256:
                    raise ValueError("文件校验失败，保留服务器副本等待重试")
                output.flush()
                os.fsync(output.fileno())
            while True:
                # Persist the name BEFORE publishing, including across process crashes.
                with self.journal:
                    self.journal.execute("INSERT OR REPLACE INTO received VALUES (?, ?)", (item_id, str(candidate)))
                try:
                    os.link(temporary, candidate)  # Atomic and never overwrites an existing file (NTFS).
                    break
                except FileExistsError:
                    serial += 1
                    candidate = self.destination / f"{Path(name).stem} ({serial}){Path(name).suffix}"
            self.newly_saved.append(candidate)
            self.acknowledge(item_id)
            logging.info("已保存：%s", candidate.name)
            return candidate
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def run_once(self) -> bool:
        self.newly_saved = []
        self.clipboard_copied = False
        with self.request("/pending") as response:
            items = json.load(response)
        success = True
        for item in items:
            try:
                self.receive(item)
            except Exception:
                logging.exception("接收失败，稍后重试：%s", item.get("name", "file"))
                success = False
        if self.clipboard and self.newly_saved:
            try:
                self.clipboard(self.newly_saved)
                self.clipboard_copied = True
                logging.info("已复制到剪贴板：%s 个文件", len(self.newly_saved))
            except Exception:
                logging.exception("复制到剪贴板失败，文件已保存")
        if self.notifier and self.newly_saved:
            try:
                self.notifier(self.newly_saved)
            except Exception:
                logging.exception("接收通知失败，文件已保存")
        return success


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config_path = args.config.resolve()
    state_dir = config_path.parent
    log_handler = RotatingFileHandler(state_dir / "receiver.log", maxBytes=2 * 1024**2, backupCount=2, encoding="utf-8")
    logging.basicConfig(handlers=[log_handler], level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # One worker per Windows account, even if started manually and at logon together.
    lock = (state_dir / "receiver.lock").open("a+b")
    if os.name == "nt":
        import msvcrt
        lock.seek(0)
        lock.write(b"0")
        lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return 0
    with config_path.open(encoding="utf-8-sig") as source:
        config = json.load(source)
    receiver = Receiver(config, state_dir)
    if os.name == "nt":
        try:
            from .notifications import DesktopNotifier
            from .clipboard import copy_files
        except ImportError:
            from notifications import DesktopNotifier
            from clipboard import copy_files
        receiver.clipboard = copy_files
        notifier = DesktopNotifier(receiver.destination)
        receiver.notifier = lambda paths: notifier(paths, clipboard_copied=receiver.clipboard_copied)
    interval = max(2, int(config.get("poll_seconds", 5)))
    logging.info("电脑接收已启动")
    while True:
        try:
            success = receiver.run_once()
        except Exception:
            logging.exception("暂时无法接收，等待网络恢复")
            success = False
        if args.once:
            return 0 if success else 1
        time.sleep(interval if success else max(15, interval))


if __name__ == "__main__":
    raise SystemExit(main())
