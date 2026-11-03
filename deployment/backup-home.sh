#!/usr/bin/env bash
# Backup und Wiederherstellung für den Heimserver (Docker, compose.home.yaml).
#
#   sudo bash /opt/medienserver/deployment/backup-home.sh                 Backup erstellen
#   sudo bash /opt/medienserver/deployment/backup-home.sh check ARCHIV    Archiv prüfen, ändert nichts
#   sudo bash /opt/medienserver/deployment/backup-home.sh restore ARCHIV  Archiv zurückspielen
#
# Gesichert werden data/ (Video-JSONs, Benutzer), uploads/ (Videos, Vorschaubilder),
# caddy/ (Zertifikate), .env und /etc/ddclient.conf (Dynamic DNS).
#
# Konsistenz bei laufenden Uploads: Zuerst wird bei laufender App in einen Zwischenstand
# kopiert (dauert). Dann wird die App kurz gestoppt – laufende Uploads dürfen noch fertig
# werden (stop_grace_period) –, nur noch die Änderungen nachkopiert und die App sofort
# wieder gestartet. Erst danach wird das Archiv gepackt.
#
# Einstellungen per Umgebungsvariable, z. B.:  sudo KEEP=3 bash deployment/backup-home.sh
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/medienserver}"
BACKUP_DIR="${BACKUP_DIR:-/home/michael/medienserver-backups}"
BACKUP_OWNER="${BACKUP_OWNER-michael}"    # darf die Archive per scp abholen; leer = nur root
KEEP="${KEEP:-7}"                         # so viele Archive bleiben liegen (täglich = 7 Tage)
DDCLIENT_CONF="${DDCLIENT_CONF:-/etc/ddclient.conf}"   # Dynamic DNS (Namecheap) am Host
STAGING="$BACKUP_DIR/.staging"
COMPOSE=(docker compose --project-directory "$PROJECT_DIR" -f "$PROJECT_DIR/compose.home.yaml")

fail() {
    echo "Fehler: $*" >&2
    exit 1
}

log() {
    echo "$(date '+%F %T') $*"
}

# Hält die App an, egal ob sie schon in Docker oder noch als systemd-Dienst läuft.
# pause_app all stoppt in Docker alle Container: Beim Restore werden Ordner ausgetauscht,
# und laufende Container würden sonst weiter die alten (verschobenen) Ordner verwenden.
paused=""
pause_app() {
    if command -v docker >/dev/null \
        && [ -n "$("${COMPOSE[@]}" ps --status running -q app 2>/dev/null)" ]; then
        if [ "${1:-}" = all ]; then
            "${COMPOSE[@]}" stop >/dev/null 2>&1
        else
            "${COMPOSE[@]}" stop app >/dev/null 2>&1
        fi
        paused="docker"
    elif systemctl is-active --quiet medienserver 2>/dev/null; then
        systemctl stop medienserver
        paused="systemd"
    fi
}

resume_app() {
    case "$paused" in
        docker) "${COMPOSE[@]}" up -d >/dev/null 2>&1 ;;
        systemd) systemctl start medienserver ;;
    esac
    paused=""
}
# Auch bei einem Fehler mitten im Backup läuft die App danach wieder.
trap resume_app EXIT

sync_to_staging() {
    mkdir -p "$STAGING/project"
    local item
    for item in data uploads caddy .env; do
        if [ -e "$PROJECT_DIR/$item" ]; then
            rsync -a --delete --numeric-ids "$PROJECT_DIR/$item" "$STAGING/project/"
        fi
    done
    if [ -f "$DDCLIENT_CONF" ]; then
        mkdir -p "$STAGING/etc"
        rsync -a "$DDCLIENT_CONF" "$STAGING/etc/ddclient.conf"
    fi
}

write_manifest() {
    {
        echo "erstellt: $(date '+%F %T')"
        echo "git-commit: $(git -c safe.directory="$PROJECT_DIR" -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo unbekannt)"
        echo "video-json: $(find "$STAGING/project/data/videos" -name '*.json' | wc -l)"
        echo "benutzer: $(find "$STAGING/project/data/users" -name '*.json' | wc -l)"
        echo "videodateien: $(find "$STAGING/project/uploads/videos" -type f | wc -l)"
        if command -v docker >/dev/null; then
            echo "images:"
            "${COMPOSE[@]}" images 2>/dev/null | sed 's/^/  /' || true
        fi
    } > "$STAGING/MANIFEST.txt"
}

backup() {
    [[ "$KEEP" =~ ^[1-9][0-9]*$ ]] || fail "KEEP muss eine Zahl ab 1 sein."
    for item in data/videos data/users uploads/videos uploads/thumbnails .env; do
        [ -e "$PROJECT_DIR/$item" ] || fail "$PROJECT_DIR/$item fehlt, Backup abgebrochen."
    done
    mkdir -p "$BACKUP_DIR"
    install -d -m 700 "$STAGING"

    # Grob prüfen, ob genug Platz frei ist: ein Archiv + was im Zwischenstand noch fehlt.
    local source_kb staging_kb needed_kb free_kb
    source_kb=$(cd "$PROJECT_DIR" && du -sk data uploads .env | awk '{s+=$1} END {print s}')
    staging_kb=$(du -sk "$STAGING" | awk '{print $1}')
    needed_kb=$(( source_kb * 11 / 10 + (source_kb > staging_kb ? source_kb - staging_kb : 0) ))
    free_kb=$(df -Pk "$BACKUP_DIR" | awk 'NR==2 {print $4}')
    if [ "$free_kb" -lt "$needed_kb" ]; then
        fail "zu wenig Speicher frei (benötigt ca. $((needed_kb / 1024)) MB, frei $((free_kb / 1024)) MB)."
    fi

    log "Kopiere bei laufender App ..."
    sync_to_staging
    log "App kurz angehalten, kopiere Änderungen ..."
    pause_app
    sync_to_staging
    resume_app
    log "App läuft wieder."

    write_manifest
    local stamp target members
    stamp=$(date +%Y-%m-%d_%H-%M)
    target="$BACKUP_DIR/medienserver-home_$stamp.tar"
    members=(MANIFEST.txt project)
    [ -f "$STAGING/etc/ddclient.conf" ] && members+=(etc)
    tar --numeric-owner -cf "$target.tmp" -C "$STAGING" "${members[@]}"
    mv "$target.tmp" "$target"
    chmod 600 "$target"   # enthält SECRET_KEY und DNS-Zugangsdaten
    [ -n "$BACKUP_OWNER" ] && chown "$BACKUP_OWNER" "$target"

    # Ältere Archive löschen, die neuesten $KEEP behalten.
    ls -1t "$BACKUP_DIR"/medienserver-home_*.tar 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm --

    log "Backup erstellt: $target ($(du -h "$target" | cut -f1))"
}

