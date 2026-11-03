#!/usr/bin/env bash
# Sichert Videos, Vorschaubilder, Metadaten, Benutzer und .env als .tar.gz.
# Aufruf am Pi:  bash /home/pi/medienserver/deployment/backup.sh
# Einstellungen per Umgebungsvariable, z. B.:  KEEP=5 bash deployment/backup.sh
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/pi/medienserver}"
BACKUP_DIR="${BACKUP_DIR:-/home/pi/backups}"
KEEP="${KEEP:-3}"   # so viele Sicherungen bleiben liegen, aeltere werden geloescht

cd "$PROJECT_DIR"
for item in data uploads .env; do
    if [ ! -e "$item" ]; then
        echo "Fehler: $PROJECT_DIR/$item fehlt, Backup abgebrochen." >&2
        exit 1
    fi
done

mkdir -p "$BACKUP_DIR"

# Grob pruefen, ob genug Platz frei ist (Groesse der Daten + 10 %).
needed_kb=$(du -sk data uploads .env | awk '{s+=$1} END {print int(s*1.1)}')
free_kb=$(df -Pk "$BACKUP_DIR" | awk 'NR==2 {print $4}')
if [ "$free_kb" -lt "$needed_kb" ]; then
    echo "Fehler: zu wenig Speicher frei (benoetigt ca. $((needed_kb/1024)) MB, frei $((free_kb/1024)) MB)." >&2
    exit 1
fi

stamp=$(date +%Y-%m-%d_%H-%M)
target="$BACKUP_DIR/medienserver_$stamp.tar.gz"

tar -czf "$target.tmp" data uploads .env
mv "$target.tmp" "$target"
chmod 600 "$target"   # enthaelt den SECRET_KEY

# Alte Sicherungen loeschen, die neuesten $KEEP behalten.
ls -1t "$BACKUP_DIR"/medienserver_*.tar.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm --

echo "$(date '+%F %T') Backup erstellt: $target ($(du -h "$target" | cut -f1))"
