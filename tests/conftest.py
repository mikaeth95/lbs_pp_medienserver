import io
import re
import uuid
from pathlib import Path

import pytest

import app as app_module
from app import create_app


PASSWORD = "geheim-123"


@pytest.fixture()
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "METADATA_DIR": tmp_path / "metadata",
            "USER_DIR": tmp_path / "users",
            "VIDEO_DIR": tmp_path / "videos",
            "THUMBNAIL_DIR": tmp_path / "thumbnails",
            "MAX_CONTENT_LENGTH": 1024 * 1024,
            "FFPROBE_PATH": "nicht-vorhandenes-ffprobe",
            "FFMPEG_PATH": "nicht-vorhandenes-ffmpeg",
            "ALLOW_REGISTRATION": True,
            "MEDIA_X_ACCEL": False,
        }
    )
    with application.app_context():
        for username, role in (
            ("admin", "admin"),
            ("trainer", "trainer"),
            ("trainer2", "trainer"),
            ("schueler", "teilnehmer"),
        ):
            app_module.create_user(username, PASSWORD, role)
    yield application


def csrf_from(response) -> str:
    match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
    assert match is not None
    return match.group(1).decode()


def login(client, username, password=PASSWORD):
    token = csrf_from(client.get("/login"))
    return client.post(
        "/login",
        data={"csrf_token": token, "username": username, "password": password},
        follow_redirects=True,
    )


@pytest.fixture()
def login_as(app):
    def make_client(username):
        client = app.test_client()
        response = login(client, username)
        assert "Willkommen" in response.text
        return client

    return make_client


@pytest.fixture()
def client(login_as):
    return login_as("trainer")


@pytest.fixture()
def fake_media(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "probe_video_metadata",
        lambda _path: {
            "duration_seconds": 90,
            "year": 2024,
            "technical_metadata": {
                "format": {"format_name": "mov,mp4"},
                "streams": [{"codec_name": "h264", "width": 1280, "height": 720}],
            },
        },
    )

    def fake_thumbnail(video_path, directory):
        filename = f"{Path(video_path).stem}-{uuid.uuid4().hex}.jpg"
        Path(directory, filename).write_bytes(b"generated-thumbnail")
        return filename

    monkeypatch.setattr(app_module, "generate_thumbnail", fake_thumbnail)


def post_upload(client, title="Excel Grundlagen", topic="Office", language="DE", filename="kurs.mp4"):
    token = csrf_from(client.get("/upload"))
    return client.post(
        "/upload",
        data={
            "csrf_token": token,
            "title": title,
            "description": "Ein kurzer Einführungskurs",
            "topic": topic,
            "language": language,
            "video_file": (io.BytesIO(b"0123456789abcdefghijklmnopqrstuvwxyz"), filename),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )


@pytest.fixture()
def upload_video(client, fake_media):
    def upload(title="Excel Grundlagen", topic="Office", language="DE", filename="kurs.mp4", as_client=None):
        return post_upload(as_client or client, title, topic, language, filename)

    return upload
