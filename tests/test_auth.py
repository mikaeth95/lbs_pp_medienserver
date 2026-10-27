import json
from pathlib import Path

import pytest

import app as app_module
from conftest import PASSWORD, csrf_from, login, post_upload


def only_video(app):
    documents = sorted(Path(app.config["METADATA_DIR"]).glob("*.json"))
    assert len(documents) == 1
    return json.loads(documents[0].read_text(encoding="utf-8"))


def user_document(app, username):
    return json.loads(Path(app.config["USER_DIR"], f"{username}.json").read_text(encoding="utf-8"))


def post_form(client, url, data, token_page=None):
    token = csrf_from(client.get(token_page or url))
    return client.post(url, data={"csrf_token": token, **data}, follow_redirects=True)


@pytest.mark.parametrize(
    "url", ["/", "/video/abc", "/media/videos/x.mp4", "/media/thumbnails/x.jpg", "/manage", "/upload", "/users", "/account"]
)
def test_anonymous_visitors_are_sent_to_login(app, url):
    response = app.test_client().get(url)
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=")


def test_login_rejects_wrong_password_and_stores_only_hashes(app):
    client = app.test_client()
    response = login(client, "trainer", "falsches-passwort")
    assert "falsch" in response.text
    assert client.get("/").status_code == 302
    stored = user_document(app, "trainer")
    assert PASSWORD not in json.dumps(stored)
    assert stored["password_hash"].startswith(("scrypt:", "pbkdf2:"))


def test_login_is_case_insensitive_and_follows_safe_next(app):
    client = app.test_client()
    token = csrf_from(client.get("/login"))
    response = client.post(
        "/login?next=/manage",
        data={"csrf_token": token, "username": " Trainer ", "password": PASSWORD},
    )
    assert response.headers["Location"] == "/manage"


@pytest.mark.parametrize("target", ["//evil.example", "https://evil.example", "/\\evil.example"])
def test_login_ignores_foreign_redirect_targets(app, target):
    client = app.test_client()
    token = csrf_from(client.get("/login"))
    response = client.post(
        "/login",
        query_string={"next": target},
        data={"csrf_token": token, "username": "trainer", "password": PASSWORD},
    )
    assert response.headers["Location"] == "/"


def test_logout_requires_post_and_ends_session(login_as):
    client = login_as("schueler")
    assert client.get("/logout").status_code == 405
    token = csrf_from(client.get("/"))
    client.post("/logout", data={"csrf_token": token})
    assert client.get("/").status_code == 302


def test_registration_creates_participant(app):
    client = app.test_client()
    response = post_form(
        client,
        "/register",
        {"username": "Neu.Schueler", "password": "passwort-1", "password_confirm": "passwort-1"},
    )
    assert "als Teilnehmer angemeldet" in response.text
    assert user_document(app, "neu.schueler")["role"] == "teilnehmer"
    assert client.get("/upload").status_code == 403


@pytest.mark.parametrize(
    "username, password, confirm, message",
    [
        ("trainer", "passwort-1", "passwort-1", "bereits vergeben"),
        ("ab", "passwort-1", "passwort-1", "Benutzername"),
        ("../boese", "passwort-1", "passwort-1", "Benutzername"),
        ("con", "passwort-1", "passwort-1", "Benutzername"),
        ("gueltig", "kurz", "kurz", "mindestens 8"),
        ("gueltig", "passwort-1", "passwort-2", "stimmen nicht"),
    ],
)
def test_registration_validates_input(app, username, password, confirm, message):
    response = post_form(
        app.test_client(),
        "/register",
        {"username": username, "password": password, "password_confirm": confirm},
    )
    assert message in response.text
    assert not Path(app.config["USER_DIR"], "gueltig.json").exists()


def test_registration_can_be_disabled(app):
    app.config["ALLOW_REGISTRATION"] = False
    client = app.test_client()
    assert client.get("/register").status_code == 404
    assert "Registrieren" not in client.get("/login").text


def test_participant_can_watch_but_not_manage(app, login_as, upload_video):
    upload_video()
    video = only_video(app)
    schueler = login_as("schueler")
    catalog = schueler.get("/")
    assert "Excel Grundlagen" in catalog.text
    assert "Video hochladen" not in catalog.text
    assert schueler.get(f"/video/{video['id']}").status_code == 200
    assert schueler.get(f"/media/videos/{video['video_path']}").status_code == 200
    assert schueler.get("/upload").status_code == 403
    assert schueler.get("/manage").status_code == 403
    assert schueler.get("/users").status_code == 403
    assert schueler.get(f"/manage/videos/{video['id']}/edit").status_code == 403
    token = csrf_from(schueler.get("/"))
    response = schueler.post(f"/manage/videos/{video['id']}/delete", data={"csrf_token": token})
    assert response.status_code == 403
    assert only_video(app)["id"] == video["id"]


