import io
import json
import subprocess
from pathlib import Path

import pytest

import app as app_module
from conftest import csrf_from


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_FFMPEG = PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
LOCAL_FFPROBE = PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"


def json_documents(app):
    return sorted(Path(app.config["METADATA_DIR"]).glob("*.json"))


def load_only_video(app):
    documents = json_documents(app)
    assert len(documents) == 1
    return json.loads(documents[0].read_text(encoding="utf-8"))


def test_catalog_upload_and_management_are_public(client):
    assert client.get("/").status_code == 200
    upload_page = client.get("/upload")
    assert upload_page.status_code == 200
    assert client.get("/manage").status_code == 200
    assert 'name="duration"' not in upload_page.text
    assert 'name="year"' not in upload_page.text
    assert 'name="thumbnail_file"' not in upload_page.text


def test_csrf_protects_video_changes(client):
    response = client.post("/manage/videos/ungueltig/delete", data={})
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
            "video_file": (io.BytesIO(b"x" * (1024 * 1024 + 1)), "gross.mp4"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 413


def test_upload_creates_one_json_document_with_automatic_metadata(app, upload_video):
    response = upload_video(filename="../../Mein Kurs.mp4")
    assert response.status_code == 200
    assert "automatische Metadaten" in response.text

    video = load_only_video(app)
    assert video["title"] == "Excel Grundlagen"
    assert video["duration_seconds"] == 90
    assert video["year"] == 2024
    assert video["language"] == "DE"
    assert video["technical_metadata"]["streams"][0]["codec_name"] == "h264"
    assert video["id"] == json_documents(app)[0].stem
    assert "/" not in video["video_path"]
    assert "\\" not in video["video_path"]
    assert Path(app.config["VIDEO_DIR"], video["video_path"]).is_file()
    assert Path(app.config["THUMBNAIL_DIR"], video["thumbnail_path"]).is_file()
    assert not list(Path(app.config["METADATA_DIR"]).glob("*.tmp"))


def test_invalid_video_removes_failed_upload(app, client, monkeypatch):
    monkeypatch.setattr(app_module, "probe_video_metadata", lambda _path: None)
    token = csrf_from(client.get("/upload"))
    response = client.post(
        "/upload",
        data={
            "csrf_token": token,
            "title": "Ungültig",
            "topic": "Test",
            "language": "DE",
            "video_file": (io.BytesIO(b"not-a-real-video"), "test.mp4"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert "konnten nicht gelesen werden" in response.text
    assert not json_documents(app)
    assert not list(Path(app.config["VIDEO_DIR"]).iterdir())


def test_combined_search_topic_and_language_filters(app, upload_video):
    upload_video(title="Excel Deutsch", topic="Office", language="DE")
    upload_video(title="Excel English", topic="Office", language="EN")
    upload_video(title="Python Deutsch", topic="Programmierung", language="DE")
    assert len(json_documents(app)) == 3

    response = app.test_client().get("/?q=Excel&topic=Office&language=DE")
    assert response.status_code == 200
    assert "Excel Deutsch" in response.text
    assert "Excel English" not in response.text
    assert "Python Deutsch" not in response.text


def test_range_request_returns_partial_video(app, upload_video):
    upload_video()
    video = load_only_video(app)
    response = app.test_client().get(
        f"/media/videos/{video['video_path']}", headers={"Range": "bytes=5-12"}
    )
    assert response.status_code == 206
    assert response.data == b"0123456789abcdefghijklmnopqrstuvwxyz"[5:13]
    assert response.headers["Content-Range"].startswith("bytes 5-12/")
    assert response.headers["Accept-Ranges"] == "bytes"


def test_edit_rewrites_json_and_delete_removes_all_files(app, client, upload_video):
    upload_video()
    original = load_only_video(app)
    old_video = Path(app.config["VIDEO_DIR"], original["video_path"])
    old_thumbnail = Path(app.config["THUMBNAIL_DIR"], original["thumbnail_path"])

    token = csrf_from(client.get(f"/manage/videos/{original['id']}/edit"))
    edited = client.post(
        f"/manage/videos/{original['id']}/edit",
        data={
            "csrf_token": token,
            "title": " Excel für Fortgeschrittene ",
            "description": "Aktualisierte Beschreibung",
            "topic": "Office",
            "language": "EN",
            "video_file": (io.BytesIO(b"new-video-content"), "neu.mp4"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert edited.status_code == 200
    updated = load_only_video(app)
    assert updated["title"] == "Excel für Fortgeschrittene"
    assert updated["language"] == "EN"
    assert updated["id"] == original["id"]
    assert not old_video.exists()
    assert not old_thumbnail.exists()

    new_video = Path(app.config["VIDEO_DIR"], updated["video_path"])
    new_thumbnail = Path(app.config["THUMBNAIL_DIR"], updated["thumbnail_path"])
    token = csrf_from(client.get("/manage"))
    deleted = client.post(
        f"/manage/videos/{updated['id']}/delete",
        data={"csrf_token": token},
        follow_redirects=True,
    )
    assert deleted.status_code == 200
    assert not json_documents(app)
    assert not new_video.exists()
    assert not new_thumbnail.exists()


@pytest.mark.skipif(
    not LOCAL_FFMPEG.is_file() or not LOCAL_FFPROBE.is_file(),
    reason="Projektlokales FFmpeg ist nicht installiert.",
)
def test_real_upload_extracts_duration_year_and_first_frame(app, client, tmp_path):
    source = tmp_path / "real-test.mp4"
    subprocess.run(
        [
            str(LOCAL_FFMPEG),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-metadata",
            "creation_time=2024-01-15T10:00:00Z",
            str(source),
        ],
        check=True,
        timeout=30,
    )
    app.config["FFMPEG_PATH"] = str(LOCAL_FFMPEG)
    app.config["FFPROBE_PATH"] = str(LOCAL_FFPROBE)
    token = csrf_from(client.get("/upload"))
    with source.open("rb") as video_file:
        response = client.post(
            "/upload",
            data={
                "csrf_token": token,
                "title": "Echter FFmpeg-Test",
                "description": "Automatisch ausgewertet",
                "topic": "Test",
                "language": "DE",
                "video_file": (video_file, "real-test.mp4"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
    assert response.status_code == 200
    assert "automatische Metadaten" in response.text
    video = load_only_video(app)
    assert video["duration_seconds"] == 2
    assert video["year"] == 2024
    assert video["technical_metadata"]["format"]["format_name"].startswith("mov,mp4")
    assert "filename" not in video["technical_metadata"]["format"]
    assert {stream["codec_type"] for stream in video["technical_metadata"]["streams"]} == {
        "audio",
        "video",
    }
    assert Path(app.config["THUMBNAIL_DIR"], video["thumbnail_path"]).stat().st_size > 0
