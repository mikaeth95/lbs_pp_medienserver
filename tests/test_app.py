import io
from pathlib import Path

import app as app_module
from app import get_db
from conftest import csrf_from


def test_catalog_upload_and_management_are_public(client):
    assert client.get("/").status_code == 200
    assert client.get("/upload").status_code == 200
    assert client.get("/manage").status_code == 200

    old_admin_url = client.get("/admin")
    assert old_admin_url.status_code == 302
    assert old_admin_url.headers["Location"].endswith("/manage")


def test_csrf_protects_video_changes(client):
    response = client.post("/manage/videos/1/delete", data={})
    assert response.status_code == 400
    assert "CSRF" in response.text


def test_upload_limit_returns_413(client):
    token = csrf_from(client.get("/upload"))
    response = client.post(
        "/upload",
        data={
            "csrf_token": token,
            "title": "Zu groß",
            "topic": "Test",
            "language": "DE",
            "year": "2025",
            "duration": "10",
            "video_file": (io.BytesIO(b"x" * (1024 * 1024 + 1)), "gross.mp4"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 413
    assert "Limit von 1 MB" in response.text


def test_media_route_blocks_path_traversal(client, tmp_path):
    outside = tmp_path / "geheim.txt"
    outside.write_text("nicht ausliefern", encoding="utf-8")
    response = client.get("/media/videos/../geheim.txt")
    assert response.status_code == 404


def test_upload_saves_secure_files_and_metadata(app, upload_video):
    response = upload_video(filename="../../Mein Kurs.mp4")
    assert response.status_code == 200
    assert "Video wurde hinzugefügt" in response.text

    with app.app_context():
        video = get_db().execute("SELECT * FROM videos").fetchone()
        assert video["title"] == "Excel Grundlagen"
        assert video["duration_seconds"] == 90
        assert video["language"] == "DE"
        assert "/" not in video["video_path"]
        assert "\\" not in video["video_path"]
        assert Path(app.config["VIDEO_DIR"], video["video_path"]).is_file()
        assert Path(app.config["THUMBNAIL_DIR"], video["thumbnail_path"]).is_file()


def test_missing_ffprobe_requires_manual_duration(client):
    token = csrf_from(client.get("/upload"))
    response = client.post(
        "/upload",
        data={
            "csrf_token": token,
            "title": "Ohne Dauer",
            "topic": "Test",
            "language": "DE",
            "year": "2025",
            "duration": "",
            "video_file": (io.BytesIO(b"not-a-real-video"), "test.mp4"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert "manuell eingeben" in response.text


def test_duration_and_thumbnail_are_generated_automatically(app, client, monkeypatch):
    monkeypatch.setattr(app_module, "probe_duration", lambda _path: 73)

    def fake_thumbnail(_video_path, directory):
        filename = "automatisch.jpg"
        Path(directory, filename).write_bytes(b"generated-thumbnail")
        return filename

    monkeypatch.setattr(app_module, "generate_thumbnail", fake_thumbnail)
    token = csrf_from(client.get("/upload"))
    response = client.post(
        "/upload",
        data={
            "csrf_token": token,
            "title": "Automatischer Import",
            "description": "",
            "topic": "Test",
            "language": "DE",
            "year": "2026",
            "duration": "",
            "video_file": (io.BytesIO(b"fake-mp4"), "automatisch.mp4"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        video = get_db().execute("SELECT * FROM videos WHERE title = ?", ("Automatischer Import",)).fetchone()
        assert video["duration_seconds"] == 73
        assert video["thumbnail_path"] == "automatisch.jpg"
        assert Path(app.config["THUMBNAIL_DIR"], video["thumbnail_path"]).is_file()


def test_combined_search_topic_and_language_filters(app, upload_video):
    assert upload_video(title="Excel Deutsch", topic="Office", language="DE").status_code == 200
    assert upload_video(title="Excel English", topic="Office", language="EN").status_code == 200
    assert upload_video(title="Python Deutsch", topic="Programmierung", language="DE").status_code == 200

    response = app.test_client().get("/?q=Excel&topic=Office&language=DE")
    assert response.status_code == 200
    assert "Excel Deutsch" in response.text
    assert "Excel English" not in response.text
    assert "Python Deutsch" not in response.text


def test_range_request_returns_partial_video(app, upload_video):
    upload_video()
    with app.app_context():
        video = get_db().execute("SELECT * FROM videos LIMIT 1").fetchone()
        video_path = video["video_path"]

    response = app.test_client().get(
        f"/media/videos/{video_path}", headers={"Range": "bytes=5-12"}
    )
    assert response.status_code == 206
    assert response.data == b"0123456789abcdefghijklmnopqrstuvwxyz"[5:13]
    assert response.headers["Content-Range"].startswith("bytes 5-12/")
    assert response.headers["Accept-Ranges"] == "bytes"


def test_edit_and_delete_update_database_and_files(app, client, upload_video):
    upload_video()
    with app.app_context():
        video = get_db().execute("SELECT * FROM videos LIMIT 1").fetchone()
        video_id = video["id"]
        stored_path = Path(app.config["VIDEO_DIR"], video["video_path"])

    edit_page = client.get(f"/manage/videos/{video_id}/edit")
    token = csrf_from(edit_page)
    edited = client.post(
        f"/manage/videos/{video_id}/edit",
        data={
            "csrf_token": token,
            "title": " Excel für Fortgeschrittene ",
            "description": "Aktualisierte Beschreibung",
            "topic": "Office",
            "language": "EN",
            "year": "2026",
            "duration": "02:15",
        },
        follow_redirects=True,
    )
    assert edited.status_code == 200
    with app.app_context():
        updated = get_db().execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        assert updated["title"] == "Excel für Fortgeschrittene"
        assert updated["language"] == "EN"
        assert updated["duration_seconds"] == 135

    token = csrf_from(client.get("/manage"))
    response = client.post(
        f"/manage/videos/{video_id}/delete",
        data={"csrf_token": token},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "wurden gelöscht" in response.text
    assert not stored_path.exists()
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 0