def test_trainer_manages_only_own_videos(app, client, login_as, upload_video):
    upload_video(title="Video von Trainer")
    video = only_video(app)
    assert video["owner"] == "trainer"
    assert client.get("/users").status_code == 403

    other = login_as("trainer2")
    assert "Video von Trainer" not in other.get("/manage").text
    assert other.get(f"/manage/videos/{video['id']}/edit").status_code == 403
    token = csrf_from(other.get("/manage"))
    assert other.post(f"/manage/videos/{video['id']}/delete", data={"csrf_token": token}).status_code == 403
    assert "Video bearbeiten" not in other.get(f"/video/{video['id']}").text

    assert "Video von Trainer" in client.get("/manage").text
    assert "Video bearbeiten" in client.get(f"/video/{video['id']}").text


def test_admin_manages_foreign_videos_and_reassigns_owner(app, login_as, upload_video):
    upload_video()
    video = only_video(app)
    admin = login_as("admin")
    assert "Excel Grundlagen" in admin.get("/manage").text
    response = post_form(
        admin,
        f"/manage/videos/{video['id']}/edit",
        {"title": "Neu", "topic": "Office", "language": "DE", "owner": "trainer2"},
    )
    assert response.status_code == 200
    assert only_video(app)["owner"] == "trainer2"

    response = post_form(
        admin,
        f"/manage/videos/{video['id']}/edit",
        {"title": "Neu", "topic": "Office", "language": "DE", "owner": "schueler"},
    )
    assert "nur ein Trainer oder Administrator" in response.text
    assert only_video(app)["owner"] == "trainer2"

    response = post_form(admin, f"/manage/videos/{video['id']}/delete", {}, token_page="/manage")
    assert response.status_code == 200
    assert not list(Path(app.config["METADATA_DIR"]).glob("*.json"))


def test_trainer_cannot_set_owner_field(app, client, upload_video):
    upload_video()
    video = only_video(app)
    post_form(
        client,
        f"/manage/videos/{video['id']}/edit",
        {"title": "Neu", "topic": "Office", "language": "DE", "owner": "trainer2"},
    )
    assert only_video(app)["owner"] == "trainer"


def test_videos_without_owner_are_admin_only(app, client, login_as, upload_video):
    upload_video()
    video = only_video(app)
    video.pop("owner")
    Path(app.config["METADATA_DIR"], f"{video['id']}.json").write_text(json.dumps(video), encoding="utf-8")
    assert client.get(f"/manage/videos/{video['id']}/edit").status_code == 403
    assert login_as("admin").get(f"/manage/videos/{video['id']}/edit").status_code == 200


def test_admin_creates_and_edits_users(app, login_as):
    admin = login_as("admin")
    response = post_form(admin, "/users", {"username": "lehrkraft", "password": "start-pass-1", "role": "trainer"})
    assert "wurde angelegt" in response.text
    assert user_document(app, "lehrkraft")["role"] == "trainer"

    neu = app.test_client()
    login(neu, "lehrkraft", "start-pass-1")
    assert neu.get("/upload").status_code == 200

    post_form(admin, "/users/lehrkraft/edit", {"role": "teilnehmer", "active": "1", "password": ""})
    assert user_document(app, "lehrkraft")["role"] == "teilnehmer"
    assert neu.get("/upload").status_code == 403

    post_form(admin, "/users/lehrkraft/edit", {"role": "teilnehmer", "password": ""})
    assert user_document(app, "lehrkraft")["active"] is False
    assert neu.get("/").status_code == 302
    assert "gesperrt" in login(app.test_client(), "lehrkraft", "start-pass-1").text


def test_admin_password_reset_logs_user_out_everywhere(app, login_as):
    schueler = login_as("schueler")
    admin = login_as("admin")
    post_form(admin, "/users/schueler/edit", {"role": "teilnehmer", "active": "1", "password": "neues-pass-1"})
    assert schueler.get("/").status_code == 302
    assert "Willkommen" in login(app.test_client(), "schueler", "neues-pass-1").text


