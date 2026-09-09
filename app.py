from __future__ import annotations

import json
import os
import secrets
import sqlite3
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
    g,
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
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
LANGUAGES = {"DE": "Deutsch", "EN": "Englisch"}


def configured_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "lokaler-entwicklungsschluessel-bitte-aendern"),
        DATABASE=configured_path(os.getenv("DATABASE_PATH", "instance/medienserver.sqlite3")),
        VIDEO_DIR=configured_path(os.getenv("VIDEO_DIR", "uploads/videos")),
        THUMBNAIL_DIR=configured_path(os.getenv("THUMBNAIL_DIR", "uploads/thumbnails")),
        MAX_CONTENT_LENGTH=int(os.getenv("MAX_UPLOAD_MB", "2048")) * 1024 * 1024,
        FFPROBE_PATH=os.getenv("FFPROBE_PATH", "ffprobe"),
        FFMPEG_PATH=os.getenv("FFMPEG_PATH", "ffmpeg"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)

    app.config["DATABASE"] = configured_path(app.config["DATABASE"])
    app.config["VIDEO_DIR"] = configured_path(app.config["VIDEO_DIR"])
    app.config["THUMBNAIL_DIR"] = configured_path(app.config["THUMBNAIL_DIR"])
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    Path(app.config["VIDEO_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["THUMBNAIL_DIR"]).mkdir(parents=True, exist_ok=True)

    register_database(app)
    register_security(app)
    register_template_helpers(app)
    register_routes(app)
    register_errors(app)

    with app.app_context():
        init_db()
    return app


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app_config("DATABASE"),
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def current_app_config(key: str) -> Any:
    from flask import current_app

    return current_app.config[key]


def close_db(_error: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    schema = (BASE_DIR / "schema.sql").read_text(encoding="utf-8")
    db.executescript(schema)
    db.commit()


def register_database(app: Flask) -> None:
    app.teardown_appcontext(close_db)


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


def parse_duration(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    if value.isdigit():
        seconds = int(value)
        return seconds if seconds > 0 else None
    parts = value.split(":")
    if len(parts) not in {2, 3} or not all(part.isdigit() for part in parts):
        raise ValueError("Dauer als Sekunden, MM:SS oder HH:MM:SS eingeben.")
    numbers = [int(part) for part in parts]
    if numbers[-1] >= 60 or (len(numbers) == 3 and numbers[-2] >= 60):
        raise ValueError("Minuten und Sekunden müssen kleiner als 60 sein.")
    seconds = numbers[-1] + numbers[-2] * 60
    if len(numbers) == 3:
        seconds += numbers[0] * 3600
    if seconds <= 0:
        raise ValueError("Die Dauer muss größer als null sein.")
    return seconds


def probe_duration(path: Path) -> int | None:
    try:
        result = subprocess.run(
            [
                str(current_app_config("FFPROBE_PATH")),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        duration = float(json.loads(result.stdout)["format"]["duration"])
        return max(1, round(duration))
    except (FileNotFoundError, subprocess.SubprocessError, KeyError, ValueError, json.JSONDecodeError):
        return None


def generate_thumbnail(video_path: Path, directory: Path) -> str | None:
    """Speichert den ersten Videoframe als kompaktes JPEG, wenn FFmpeg verfügbar ist."""
    filename = f"automatisch-{uuid.uuid4().hex}.jpg"
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


def save_upload(storage, directory: Path, allowed_extensions: set[str]) -> str:
    original = secure_filename(storage.filename or "")
    suffix = Path(original).suffix.lower()
    if not original or suffix not in allowed_extensions:
        allowed = ", ".join(sorted(allowed_extensions))
        raise ValueError(f"Nicht unterstützter Dateityp. Erlaubt: {allowed}")
    stem = secure_filename(Path(original).stem)[:60] or "datei"
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


def form_values() -> dict[str, Any]:
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    topic = request.form.get("topic", "").strip()
    language = request.form.get("language", "").upper()
    year_text = request.form.get("year", "").strip()
    if not title:
        raise ValueError("Ein Titel ist erforderlich.")
    if not topic:
        raise ValueError("Ein Thema ist erforderlich.")
    if language not in LANGUAGES:
        raise ValueError("Bitte Deutsch oder Englisch auswählen.")
    try:
        year = int(year_text)
    except ValueError as error:
        raise ValueError("Bitte ein gültiges Erscheinungsjahr eingeben.") from error
    if year < 1900 or year > datetime.now().year + 1:
        raise ValueError("Das Erscheinungsjahr liegt außerhalb des gültigen Bereichs.")
    duration = parse_duration(request.form.get("duration", ""))
    return {
        "title": title,
        "description": description,
        "topic": topic,
        "language": language,
        "year": year,
        "duration_seconds": duration,
    }


def register_routes(app: Flask) -> None:
    @app.get("/")
    def catalog():
        query = request.args.get("q", "").strip()
        topic = request.args.get("topic", "").strip()
        language = request.args.get("language", "").upper().strip()
        sql = "SELECT * FROM videos WHERE 1 = 1"
        params: list[Any] = []
        if query:
            sql += " AND (title LIKE ? OR description LIKE ? OR topic LIKE ?)"
            search = f"%{query}%"
            params.extend([search, search, search])
        if topic:
            sql += " AND topic = ?"
            params.append(topic)
        if language in LANGUAGES:
            sql += " AND language = ?"
            params.append(language)
        sql += " ORDER BY created_at DESC, title COLLATE NOCASE"
        db = get_db()
        videos = db.execute(sql, params).fetchall()
        topics = db.execute("SELECT DISTINCT topic FROM videos ORDER BY topic COLLATE NOCASE").fetchall()
        return render_template(
            "catalog.html",
            videos=videos,
            topics=[row["topic"] for row in topics],
            filters={"q": query, "topic": topic, "language": language},
        )

    @app.get("/video/<int:video_id>")
    def video_detail(video_id: int):
        video = get_db().execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
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
        videos = get_db().execute(
            "SELECT * FROM videos ORDER BY created_at DESC, title COLLATE NOCASE"
        ).fetchall()
        return render_template("manage/dashboard.html", videos=videos)

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
                saved_video = save_upload(video_upload, app.config["VIDEO_DIR"], VIDEO_EXTENSIONS)
                thumbnail_upload = request.files.get("thumbnail_file")
                if thumbnail_upload and thumbnail_upload.filename:
                    saved_thumbnail = save_upload(
                        thumbnail_upload, app.config["THUMBNAIL_DIR"], IMAGE_EXTENSIONS
                    )
                else:
                    saved_thumbnail = generate_thumbnail(
                        app.config["VIDEO_DIR"] / saved_video, app.config["THUMBNAIL_DIR"]
                    )
                if values["duration_seconds"] is None:
                    values["duration_seconds"] = probe_duration(app.config["VIDEO_DIR"] / saved_video)
                if values["duration_seconds"] is None:
                    raise ValueError(
                        "Die Dauer konnte nicht automatisch gelesen werden. Bitte manuell eingeben."
                    )
                db = get_db()
                db.execute(
                    """
                    INSERT INTO videos
                        (title, description, topic, language, duration_seconds, year,
                         video_path, thumbnail_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        values["title"], values["description"], values["topic"],
                        values["language"], values["duration_seconds"], values["year"],
                        saved_video, saved_thumbnail,
                    ),
                )
                db.commit()
                flash("Video wurde hinzugefügt.", "success")
                return redirect(url_for("manage_videos"))
            except (ValueError, sqlite3.DatabaseError) as error:
                remove_file(app.config["VIDEO_DIR"], saved_video)
                remove_file(app.config["THUMBNAIL_DIR"], saved_thumbnail)
                flash(str(error), "error")
        return render_template("manage/video_form.html", video=None, mode="new")

    @app.route("/manage/videos/<int:video_id>/edit", methods=["GET", "POST"])
    def edit_video(video_id: int):
        db = get_db()
        video = db.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        if video is None:
            abort(404)
        if request.method == "POST":
            new_video = None
            new_thumbnail = None
            try:
                values = form_values()
                video_upload = request.files.get("video_file")
                thumbnail_upload = request.files.get("thumbnail_file")
                final_video = video["video_path"]
                final_thumbnail = video["thumbnail_path"]
                if video_upload and video_upload.filename:
                    new_video = save_upload(video_upload, app.config["VIDEO_DIR"], VIDEO_EXTENSIONS)
                    final_video = new_video
                    if values["duration_seconds"] is None:
                        values["duration_seconds"] = probe_duration(app.config["VIDEO_DIR"] / new_video)
                if thumbnail_upload and thumbnail_upload.filename:
                    new_thumbnail = save_upload(
                        thumbnail_upload, app.config["THUMBNAIL_DIR"], IMAGE_EXTENSIONS
                    )
                    final_thumbnail = new_thumbnail
                elif new_video:
                    new_thumbnail = generate_thumbnail(
                        app.config["VIDEO_DIR"] / new_video, app.config["THUMBNAIL_DIR"]
                    )
                    if new_thumbnail:
                        final_thumbnail = new_thumbnail
                if values["duration_seconds"] is None:
                    values["duration_seconds"] = video["duration_seconds"]
                db.execute(
                    """
                    UPDATE videos SET title = ?, description = ?, topic = ?, language = ?,
                        duration_seconds = ?, year = ?, video_path = ?, thumbnail_path = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        values["title"], values["description"], values["topic"],
                        values["language"], values["duration_seconds"], values["year"],
                        final_video, final_thumbnail, video_id,
                    ),
                )
                db.commit()
                if new_video:
                    remove_file(app.config["VIDEO_DIR"], video["video_path"])
                if new_thumbnail:
                    remove_file(app.config["THUMBNAIL_DIR"], video["thumbnail_path"])
                flash("Video wurde aktualisiert.", "success")
                return redirect(url_for("manage_videos"))
            except (ValueError, sqlite3.DatabaseError) as error:
                remove_file(app.config["VIDEO_DIR"], new_video)
                remove_file(app.config["THUMBNAIL_DIR"], new_thumbnail)
                flash(str(error), "error")
        return render_template("manage/video_form.html", video=video, mode="edit")

    @app.post("/manage/videos/<int:video_id>/delete")
    def delete_video(video_id: int):
        db = get_db()
        video = db.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        if video is None:
            abort(404)
        db.execute("DELETE FROM videos WHERE id = ?", (video_id,))
        db.commit()
        remove_file(app.config["VIDEO_DIR"], video["video_path"])
        remove_file(app.config["THUMBNAIL_DIR"], video["thumbnail_path"])
        flash("Video und zugehörige Dateien wurden gelöscht.", "success")
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
