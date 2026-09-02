from pathlib import Path

from fastapi.testclient import TestClient

from app import main


def test_complete_file_lifecycle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main, "LOGIN_PASSWORD", "test-login-password")
    monkeypatch.setattr(main, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(main, "PULL_TOKEN", "test-pull-token")
    monkeypatch.setattr(main, "PUBLIC_BASE_URL", "https://drop.example.test/drop")
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "FILES_DIR", tmp_path / "files")
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "drop.sqlite3")

    with TestClient(main.app, base_url="https://drop.example.test") as client:
        manifest = client.get("/drop/manifest.webmanifest")
        assert manifest.status_code == 200
        assert manifest.json()["share_target"]["action"] == "/drop/share"

        assert client.get("/drop/api/pending").status_code == 401

        login = client.post(
            "/drop/login",
            data={"password": "test-login-password"},
            follow_redirects=False,
        )
        assert login.status_code == 303

        uploaded = client.post(
            "/drop/upload",
            files={"files": ("hello.txt", b"hello iPad", "text/plain")},
        )
        assert uploaded.status_code == 200

        headers = {"Authorization": "Bearer test-pull-token"}
        pending = client.get("/drop/api/pending", headers=headers)
        assert pending.status_code == 200
        assert len(pending.json()) == 1
        item = pending.json()[0]
        assert item["name"] == "hello.txt"
        assert item["download_url"].startswith("https://drop.example.test/drop/api/file/")
        assert item["ack_url"].startswith("https://drop.example.test/drop/api/ack/")

        downloaded = client.get(f"/drop/api/file/{item['id']}", headers=headers)
        assert downloaded.status_code == 200
        assert downloaded.content == b"hello iPad"

        acknowledged = client.post(f"/drop/api/ack/{item['id']}", headers=headers)
        assert acknowledged.status_code == 200
        assert client.get("/drop/api/pending", headers=headers).json() == []


def test_safe_name_removes_paths_and_reserved_characters() -> None:
    assert main.safe_name("../../folder/hello?.txt") == "hello.txt"
    assert main.safe_name("..\\folder\\photo.jpg") == "photo.jpg"


def test_failed_batch_leaves_no_files_or_queue_rows(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main, "LOGIN_PASSWORD", "test-login-password")
    monkeypatch.setattr(main, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(main, "PULL_TOKEN", "test-pull-token")
    monkeypatch.setattr(main, "PUBLIC_BASE_URL", "https://drop.example.test/drop")
    monkeypatch.setattr(main, "MAX_FILE_BYTES", 5)
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "FILES_DIR", tmp_path / "files")
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "drop.sqlite3")

    with TestClient(main.app, base_url="https://drop.example.test") as client:
        client.post("/drop/login", data={"password": "test-login-password"})
        response = client.post(
            "/drop/upload",
            files=[
                ("files", ("small.txt", b"1234", "text/plain")),
                ("files", ("large.txt", b"123456", "text/plain")),
            ],
        )
        assert response.status_code == 413

        headers = {"Authorization": "Bearer test-pull-token"}
        assert client.get("/drop/api/pending", headers=headers).json() == []
        assert list((tmp_path / "files").iterdir()) == []