def test_admin_cannot_lock_out_themselves(app, login_as):
    admin = login_as("admin")
    response = post_form(admin, "/users/admin/edit", {"role": "trainer", "active": "1", "password": ""})
    assert "eigene Rolle" in response.text
    response = post_form(admin, "/users/admin/edit", {"role": "admin", "password": ""})
    assert "eigene Rolle" in response.text
    response = post_form(admin, "/users/admin/delete", {}, token_page="/users")
    assert "nicht gelöscht" in response.text
    stored = user_document(app, "admin")
    assert stored["role"] == "admin" and stored["active"] is True


def test_deleting_user_keeps_videos_without_owner(app, login_as, upload_video):
    upload_video()
    admin = login_as("admin")
    response = post_form(admin, "/users/trainer/delete", {}, token_page="/users")
    assert "wurde gelöscht" in response.text
    assert not Path(app.config["USER_DIR"], "trainer.json").exists()
    assert only_video(app)["owner"] is None


def test_deleted_user_session_ends(app, login_as):
    schueler = login_as("schueler")
    Path(app.config["USER_DIR"], "schueler.json").unlink()
    assert schueler.get("/").status_code == 302
    with app.app_context():
        app_module.create_user("schueler", PASSWORD, "admin")
    assert schueler.get("/users").status_code == 302


def test_user_changes_own_password(app, login_as):
    first = login_as("schueler")
    second = login_as("schueler")
    response = post_form(
        first,
        "/account",
        {"current_password": "falsch-falsch", "password": "neues-pass-1", "password_confirm": "neues-pass-1"},
    )
    assert "aktuelle Passwort ist falsch" in response.text
    response = post_form(
        first,
        "/account",
        {"current_password": PASSWORD, "password": "neues-pass-1", "password_confirm": "neues-pass-1"},
    )
    assert "Passwort wurde geändert" in response.text
    assert first.get("/").status_code == 200
    assert second.get("/").status_code == 302


def test_x_accel_redirect_hands_media_to_nginx(app, client, upload_video):
    upload_video()
    video = only_video(app)
    app.config["MEDIA_X_ACCEL"] = True
    response = client.get(f"/media/videos/{video['video_path']}")
    assert response.status_code == 200
    assert response.headers["X-Accel-Redirect"] == f"/_protected/videos/{video['video_path']}"
    assert response.headers["Content-Type"] == "video/mp4"
    assert response.data == b""
    thumb = client.get(f"/media/thumbnails/{video['thumbnail_path']}")
    assert thumb.headers["X-Accel-Redirect"] == f"/_protected/thumbnails/{video['thumbnail_path']}"
    assert client.get("/media/videos/fehlt.mp4").status_code == 404
    assert client.get("/media/videos/../users/admin.json").status_code == 404


def test_cli_creates_user_and_resets_password(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["create-user", "chef", "--password", "chef-pass-1"])
    assert result.exit_code == 0, result.output
    assert user_document(app, "chef")["role"] == "admin"
    result = runner.invoke(args=["create-user", "chef", "--password", "chef-pass-1"])
    assert result.exit_code != 0
    assert "bereits vergeben" in result.output
    result = runner.invoke(args=["set-password", "chef", "--password", "anderes-1"])
    assert result.exit_code == 0, result.output
    assert "Willkommen" in login(app.test_client(), "chef", "anderes-1").text


def test_login_page_hints_when_no_users_exist(tmp_path):
    empty = app_module.create_app(
        {
            "TESTING": True,
            "METADATA_DIR": tmp_path / "m",
            "USER_DIR": tmp_path / "u",
            "VIDEO_DIR": tmp_path / "v",
            "THUMBNAIL_DIR": tmp_path / "t",
        }
    )
    assert "create-user" in empty.test_client().get("/login").text


def test_upload_by_admin_records_owner(app, login_as, fake_media):
    admin = login_as("admin")
    post_upload(admin, title="Admin-Video")
    assert only_video(app)["owner"] == "admin"


@pytest.mark.parametrize("target", ["/\t/evil.example", "/\n/evil.example", "/\r\n/x", "/\x7f/evil.example"])
def test_login_ignores_targets_with_control_characters(app, target):
    client = app.test_client()
    token = csrf_from(client.get("/login"))
    response = client.post(
        "/login",
        query_string={"next": target},
        data={"csrf_token": token, "username": "trainer", "password": PASSWORD},
    )
    assert response.headers["Location"] == "/"


def test_dead_session_logout_and_post_redirect_without_next(app, login_as):
    schueler = login_as("schueler")
    token = csrf_from(schueler.get("/"))
    with app.app_context():
        user = app_module.read_user("schueler")
        app_module.set_password(user, "anderes-pass-1")
        app_module.write_user(user)
    response = schueler.post("/logout", data={"csrf_token": token})
    assert response.status_code == 302
    assert response.headers["Location"] == "/login"
    response = schueler.post("/manage/videos/abc/delete", data={"csrf_token": token})
    assert response.headers["Location"] == "/login"