# Prüft ein Archiv, ohne laufende Daten anzufassen: lesbar, vollständig, jede Video-JSON
# verweist auf vorhandene Video- und Bilddateien, Benutzer sind gültig.
check() {
    local archive="$1" work
    [ -f "$archive" ] || fail "Archiv $archive nicht gefunden."
    work=$(mktemp -d)
    # shellcheck disable=SC2064
    trap "rm -rf '$work'; resume_app" EXIT

    tar -tf "$archive" > "$work/members" || fail "Archiv ist beschädigt oder unvollständig."
    for member in MANIFEST.txt project/.env project/data/videos/ project/data/users/; do
        grep -qxF "$member" "$work/members" || fail "Im Archiv fehlt $member."
    done
    tar -xf "$archive" -C "$work" MANIFEST.txt project/data project/.env
    grep -q '^SECRET_KEY=.' "$work/project/.env" || fail "project/.env enthält keinen SECRET_KEY."

    python3 - "$work" <<'PY'
import json
import sys
from pathlib import Path

work = Path(sys.argv[1])
members = set((work / "members").read_text(encoding="utf-8").splitlines())
problems = []
videos = sorted((work / "project/data/videos").glob("*.json"))
for path in videos:
    try:
        video = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        problems.append(f"{path.name}: kein gültiges JSON")
        continue
    if video.get("id") != path.stem:
        problems.append(f"{path.name}: id passt nicht zum Dateinamen")
    for key, folder in (("video_path", "videos"), ("thumbnail_path", "thumbnails")):
        if f"project/uploads/{folder}/{video.get(key)}" not in members:
            problems.append(f"{path.name}: {folder}/{video.get(key)} fehlt im Archiv")
users = []
for path in sorted((work / "project/data/users").glob("*.json")):
    try:
        user = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        problems.append(f"{path.name}: kein gültiges JSON")
        continue
    if user.get("username") != path.stem or not user.get("password_hash"):
        problems.append(f"{path.name}: unvollständiges Benutzerkonto")
    users.append(user)
admins = [u for u in users if u.get("role") == "admin" and u.get("active")]
print(f"Videos: {len(videos)}, Benutzer: {len(users)}, aktive Administratoren: {len(admins)}")
if not admins:
    problems.append("kein aktiver Administrator")
for problem in problems:
    print("Problem:", problem)
sys.exit(1 if problems else 0)
PY
    sed 's/^/  /' "$work/MANIFEST.txt"
    log "Archiv ist in Ordnung: $archive"
}

# Spielt ein Archiv zurück. Nichts wird gelöscht: der bisherige Stand wandert nach
# $PROJECT_DIR/vor-restore-<Zeitpunkt>/ und kann nach erfolgreicher Prüfung von Hand entfernt werden.
restore() {
    local archive="$1" stamp aside item
    ( check "$archive" ) || fail "Archiv nicht in Ordnung, Wiederherstellung abgebrochen."
    stamp=$(date +%Y-%m-%d_%H-%M-%S)
    aside="$PROJECT_DIR/vor-restore-$stamp"

    pause_app all
    install -d -m 700 "$aside"
    for item in data uploads caddy .env; do
        if [ -e "$PROJECT_DIR/$item" ]; then
            mv "$PROJECT_DIR/$item" "$aside/"
        fi
    done
    tar --numeric-owner -xpf "$archive" -C "$PROJECT_DIR" --strip-components=1 project
    # ddclient-Konfiguration nur auf einem neuen Server zurückschreiben, nie eine vorhandene ersetzen.
    if tar -tf "$archive" etc/ddclient.conf >/dev/null 2>&1 && [ ! -e "$DDCLIENT_CONF" ]; then
        install -m 600 /dev/null "$DDCLIENT_CONF"
        tar -xOf "$archive" etc/ddclient.conf > "$DDCLIENT_CONF"
        log "ddclient-Konfiguration nach $DDCLIENT_CONF zurückgeschrieben."
    fi
    resume_app
    log "Wiederhergestellt aus $archive."
    log "Bisheriger Stand liegt in $aside (erst löschen, wenn alles geprüft ist)."
}

[ "$(id -u)" -eq 0 ] || fail "bitte mit sudo ausführen."
case "${1:-backup}" in
    backup) backup ;;
    check) check "${2:?Archiv angeben}" ;;
    restore) restore "${2:?Archiv angeben}" ;;
    *) fail "Aufruf: $0 [backup | check ARCHIV | restore ARCHIV]" ;;
esac
