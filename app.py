from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

VIDEO_EXTENSIONS = {".mp4"}
LANGUAGES = {"DE": "Deutsch", "EN": "Englisch"}
YEAR_PATTERN = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def configured_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


def configured_executable(env_name: str, local_path: str, fallback: str) -> str:
    value = os.getenv(env_name)
    bundled = BASE_DIR / local_path
    if (not value or value == fallback) and bundled.is_file():
        return str(bundled)
    if value:
        path = Path(value).expanduser()
        if not path.is_absolute() and ("/" in value or "\\" in value):
            return str(BASE_DIR / path)
        return str(path)
    return fallback


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "lokaler-entwicklungsschluessel-bitte-aendern"),
        METADATA_DIR=configured_path(os.getenv("METADATA_DIR", "data/videos")),
        VIDEO_DIR=configured_path(os.getenv("VIDEO_DIR", "uploads/videos")),
        THUMBNAIL_DIR=configured_path(os.getenv("THUMBNAIL_DIR", "uploads/thumbnails")),
        MAX_CONTENT_LENGTH=int(os.getenv("MAX_UPLOAD_MB", "2048")) * 1024 * 1024,
        FFPROBE_PATH=configured_executable(
            "FFPROBE_PATH", "tools/ffmpeg/bin/ffprobe.exe", "ffprobe"
        ),
        FFMPEG_PATH=configured_executable(
            "FFMPEG_PATH", "tools/ffmpeg/bin/ffmpeg.exe", "ffmpeg"
        ),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)

    for key in ("METADATA_DIR", "VIDEO_DIR", "THUMBNAIL_DIR"):
        app.config[key] = configured_path(app.config[key])
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)

    register_security(app)
    register_template_helpers(app)
    register_routes(app)
    register_errors(app)
    return app


def current_app_config(key: str) -> Any:
    from flask import current_app

    return current_app.config[key]


def csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def register_security(app: Flask) -> None:
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def verify_csrf() -> None:
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            expected = session.get("csrf_token", "")
            supplied = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
            if not expected or not secrets.compare_digest(expected, supplied):
                abort(400, description="Ungültiger oder abgelaufener CSRF-Token.")


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "–"
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def register_template_helpers(app: Flask) -> None:
    app.jinja_env.filters["duration"] = format_duration
    app.jinja_env.globals["language_names"] = LANGUAGES


def metadata_path(video_id: str) -> Path | None:
    try:
        safe_id = uuid.UUID(video_id).hex
    except (ValueError, AttributeError):
        return None
    return Path(current_app_config("METADATA_DIR")) / f"{safe_id}.json"


def read_video(video_id: str) -> dict[str, Any] | None:
    path = metadata_path(video_id)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("id") == path.stem else None


def list_videos() -> list[dict[str, Any]]:
    videos: list[dict[str, Any]] = []
    for path in Path(current_app_config("METADATA_DIR")).glob("*.json"):
        video = read_video(path.stem)
        if video is not None:
            videos.append(video)
    videos.sort(key=lambda video: str(video.get("title", "")).casefold())
    videos.sort(key=lambda video: str(video.get("created_at", "")), reverse=True)
    return videos


