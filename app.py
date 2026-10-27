from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import uuid
from datetime import datetime
from functools import lru_cache, wraps
from pathlib import Path
from typing import Any
from urllib.parse import quote

import click
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
from werkzeug.security import check_password_hash, generate_password_hash, safe_join
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

VIDEO_EXTENSIONS = {".mp4"}
LANGUAGES = {"DE": "Deutsch", "EN": "Englisch"}
YEAR_PATTERN = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
SORT_OPTIONS = {
    "newest": "Neueste zuerst",
    "oldest": "Älteste zuerst",
    "title": "Titel A–Z",
    "duration": "Kürzeste zuerst",
}

# Berechtigungsstufen: Administrator > Trainer > Teilnehmer.
ROLES = {"admin": "Administrator", "trainer": "Trainer", "teilnehmer": "Teilnehmer"}
CONTENT_ROLES = {"admin", "trainer"}
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,30}[a-z0-9]$")
# Unter Windows nicht als Dateiname erlaubt, auch nicht mit Endung.
RESERVED_USERNAME = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\.|$)")
MIN_PASSWORD_LENGTH = 8
PUBLIC_ENDPOINTS = {"login", "logout", "register", "static"}


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
        USER_DIR=configured_path(os.getenv("USER_DIR", "data/users")),
        VIDEO_DIR=configured_path(os.getenv("VIDEO_DIR", "uploads/videos")),
        THUMBNAIL_DIR=configured_path(os.getenv("THUMBNAIL_DIR", "uploads/thumbnails")),
        MAX_CONTENT_LENGTH=int(os.getenv("MAX_UPLOAD_MB", "2048")) * 1024 * 1024,
        ALLOW_REGISTRATION=os.getenv("ALLOW_REGISTRATION", "1") == "1",
        MEDIA_X_ACCEL=os.getenv("MEDIA_X_ACCEL", "0") == "1",
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

    for key in ("METADATA_DIR", "USER_DIR", "VIDEO_DIR", "THUMBNAIL_DIR"):
        app.config[key] = configured_path(app.config[key])
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)

    register_auth(app)
    register_security(app)
    register_template_helpers(app)
    register_routes(app)
    register_user_routes(app)
    register_cli(app)
    register_errors(app)
    return app


def current_app_config(key: str) -> Any:
    from flask import current_app

    return current_app.config[key]


def csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def log_in(user: dict[str, Any]) -> None:
    session.clear()
    session["user"] = user["username"]
    session["session_key"] = user["session_key"]


def current_user_from_session() -> dict[str, Any] | None:
    username = session.get("user")
    if not username:
        return None
    user = read_user(username)
    if user is None or not user.get("active"):
        return None
    if not secrets.compare_digest(str(user.get("session_key", "")), str(session.get("session_key", ""))):
        return None
    return user


def safe_next(target: str | None) -> str:
    # Nur Pfade dieser Seite; Steuerzeichen würden Browser zu "//fremde-seite" umdeuten lassen.
    if (
        target
        and target.startswith("/")
        and not target.startswith("//")
        and "\\" not in target
        and not any(ord(char) < 32 or ord(char) == 127 for char in target)
    ):
        return target
    return url_for("catalog")


def role_required(*roles: str):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None or g.user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def register_auth(app: Flask) -> None:
    @app.before_request
    def require_login():
        g.user = current_user_from_session()
        if g.user is None and session.get("user"):
            # CSRF-Token behalten, damit z. B. "Abmelden" mit abgelaufener Sitzung noch klappt.
            session.pop("user", None)
            session.pop("session_key", None)
        if g.user is None and request.endpoint not in PUBLIC_ENDPOINTS and request.endpoint is not None:
            # Nach dem Login zurück nur zu Seiten, die per GET aufrufbar sind (nicht z. B. zu /logout).
            target = request.full_path.rstrip("?") if request.method == "GET" else None
            return redirect(url_for("login", next=target))
        return None

    @app.context_processor
    def inject_user() -> dict[str, Any]:
        user = g.get("user")
        return {
            "current_user": user,
            "is_admin": is_admin(user),
            "can_upload": can_upload(user),
            "open_trainer_requests": len(trainer_requests()) if is_admin(user) else 0,
        }


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
    app.jinja_env.globals["role_names"] = ROLES
    app.jinja_env.globals["sort_options"] = SORT_OPTIONS
    app.jinja_env.globals["can_manage"] = can_manage_video


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


