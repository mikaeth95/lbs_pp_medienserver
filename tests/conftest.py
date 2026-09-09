import io
import re

import pytest

from app import create_app


@pytest.fixture()
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE": tmp_path / "test.sqlite3",
            "VIDEO_DIR": tmp_path / "videos",
            "THUMBNAIL_DIR": tmp_path / "thumbnails",
            "MAX_CONTENT_LENGTH": 1024 * 1024,
            "FFPROBE_PATH": "nicht-vorhandenes-ffprobe",
        }
    )
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


def csrf_from(response) -> str:
    match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
    assert match is not None
    return match.group(1).decode()


@pytest.fixture()
def upload_video(client):
    def upload(title="Excel Grundlagen", topic="Office", language="DE", filename="kurs.mp4"):
        token = csrf_from(client.get("/upload"))
        return client.post(
            "/upload",
            data={
                "csrf_token": token,
                "title": title,
                "description": "Ein kurzer Einführungskurs",
                "topic": topic,
                "language": language,
                "year": "2025",
                "duration": "01:30",
                "video_file": (io.BytesIO(b"0123456789abcdefghijklmnopqrstuvwxyz"), filename),
                "thumbnail_file": (io.BytesIO(b"fake-image"), "vorschaubild.jpg"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )

    return upload