def write_video(video: dict[str, Any]) -> None:
    path = metadata_path(str(video.get("id", "")))
    if path is None:
        raise ValueError("Ungültige Video-ID.")
    temporary = path.with_name(f".{path.stem}-{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(video, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def delete_video_document(video_id: str) -> bool:
    path = metadata_path(video_id)
    if path is None or not path.is_file():
        return False
    path.unlink()
    return True


def extract_year(probe_data: dict[str, Any]) -> int:
    tag_groups: list[dict[str, Any]] = []
    format_tags = probe_data.get("format", {}).get("tags", {})
    if isinstance(format_tags, dict):
        tag_groups.append(format_tags)
    for stream in probe_data.get("streams", []):
        tags = stream.get("tags", {}) if isinstance(stream, dict) else {}
        if isinstance(tags, dict):
            tag_groups.append(tags)

    maximum_year = datetime.now().year + 1
    for tags in tag_groups:
        lowered = {str(key).lower(): str(value) for key, value in tags.items()}
        for key in ("date", "year", "creation_time"):
            match = YEAR_PATTERN.search(lowered.get(key, ""))
            if match:
                year = int(match.group(1))
                if 1900 <= year <= maximum_year:
                    return year
    return datetime.now().year


def probe_video_metadata(path: Path) -> dict[str, Any] | None:
    try:
        result = subprocess.run(
            [
                str(current_app_config("FFPROBE_PATH")),
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        probe_data = json.loads(result.stdout)
        duration = max(1, round(float(probe_data["format"]["duration"])))
        probe_data.get("format", {}).pop("filename", None)
        return {
            "duration_seconds": duration,
            "year": extract_year(probe_data),
            "technical_metadata": probe_data,
        }
    except (
        FileNotFoundError,
        subprocess.SubprocessError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None


def generate_thumbnail(video_path: Path, directory: Path) -> str | None:
    filename = f"{video_path.stem}-{uuid.uuid4().hex}.jpg"
    output_path = directory / filename
    try:
        subprocess.run(
            [
                str(current_app_config("FFMPEG_PATH")),
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                "scale=min(1280\\,iw):-2",
                "-q:v",
                "3",
                str(output_path),
            ],
            capture_output=True,
            timeout=60,
            check=True,
        )
        if output_path.is_file() and output_path.stat().st_size > 0:
            return filename
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass
    try:
        output_path.unlink(missing_ok=True)
    except OSError:
        pass
    return None


def save_upload(storage, directory: Path) -> str:
    original = secure_filename(storage.filename or "")
    suffix = Path(original).suffix.lower()
    if not original or suffix not in VIDEO_EXTENSIONS:
        raise ValueError("Nicht unterstützter Dateityp. Erlaubt: .mp4")
    stem = secure_filename(Path(original).stem)[:60] or "video"
    filename = f"{stem}-{uuid.uuid4().hex}{suffix}"
    storage.save(directory / filename)
    return filename


def remove_file(directory: Path, filename: str | None) -> None:
    if not filename:
        return
    candidate = (directory / filename).resolve()
    try:
        candidate.relative_to(directory.resolve())
        candidate.unlink(missing_ok=True)
    except (ValueError, OSError):
        pass


def form_values() -> dict[str, str]:
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    topic = request.form.get("topic", "").strip()
    language = request.form.get("language", "").upper()
    if not title:
        raise ValueError("Ein Titel ist erforderlich.")
    if not topic:
        raise ValueError("Ein Thema ist erforderlich.")
    if language not in LANGUAGES:
        raise ValueError("Bitte Deutsch oder Englisch auswählen.")
    return {
        "title": title,
        "description": description,
        "topic": topic,
        "language": language,
    }


def analyze_upload(video_filename: str) -> tuple[dict[str, Any], str]:
    video_path = Path(current_app_config("VIDEO_DIR")) / video_filename
    automatic = probe_video_metadata(video_path)
    if automatic is None:
        raise ValueError(
            "Dauer und Erscheinungsjahr konnten nicht gelesen werden. "
            "Bitte eine gültige MP4-Datei hochladen und ffprobe prüfen."
        )
    thumbnail = generate_thumbnail(video_path, Path(current_app_config("THUMBNAIL_DIR")))
    if thumbnail is None:
        raise ValueError(
            "Das Vorschaubild konnte nicht erzeugt werden. Bitte Videodatei und ffmpeg prüfen."
        )
    return automatic, thumbnail


def register_routes(app: Flask) -> None:
    @app.get("/")
    def catalog():
        query = request.args.get("q", "").strip()
        topic = request.args.get("topic", "").strip()
        language = request.args.get("language", "").upper().strip()
        videos = list_videos()
        topics = sorted(
            {str(video.get("topic", "")) for video in videos if video.get("topic")},
            key=str.casefold,
        )
        if query:
            needle = query.casefold()
            videos = [
                video
                for video in videos
                if needle
                in " ".join(
                    [
                        str(video.get("title", "")),
                        str(video.get("description", "")),
                        str(video.get("topic", "")),
                    ]
                ).casefold()
            ]
        if topic:
            videos = [video for video in videos if video.get("topic") == topic]
        if language in LANGUAGES:
            videos = [video for video in videos if video.get("language") == language]
        return render_template(
            "catalog.html",
            videos=videos,
            topics=topics,
            filters={"q": query, "topic": topic, "language": language},
        )

    @app.get("/video/<video_id>")
    def video_detail(video_id: str):
        video = read_video(video_id)
        if video is None:
            abort(404)
        return render_template("video_detail.html", video=video)

    @app.get("/media/videos/<path:filename>")
    def serve_video(filename: str):
        response = send_from_directory(
            app.config["VIDEO_DIR"], filename, conditional=True, mimetype="video/mp4"
        )
        response.headers.setdefault("Accept-Ranges", "bytes")
        return response

    @app.get("/media/thumbnails/<path:filename>")
    def serve_thumbnail(filename: str):
        return send_from_directory(app.config["THUMBNAIL_DIR"], filename, conditional=True)

    @app.get("/admin", strict_slashes=False)
    @app.get("/admin/login", strict_slashes=False)
    def former_admin_redirect():
        return redirect(url_for("manage_videos"))

    @app.get("/manage", strict_slashes=False)
    def manage_videos():
        return render_template("manage/dashboard.html", videos=list_videos())

    @app.route("/upload", methods=["GET", "POST"])
    def upload_video():
        if request.method == "POST":
            saved_video = None
            saved_thumbnail = None
            try:
                values = form_values()
                video_upload = request.files.get("video_file")
                if video_upload is None or not video_upload.filename:
                    raise ValueError("Bitte eine MP4-Datei auswählen.")
                saved_video = save_upload(video_upload, app.config["VIDEO_DIR"])
                automatic, saved_thumbnail = analyze_upload(saved_video)
                video_id = uuid.uuid4().hex
                timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
                video = {
                    "id": video_id,
                    **values,
                    **automatic,
                    "video_path": saved_video,
                    "thumbnail_path": saved_thumbnail,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
                write_video(video)
                flash("Video und automatische Metadaten wurden gespeichert.", "success")
                return redirect(url_for("manage_videos"))
            except (ValueError, OSError) as error:
                remove_file(app.config["VIDEO_DIR"], saved_video)
                remove_file(app.config["THUMBNAIL_DIR"], saved_thumbnail)
                flash(str(error), "error")
        return render_template("manage/video_form.html", video=None, mode="new")

    @app.route("/manage/videos/<video_id>/edit", methods=["GET", "POST"])
    def edit_video(video_id: str):
        video = read_video(video_id)
        if video is None:
            abort(404)
        if request.method == "POST":
            new_video = None
            new_thumbnail = None
            old_video = video.get("video_path")
            old_thumbnail = video.get("thumbnail_path")
            try:
                values = form_values()
                replacement = request.files.get("video_file")
                if replacement and replacement.filename:
                    new_video = save_upload(replacement, app.config["VIDEO_DIR"])
                    automatic, new_thumbnail = analyze_upload(new_video)
                    values.update(automatic)
                    values["video_path"] = new_video
                    values["thumbnail_path"] = new_thumbnail
                video.update(values)
                video["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                write_video(video)
                if new_video:
                    remove_file(app.config["VIDEO_DIR"], old_video)
                    remove_file(app.config["THUMBNAIL_DIR"], old_thumbnail)
                flash("Video wurde aktualisiert.", "success")
                return redirect(url_for("manage_videos"))
            except (ValueError, OSError) as error:
                remove_file(app.config["VIDEO_DIR"], new_video)
                remove_file(app.config["THUMBNAIL_DIR"], new_thumbnail)
                flash(str(error), "error")
        return render_template("manage/video_form.html", video=video, mode="edit")

    @app.post("/manage/videos/<video_id>/delete")
    def delete_video(video_id: str):
        video = read_video(video_id)
        if video is None:
            abort(404)
        delete_video_document(video_id)
        remove_file(app.config["VIDEO_DIR"], video.get("video_path"))
        remove_file(app.config["THUMBNAIL_DIR"], video.get("thumbnail_path"))
        flash("Video, Vorschaubild und JSON-Metadaten wurden gelöscht.", "success")
        return redirect(url_for("manage_videos"))


def register_errors(app: Flask) -> None:
    @app.errorhandler(400)
    def bad_request(error):
        return render_template("error.html", code=400, message=error.description), 400

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", code=404, message="Die Seite wurde nicht gefunden."), 404

    @app.errorhandler(413)
    def too_large(_error):
        limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        return render_template(
            "error.html", code=413, message=f"Die Datei überschreitet das Limit von {limit_mb} MB."
        ), 413


if __name__ == "__main__":
    create_app().run(
        host=os.getenv("FLASK_HOST", "127.0.0.1"),
        port=int(os.getenv("FLASK_PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
