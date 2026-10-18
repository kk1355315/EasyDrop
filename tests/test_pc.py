import io
import sqlite3
import urllib.error
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from app import main
from desktop.receiver import Receiver, safe_filename


@pytest.fixture
def pc_client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "LOGIN_PASSWORD", "test-password")
    monkeypatch.setattr(main, "SESSION_SECRET", "test-secret")
    monkeypatch.setattr(main, "PULL_TOKEN", "ipad-token")
    monkeypatch.setattr(main, "PC_PULL_TOKEN", "pc-token")
    monkeypatch.setattr(main, "PUBLIC_BASE_URL", "https://example.test/drop")
    monkeypatch.setattr(main, "FILES_DIR", tmp_path / "files")
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "drop.sqlite3")
    notifications = []
    monkeypatch.setattr(main, "send_trigger_email", lambda *args: notifications.append(args))
    with TestClient(main.app, base_url="https://example.test") as client:
        client.post("/drop/login", data={"password": "test-password"})
        yield client, notifications


IPAD = {"Authorization": "Bearer ipad-token"}
PC = {"Authorization": "Bearer pc-token"}


def test_targets_are_separate_and_ipad_api_stays_compatible(pc_client):
    client, notifications = pc_client
    assert client.post("/drop/upload", files={"files": ("ipad.txt", b"ipad")}).status_code == 200
    assert client.post("/drop/upload", data={"target": "pc"}, files={"files": ("pc.txt", b"pc")}).status_code == 200
    assert len(notifications) == 1
    ipad = client.get("/drop/api/pending", headers=IPAD).json()
    pc = client.get("/drop/api/pc/pending", headers=PC).json()
    assert [item["name"] for item in ipad] == ["ipad.txt"]
    assert [item["name"] for item in pc] == ["pc.txt"]
    assert pc[0]["download_url"] == f"https://example.test/drop/api/pc/file/{pc[0]['id']}"
    assert client.get("/drop/api/pc/pending", headers=IPAD).status_code == 401
    assert client.get("/drop/api/pending", headers=PC).status_code == 401
    for prefix, token, wrong_item in [("/drop/api", IPAD, pc[0]), ("/drop/api/pc", PC, ipad[0])]:
        assert client.get(f"{prefix}/file/{wrong_item['id']}", headers=token).status_code == 404
        assert client.post(f"{prefix}/ack/{wrong_item['id']}", headers=token).status_code == 404
    assert client.get(pc[0]["download_url"], headers=PC).content == b"pc"
    assert client.post(pc[0]["ack_url"], headers=PC).status_code == 200
    assert len(client.get("/drop/api/pending", headers=IPAD).json()) == 1


def test_pc_share_manifest_login_and_no_email(pc_client):
    client, notifications = pc_client
    manifest = client.get("/drop-pc/manifest.webmanifest").json()
    assert manifest["name"] == "发到电脑"
    assert manifest["id"] == manifest["scope"] == manifest["start_url"] == "/drop-pc/"
    assert manifest["share_target"]["action"] == "/drop-pc/share"
    page = client.get("/drop-pc/").text
    assert '<base href="/drop-pc/">' in page
    assert 'href="manifest.webmanifest"' in page
    assert 'data-target="pc"' in page
    response = client.post("/drop/pc/share", files={"files": ("photo.jpg", b"photo")})
    assert response.status_code == 200
    assert "等待电脑在线后自动保存" in response.text
    assert notifications == []
    assert len(client.get("/drop/api/pc/pending", headers=PC).json()) == 1
    client.cookies.clear()
    assert client.post("/drop/pc/share", files={"files": ("bad.txt", b"bad")}).status_code == 401
    assert 'action="login"' in client.get("/drop-pc/").text
    response = client.post("/drop/pc/login", data={"password": "test-password"}, follow_redirects=False)
    assert response.headers["location"] == "/drop-pc/"


def test_install_scopes_do_not_overlap_and_login_registers_workers(pc_client):
    client, _ = pc_client
    ipad = client.get("/drop/manifest.webmanifest").json()
    pc = client.get("/drop-pc/manifest.webmanifest").json()
    assert not pc["scope"].startswith(ipad["scope"])
    assert not ipad["scope"].startswith(pc["scope"])
    assert ipad["id"] == ipad["start_url"] == "/drop/"
    assert ipad["share_target"]["action"] == "/drop/share"
    assert client.get("/drop/pc/", follow_redirects=False).headers["location"] == "/drop-pc/"
    client.cookies.clear()
    for prefix in ("/drop", "/drop-pc"):
        page = client.get(prefix + "/").text
        assert 'src="/drop/static/install.js"' in page
        assert client.get(prefix + "/sw.js").headers["service-worker-allowed"] == prefix + "/"
        assert client.head(prefix + "/manifest.webmanifest").status_code == 200
    response = client.post("/drop-pc/login", data={"password": "test-password"}, follow_redirects=False)
    assert response.headers["location"] == "/drop-pc/"
    assert 'id="upload-form"' in client.get("/drop/").text
    assert 'id="upload-form"' in client.get("/drop-pc/").text


def test_shares_accept_text_urls_and_legacy_file_field_names(pc_client):
    client, notifications = pc_client
    assert client.post("/drop/share", data={"text": "https://example.test/hello"}).status_code == 200
    item = client.get("/drop/api/pending", headers=IPAD).json()[0]
    assert client.get(item["download_url"], headers=IPAD).content == b"https://example.test/hello"
    assert client.post("/drop/share", files={"file": ("old.jpg", b"photo", "image/jpeg")}).status_code == 200
    assert client.post("/drop-pc/share", files={"files": ("pc.jpg", b"photo", "image/jpeg")}).status_code == 200
    assert len(notifications) == 2
    assert client.post("/drop/share", data={"text": ""}).status_code == 400
    assert client.get("/drop/share", follow_redirects=False).status_code == 303