def json_text(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def write_json(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.stem}-{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json_text(document), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_video(video: dict[str, Any]) -> None:
    path = metadata_path(str(video.get("id", "")))
    if path is None:
        raise ValueError("Ungültige Video-ID.")
    write_json(path, video)


def delete_video_document(video_id: str) -> bool:
    path = metadata_path(video_id)
    if path is None or not path.is_file():
        return False
    path.unlink()
    return True


def normalize_username(value: Any) -> str:
    return str(value or "").strip().lower()


def valid_username(username: str) -> bool:
    return bool(USERNAME_PATTERN.match(username)) and not RESERVED_USERNAME.match(username)


def user_path(username: str) -> Path | None:
    if not isinstance(username, str) or not valid_username(username):
        return None
    return Path(current_app_config("USER_DIR")) / f"{username}.json"


def read_user(username: str) -> dict[str, Any] | None:
    path = user_path(username)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("username") != path.stem:
        return None
    return data if data.get("role") in ROLES else None


def list_users() -> list[dict[str, Any]]:
    users = [
        user
        for user in (read_user(path.stem) for path in Path(current_app_config("USER_DIR")).glob("*.json"))
        if user is not None
    ]
    users.sort(key=lambda user: user["username"])
    return users


def write_user(user: dict[str, Any]) -> None:
    path = user_path(str(user.get("username", "")))
    if path is None:
        raise ValueError("Ungültiger Benutzername.")
    write_json(path, user)


def validate_password(password: str, confirmation: str | None = None) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein.")
    if confirmation is not None and password != confirmation:
        raise ValueError("Die beiden Passwörter stimmen nicht überein.")


def set_password(user: dict[str, Any], password: str) -> None:
    user["password_hash"] = generate_password_hash(password)
    # Ein neuer Sitzungsschlüssel meldet alle anderen offenen Sitzungen ab.
    user["session_key"] = secrets.token_urlsafe(16)


def create_user(
    username: str, password: str, role: str, trainer_requested: bool = False
) -> dict[str, Any]:
    username = normalize_username(username)
    if not valid_username(username):
        raise ValueError(
            "Benutzername: 3–32 Zeichen, nur Kleinbuchstaben, Ziffern sowie . _ - "
            "und Anfang/Ende mit Buchstabe oder Ziffer."
        )
    if role not in ROLES:
        raise ValueError("Unbekannte Rolle.")
    validate_password(password)
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    user = {
        "username": username,
        "role": role,
        "active": True,
        # Trainer-Konten starten als Teilnehmer, bis ein Administrator sie freischaltet.
        "trainer_requested": trainer_requested and role == "teilnehmer",
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    set_password(user, password)
    path = user_path(username)
    try:
        # O_EXCL: Legen zwei Anfragen gleichzeitig denselben Namen an, gewinnt nur eine.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    except FileExistsError:
        raise ValueError("Dieser Benutzername ist bereits vergeben.") from None
    except OSError:
        raise ValueError(
            "Der Benutzerordner ist nicht beschreibbar. Bitte USER_DIR und Dateirechte prüfen."
        ) from None
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(json_text(user))
    return user


def delete_user_document(username: str) -> bool:
    path = user_path(username)
    if path is None or not path.is_file():
        return False
    path.unlink()
    return True


@lru_cache(maxsize=1)
def dummy_password_hash() -> str:
    return generate_password_hash(secrets.token_urlsafe(16))


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    user = read_user(normalize_username(username))
    if user is None:
        # Gleiche Rechenzeit wie bei einem echten Konto verrät keine Benutzernamen.
        check_password_hash(dummy_password_hash(), password)
        return None
    if not check_password_hash(str(user.get("password_hash", "")), password):
        return None
    return user if user.get("active") else None


def trainer_requests() -> list[dict[str, Any]]:
    return [user for user in list_users() if user.get("trainer_requested") and user["role"] == "teilnehmer"]


def active_admin_count() -> int:
    return sum(1 for user in list_users() if user["role"] == "admin" and user.get("active"))


def is_admin(user: dict[str, Any] | None) -> bool:
    return user is not None and user["role"] == "admin"


def can_upload(user: dict[str, Any] | None) -> bool:
    return user is not None and user["role"] in CONTENT_ROLES


def can_manage_video(user: dict[str, Any] | None, video: dict[str, Any]) -> bool:
    if is_admin(user):
        return True
    return can_upload(user) and video.get("owner") == user["username"]


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
        sort = request.args.get("sort", "newest")
        if sort not in SORT_OPTIONS:
            sort = "newest"
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
        if language in LANGUAGES:
            videos = [video for video in videos if video.get("language") == language]
        # Zähler je Thema beziehen Suche und Sprache mit ein, aber nicht das gewählte Thema.
        topic_counts = {name: 0 for name in topics}
        for video in videos:
            if video.get("topic") in topic_counts:
                topic_counts[video["topic"]] += 1
        total = len(videos)
        if topic:
            videos = [video for video in videos if video.get("topic") == topic]
        # list_videos() liefert bereits "neueste zuerst".
        if sort == "oldest":
            videos.reverse()
        elif sort == "title":
            videos.sort(key=lambda video: str(video.get("title", "")).casefold())
        elif sort == "duration":
            videos.sort(key=lambda video: int(video.get("duration_seconds") or 0))
        return render_template(
            "catalog.html",
            videos=videos,
            topics=topics,
            topic_counts=topic_counts,
            total=total,
            filters={"q": query, "topic": topic, "language": language, "sort": sort},
        )

    @app.get("/video/<video_id>")
    def video_detail(video_id: str):
        video = read_video(video_id)
        if video is None:
            abort(404)
        return render_template("video_detail.html", video=video)

    def accel_redirect(directory_key: str, internal_prefix: str, filename: str, mimetype: str):
        # Flask prüft nur die Anmeldung; Nginx liefert die Datei über eine interne Location aus.
        resolved = safe_join(str(app.config[directory_key]), filename)
        if resolved is None or not Path(resolved).is_file():
            abort(404)
        response = app.response_class(mimetype=mimetype)
        response.headers["X-Accel-Redirect"] = internal_prefix + quote(filename)
        return response

    @app.get("/media/videos/<path:filename>")
    def serve_video(filename: str):
        if app.config["MEDIA_X_ACCEL"]:
            return accel_redirect("VIDEO_DIR", "/_protected/videos/", filename, "video/mp4")
        response = send_from_directory(
            app.config["VIDEO_DIR"], filename, conditional=True, mimetype="video/mp4"
        )
        response.headers.setdefault("Accept-Ranges", "bytes")
        return response

    @app.get("/media/thumbnails/<path:filename>")
    def serve_thumbnail(filename: str):
        if app.config["MEDIA_X_ACCEL"]:
            return accel_redirect("THUMBNAIL_DIR", "/_protected/thumbnails/", filename, "image/jpeg")
        return send_from_directory(app.config["THUMBNAIL_DIR"], filename, conditional=True)

    @app.get("/admin", strict_slashes=False)
    @app.get("/admin/login", strict_slashes=False)
    def former_admin_redirect():
        return redirect(url_for("manage_videos"))

    @app.get("/manage", strict_slashes=False)
    @role_required(*CONTENT_ROLES)
    def manage_videos():
        videos = [video for video in list_videos() if can_manage_video(g.user, video)]
        return render_template("manage/dashboard.html", videos=videos)

    def managed_video_or_abort(video_id: str) -> dict[str, Any]:
        video = read_video(video_id)
        if video is None:
            abort(404)
        if not can_manage_video(g.user, video):
            abort(403)
        return video

    def owner_choices() -> list[str]:
        return [user["username"] for user in list_users() if user["role"] in CONTENT_ROLES]

    @app.route("/upload", methods=["GET", "POST"])
    @role_required(*CONTENT_ROLES)
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
                    "owner": g.user["username"],
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
    @role_required(*CONTENT_ROLES)
    def edit_video(video_id: str):
        video = managed_video_or_abort(video_id)
        owners = owner_choices() if is_admin(g.user) else []
        if request.method == "POST":
            new_video = None
            new_thumbnail = None
            old_video = video.get("video_path")
            old_thumbnail = video.get("thumbnail_path")
            try:
                values = form_values()
                if is_admin(g.user):
                    owner = request.form.get("owner", "")
                    if owner and owner not in owners and owner != video.get("owner"):
                        raise ValueError("Als Besitzer ist nur ein Trainer oder Administrator möglich.")
                    values["owner"] = owner or None
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
        return render_template("manage/video_form.html", video=video, mode="edit", owners=owners)

    @app.post("/manage/videos/<video_id>/delete")
    @role_required(*CONTENT_ROLES)
    def delete_video(video_id: str):
        video = managed_video_or_abort(video_id)
        delete_video_document(video_id)
        remove_file(app.config["VIDEO_DIR"], video.get("video_path"))
        remove_file(app.config["THUMBNAIL_DIR"], video.get("thumbnail_path"))
        flash("Video, Vorschaubild und JSON-Metadaten wurden gelöscht.", "success")
        return redirect(url_for("manage_videos"))


def register_user_routes(app: Flask) -> None:
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user is not None:
            return redirect(safe_next(request.args.get("next")))
        if request.method == "POST":
            user = authenticate(request.form.get("username", ""), request.form.get("password", ""))
            if user is None:
                flash("Benutzername oder Passwort ist falsch, oder das Konto ist gesperrt.", "error")
            else:
                log_in(user)
                flash(f"Willkommen, {user['username']}!", "success")
                return redirect(safe_next(request.args.get("next")))
        return render_template("auth/login.html", no_users=not list_users())

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if not app.config["ALLOW_REGISTRATION"]:
            abort(404)
        if g.user is not None:
            return redirect(url_for("catalog"))
        if request.method == "POST":
            try:
                account_type = request.form.get("account_type", "teilnehmer")
                if account_type not in {"teilnehmer", "trainer"}:
                    raise ValueError("Bitte Teilnehmer oder Trainer auswählen.")
                password = request.form.get("password", "")
                validate_password(password, request.form.get("password_confirm", ""))
                user = create_user(
                    request.form.get("username", ""),
                    password,
                    "teilnehmer",
                    trainer_requested=account_type == "trainer",
                )
            except ValueError as error:
                flash(str(error), "error")
            else:
                log_in(user)
                if user["trainer_requested"]:
                    flash(
                        "Konto wurde angelegt. Die Trainer-Rechte muss ein Administrator noch "
                        "freischalten; bis dahin kannst du Videos als Teilnehmer ansehen.",
                        "warning",
                    )
                else:
                    flash("Konto wurde angelegt. Du bist als Teilnehmer angemeldet.", "success")
                return redirect(url_for("catalog"))
        return render_template("auth/register.html")

    @app.post("/logout")
    def logout():
        session.clear()
        flash("Du wurdest abgemeldet.", "success")
        return redirect(url_for("login"))

    @app.route("/account", methods=["GET", "POST"])
    def account():
        if request.method == "POST":
            try:
                if not check_password_hash(
                    str(g.user.get("password_hash", "")), request.form.get("current_password", "")
                ):
                    raise ValueError("Das aktuelle Passwort ist falsch.")
                password = request.form.get("password", "")
                validate_password(password, request.form.get("password_confirm", ""))
                # Frisch einlesen: Hat ein Administrator das Konto während der (langsamen)
                # Passwortprüfung gesperrt, geändert oder gelöscht, darf das nicht überschrieben werden.
                user = current_user_from_session()
                if user is None:
                    session.clear()
                    return redirect(url_for("login"))
                set_password(user, password)
                user["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                write_user(user)
            except ValueError as error:
                flash(str(error), "error")
            else:
                log_in(user)
                flash("Passwort wurde geändert. Andere Sitzungen wurden abgemeldet.", "success")
                return redirect(url_for("account"))
        return render_template("auth/account.html")

    @app.route("/users", methods=["GET", "POST"])
    @role_required("admin")
    def manage_users():
        if request.method == "POST":
            try:
                user = create_user(
                    request.form.get("username", ""),
                    request.form.get("password", ""),
                    request.form.get("role", ""),
                )
            except ValueError as error:
                flash(str(error), "error")
            else:
                flash(f"Benutzer „{user['username']}“ wurde angelegt.", "success")
                return redirect(url_for("manage_users"))
        return render_template("users/list.html", users=list_users(), requests=trainer_requests())

    @app.post("/users/<username>/trainer-request")
    @role_required("admin")
    def decide_trainer_request(username: str):
        user = user_or_abort(username)
        if not user.get("trainer_requested") or user["role"] != "teilnehmer":
            flash(f"Für „{user['username']}“ gibt es keine offene Trainer-Anfrage.", "error")
            return redirect(url_for("manage_users"))
        approve = request.form.get("decision") == "approve"
        if approve:
            user["role"] = "trainer"
        user["trainer_requested"] = False
        user["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        write_user(user)
        if approve:
            flash(f"„{user['username']}“ ist jetzt Trainer.", "success")
        else:
            flash(f"Trainer-Anfrage von „{user['username']}“ wurde abgelehnt; das Konto bleibt Teilnehmer.", "success")
        return redirect(url_for("manage_users"))

    def user_or_abort(username: str) -> dict[str, Any]:
        user = read_user(username)
        if user is None:
            abort(404)
        return user

    @app.route("/users/<username>/edit", methods=["GET", "POST"])
    @role_required("admin")
    def edit_user(username: str):
        user = user_or_abort(username)
        is_self = user["username"] == g.user["username"]
        if request.method == "POST":
            try:
                role = request.form.get("role", "")
                active = request.form.get("active") == "1"
                if role not in ROLES:
                    raise ValueError("Unbekannte Rolle.")
                if is_self and (role != "admin" or not active):
                    raise ValueError("Die eigene Rolle und Sperre kannst du nicht ändern.")
                if (
                    user["role"] == "admin"
                    and user.get("active")
                    and (role != "admin" or not active)
                    and active_admin_count() <= 1
                ):
                    raise ValueError("Es muss mindestens ein aktiver Administrator bleiben.")
                password = request.form.get("password", "")
                if password:
                    validate_password(password)
                    set_password(user, password)
                elif user.get("active") and not active:
                    # Sperren beendet offene Sitzungen dauerhaft, auch nach dem Entsperren.
                    user["session_key"] = secrets.token_urlsafe(16)
                if role != "teilnehmer":
                    user["trainer_requested"] = False
                user["role"] = role
                user["active"] = active
                user["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                write_user(user)
            except ValueError as error:
                flash(str(error), "error")
            else:
                if is_self:
                    log_in(user)
                flash(f"Benutzer „{user['username']}“ wurde gespeichert.", "success")
                return redirect(url_for("manage_users"))
        return render_template("users/edit.html", user=user, is_self=is_self)

    @app.post("/users/<username>/delete")
    @role_required("admin")
    def delete_user(username: str):
        user = user_or_abort(username)
        if user["username"] == g.user["username"]:
            flash("Das eigene Konto kann nicht gelöscht werden.", "error")
            return redirect(url_for("manage_users"))
        if user["role"] == "admin" and user.get("active") and active_admin_count() <= 1:
            flash("Es muss mindestens ein aktiver Administrator bleiben.", "error")
            return redirect(url_for("manage_users"))
        delete_user_document(user["username"])
        # Videos bleiben erhalten; ohne Besitzer kann sie nur noch ein Administrator verwalten.
        for video in list_videos():
            if video.get("owner") == user["username"]:
                video["owner"] = None
                write_video(video)
        flash(f"Benutzer „{user['username']}“ wurde gelöscht.", "success")
        return redirect(url_for("manage_users"))


def register_cli(app: Flask) -> None:
    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--role", type=click.Choice(list(ROLES)), default="admin", show_default=True)
    @click.password_option("--password", prompt="Passwort", confirmation_prompt="Passwort wiederholen")
    def create_user_command(username: str, role: str, password: str) -> None:
        """Legt einen Benutzer an, z. B. den ersten Administrator."""
        try:
            user = create_user(username, password, role)
        except ValueError as error:
            raise click.ClickException(str(error)) from None
        click.echo(f"Benutzer „{user['username']}“ ({ROLES[role]}) wurde angelegt.")

    @app.cli.command("set-password")
    @click.argument("username")
    @click.password_option("--password", prompt="Neues Passwort", confirmation_prompt="Passwort wiederholen")
    def set_password_command(username: str, password: str) -> None:
        """Setzt das Passwort eines Benutzers neu und entsperrt das Konto."""
        user = read_user(normalize_username(username))
        if user is None:
            raise click.ClickException("Benutzer nicht gefunden.")
        try:
            validate_password(password)
        except ValueError as error:
            raise click.ClickException(str(error)) from None
        set_password(user, password)
        user["active"] = True
        user["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        write_user(user)
        click.echo(f"Passwort für „{user['username']}“ wurde gesetzt.")


def register_errors(app: Flask) -> None:
    @app.errorhandler(400)
    def bad_request(error):
        return render_template("error.html", code=400, message=error.description), 400

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template(
            "error.html", code=403, message="Für diese Seite fehlt dir die Berechtigung."
        ), 403

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