def test_locking_invalidates_sessions_even_after_unlock(app, login_as):
    schueler = login_as("schueler")
    admin = login_as("admin")
    post_form(admin, "/users/schueler/edit", {"role": "teilnehmer", "password": ""})
    post_form(admin, "/users/schueler/edit", {"role": "teilnehmer", "active": "1", "password": ""})
    assert user_document(app, "schueler")["active"] is True
    assert schueler.get("/").status_code == 302


def test_account_change_does_not_revive_deleted_user(app, login_as, monkeypatch):
    schueler = login_as("schueler")
    token = csrf_from(schueler.get("/account"))
    original_check = app_module.check_password_hash

    def check_then_admin_deletes(stored, password):
        result = original_check(stored, password)
        Path(app.config["USER_DIR"], "schueler.json").unlink(missing_ok=True)
        return result

    monkeypatch.setattr(app_module, "check_password_hash", check_then_admin_deletes)
    schueler.post(
        "/account",
        data={
            "csrf_token": token,
            "current_password": PASSWORD,
            "password": "neues-pass-1",
            "password_confirm": "neues-pass-1",
        },
    )
    assert not Path(app.config["USER_DIR"], "schueler.json").exists()


def test_edit_user_form_keeps_unchecked_active_box_after_error(login_as):
    admin = login_as("admin")
    response = post_form(admin, "/users/trainer/edit", {"role": "trainer", "password": "kurz"})
    assert "mindestens 8" in response.text
    assert 'name="active" value="1" checked' not in response.text


def register_as(client, username, account_type):
    return post_form(
        client,
        "/register",
        {
            "username": username,
            "password": "passwort-1",
            "password_confirm": "passwort-1",
            "account_type": account_type,
        },
    )


def test_trainer_registration_waits_for_admin_approval(app, login_as):
    neu = app.test_client()
    response = register_as(neu, "lehrer1", "trainer")
    assert "freischalten" in response.text
    stored = user_document(app, "lehrer1")
    assert stored["role"] == "teilnehmer" and stored["trainer_requested"] is True
    assert neu.get("/").status_code == 200
    assert neu.get("/upload").status_code == 403
    assert "Freischaltung ist beantragt" in neu.get("/account").text

    admin = login_as("admin")
    page = admin.get("/users").text
    assert "Offene Trainer-Anfragen" in page
    assert 'class="nav-count"' in page
    response = post_form(admin, "/users/lehrer1/trainer-request", {"decision": "approve"}, token_page="/users")
    assert "ist jetzt Trainer" in response.text
    stored = user_document(app, "lehrer1")
    assert stored["role"] == "trainer" and stored["trainer_requested"] is False
    assert neu.get("/upload").status_code == 200
    assert "Offene Trainer-Anfragen" not in admin.get("/users").text


def test_rejected_trainer_request_stays_participant(app, login_as):
    register_as(app.test_client(), "lehrer2", "trainer")
    admin = login_as("admin")
    response = post_form(admin, "/users/lehrer2/trainer-request", {"decision": "reject"}, token_page="/users")
    assert "abgelehnt" in response.text
    stored = user_document(app, "lehrer2")
    assert stored["role"] == "teilnehmer" and stored["trainer_requested"] is False
    response = post_form(admin, "/users/lehrer2/trainer-request", {"decision": "approve"}, token_page="/users")
    assert "keine offene Trainer-Anfrage" in response.text
    assert user_document(app, "lehrer2")["role"] == "teilnehmer"


def test_participant_registration_has_no_request(app):
    register_as(app.test_client(), "schueler2", "teilnehmer")
    assert user_document(app, "schueler2")["trainer_requested"] is False


def test_registration_cannot_pick_admin_role(app):
    response = register_as(app.test_client(), "boese", "admin")
    assert "Teilnehmer oder Trainer" in response.text
    assert not Path(app.config["USER_DIR"], "boese.json").exists()


def test_only_admin_decides_trainer_requests(app, login_as):
    register_as(app.test_client(), "lehrer3", "trainer")
    trainer = login_as("trainer")
    token = csrf_from(trainer.get("/"))
    response = trainer.post("/users/lehrer3/trainer-request", data={"csrf_token": token, "decision": "approve"})
    assert response.status_code == 403
    assert user_document(app, "lehrer3")["role"] == "teilnehmer"