def test_existing_ipad_shortcut_query_token_still_works(pc_client):
    client, _ = pc_client
    client.post("/drop/upload", files={"files": ("ipad.txt", b"shortcut")})
    item = client.get("/drop/api/pending?token=ipad-token").json()[0]
    assert client.get(f"/drop/api/file/{item['id']}?token=ipad-token").content == b"shortcut"
    assert client.post(f"/drop/api/ack/{item['id']}?token=ipad-token").status_code == 200
    assert client.get("/drop/api/pc/pending?token=pc-token").status_code == 401


def test_unconfigured_pc_does_not_accept_files(pc_client, monkeypatch):
    client, _ = pc_client
    monkeypatch.setattr(main, "PC_PULL_TOKEN", "")
    assert client.post("/drop/upload", data={"target": "pc"}, files={"files": ("a", b"a")}).status_code == 503
    assert client.post("/drop/pc/share", files={"files": ("a", b"a")}).status_code == 503
    assert client.get("/drop/api/pc/pending", headers=PC).status_code == 401
    assert client.post("/drop/upload", data={"target": "other"}, files={"files": ("a", b"a")}).status_code == 422


def test_old_database_migrates_files_to_ipad(tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE files (id TEXT PRIMARY KEY, original_name TEXT, stored_name TEXT, mime TEXT, size INTEGER, sha256 TEXT, created_at INTEGER, acknowledged_at INTEGER)")
        db.execute("INSERT INTO files VALUES ('old', 'old.txt', 'old', 'text/plain', 3, 'hash', 1, NULL)")
    monkeypatch.setattr(main, "DB_PATH", path)
    monkeypatch.setattr(main, "FILES_DIR", tmp_path / "files")
    main.init_db()
    main.init_db()
    with main.db() as db:
        row = db.execute("SELECT * FROM files WHERE id='old'").fetchone()
        assert row["target"] == "ipad"
        assert row["original_name"] == "old.txt"


class TestOpener:
    __test__ = False

    def __init__(self, client, fail_ack=False, corrupt=False):
        self.client = client
        self.fail_ack = fail_ack
        self.corrupt = corrupt

    def open(self, request, timeout):
        path = urlsplit(request.full_url).path
        if self.fail_ack and "/ack/" in path:
            self.fail_ack = False
            raise OSError("temporary network failure")
        response = self.client.request(request.method, path, headers=dict(request.headers), content=request.data)
        if response.status_code >= 400:
            raise urllib.error.HTTPError(request.full_url, response.status_code, "error", {}, None)
        content = b"corrupt" if self.corrupt and "/file/" in path else response.content
        return io.BytesIO(content)


def receiver_config(tmp_path):
    return {"server_url": "https://example.test/drop", "token": "pc-token", "destination": str(tmp_path / "来自vivo")}


def test_receiver_full_flow_and_same_name_without_overwriting(pc_client, tmp_path):
    client, _ = pc_client
    destination = tmp_path / "来自vivo"
    destination.mkdir()
    (destination / "照片.txt").write_bytes(b"existing")
    for content in [b"one", b"two"]:
        client.post("/drop/pc/share", files={"files": ("照片.txt", content)})
    receiver = Receiver(receiver_config(tmp_path), tmp_path / "state", TestOpener(client))
    assert receiver.run_once()
    assert sorted(file.read_bytes() for file in destination.iterdir()) == [b"existing", b"one", b"two"]
    assert client.get("/drop/api/pc/pending", headers=PC).json() == []
    assert receiver.run_once()
    assert len(list(destination.iterdir())) == 3
    receiver.journal.close()


def test_ack_failure_and_restart_do_not_duplicate_download(pc_client, tmp_path):
    client, _ = pc_client
    client.post("/drop/pc/share", files={"files": ("vivo.txt", b"retry me")})
    receiver = Receiver(receiver_config(tmp_path), tmp_path / "state", TestOpener(client, fail_ack=True))
    assert not receiver.run_once()
    receiver.journal.close()
    assert len(client.get("/drop/api/pc/pending", headers=PC).json()) == 1
    receiver = Receiver(receiver_config(tmp_path), tmp_path / "state", TestOpener(client))
    assert receiver.run_once()
    assert [file.name for file in (tmp_path / "来自vivo").iterdir()] == ["vivo.txt"]
    assert client.get("/drop/api/pc/pending", headers=PC).json() == []
    receiver.journal.close()


def test_corrupt_download_keeps_server_copy(pc_client, tmp_path):
    client, _ = pc_client
    client.post("/drop/pc/share", files={"files": ("photo.jpg", b"original")})
    receiver = Receiver(receiver_config(tmp_path), tmp_path / "state", TestOpener(client, corrupt=True))
    assert not receiver.run_once()
    assert list((tmp_path / "来自vivo").iterdir()) == []
    assert len(client.get("/drop/api/pc/pending", headers=PC).json()) == 1
    receiver.journal.close()


@pytest.mark.parametrize("value,expected", [("../../vivo.txt", "vivo.txt"), ("C:\\folder\\照片.jpg", "照片.jpg"), ("CON.txt", "_CON.txt"), ("..", "file"), ("a?.txt. ", "a_.txt")])
def test_windows_filename_safety(value, expected):
    assert safe_filename(value) == expected
