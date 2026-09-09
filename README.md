# Medienserver für Schulungsvideos

Eine kleine deutschsprachige Videothek auf Basis von Flask und JSON-Dokumenten. Sie läuft lokal unter Windows und kann später hinter Nginx und Gunicorn auf einem Raspberry Pi betrieben werden. Videos werden nicht während der Wiedergabe umgewandelt. Erwartet werden vorbereitete MP4-Dateien mit H.264-Video und AAC-Audio.

## Funktionen

- Responsive Videothek mit Suche sowie kombinierbaren Filtern für Thema und Sprache
- HTML5-Player mit Browser-Steuerung und zusätzlichen Sprüngen um 10 Sekunden
- Offener Upload und eine Verwaltung zum Hinzufügen, Bearbeiten und Löschen von Videos
- CSRF-Schutz, sichere Upload-Dateinamen und konfigurierbares Upload-Limit
- Eine eigene JSON-Datei pro Video mit Katalog- und technischen Metadaten; Videos und Vorschaubilder bleiben in getrennten Ordnern
- HTTP-Range-Unterstützung im lokalen Flask-Betrieb; direkte Videoauslieferung durch Nginx auf dem Pi
- Automatische Dauerermittlung mit `ffprobe` und Vorschaubild aus dem ersten Frame mit `ffmpeg`

## Windows: Einrichtung in PowerShell

Voraussetzungen: Python 3.11 oder neuer und ein aktueller Browser. Im Projektordner:

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Öffne `.env` und ersetze `SECRET_KEY` durch einen langen Zufallswert. Einen Wert kannst du so erzeugen:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Die JSON- und Upload-Ordner werden beim ersten Start automatisch angelegt. Ein Benutzerkonto ist nicht erforderlich.

## Lokal starten

Mit aktivierter virtueller Umgebung:

```powershell
python app.py
```

Die Anwendung ist standardmäßig nur auf diesem PC erreichbar:

<http://127.0.0.1:5000>

Ein Video lädst du unter <http://127.0.0.1:5000/upload> hoch. Die Verwaltung liegt unter <http://127.0.0.1:5000/manage>.

## Erstes Video hinzufügen

Im Repository liegt bewusst kein Testvideo. Wähle in der Navigation **Video hochladen**. Eingetragen werden nur Titel, Beschreibung, Thema und Sprache; dazu kommt die MP4-Datei. `ffprobe` liest Dauer, eingebettetes Erscheinungsjahr und sämtliche technischen Stream- und Containerdaten wie Codec, Auflösung, Bildrate, Audio und Bitrate aus. Fehlt eine Jahresangabe in der Datei, wird das aktuelle Uploadjahr verwendet. `ffmpeg` erzeugt das Vorschaubild aus dem ersten Frame.

Projektlokale Programme unter `tools/ffmpeg/bin/ffprobe.exe` und `tools/ffmpeg/bin/ffmpeg.exe` werden automatisch erkannt. Alternativ lassen sich `FFPROBE_PATH` und `FFMPEG_PATH` in `.env` setzen. Kann eines der drei automatischen Felder nicht erzeugt werden, wird der Upload mit einer verständlichen Fehlermeldung abgebrochen und die unvollständige Datei entfernt.

Ein vorhandenes Video lässt sich auf einem leistungsfähigeren PC vor dem Upload passend vorbereiten:

```powershell
ffmpeg -i eingabe.mov -c:v libx264 -preset medium -crf 22 -c:a aac -b:a 128k -movflags +faststart ausgabe.mp4
```

Die Anwendung prüft die Dateiendung, wandelt das Video aber absichtlich nicht um. Codec und tatsächliche Abspielbarkeit müssen mit einem echten Video geprüft werden.

## Test mit einem Smartphone im lokalen Netz

PC und Smartphone müssen im selben vertrauenswürdigen WLAN/LAN sein.

1. Setze in `.env` den Wert `FLASK_HOST=0.0.0.0`.
2. Ermittle mit `ipconfig` die IPv4-Adresse des PCs, zum Beispiel `192.168.1.25`.
3. Starte die Anwendung mit `python app.py`.
4. Erlaube bei Nachfrage Python für private Netzwerke in der Windows-Firewall. Falls keine Nachfrage erscheint, erstelle dort eine eingehende TCP-Regel für Port 5000.
5. Öffne auf dem Smartphone `http://192.168.1.25:5000`.

Der eingebaute Flask-Server ist für Entwicklung und einen kurzen Gerätetest gedacht. Da die Verwaltung bewusst keine Anmeldung verlangt, kann jedes Gerät mit Zugriff auf die Anwendung Videos hochladen, bearbeiten und löschen. Verwende sie deshalb nur in einem vertrauenswürdigen lokalen Netz.

## Konfiguration

Die Datei `.env.example` dokumentiert alle Einstellungen:

| Variable | Standard | Bedeutung |
| --- | --- | --- |
| `SECRET_KEY` | Entwicklungswert | Signiert Sitzungs- und CSRF-Daten; unbedingt ändern |
| `METADATA_DIR` | `data/videos` | Ordner mit einer JSON-Datei je Video |
| `VIDEO_DIR` | `uploads/videos` | Ordner für MP4-Dateien |
| `THUMBNAIL_DIR` | `uploads/thumbnails` | Ordner für Bilder |
| `MAX_UPLOAD_MB` | `2048` | maximales Volumen einer Upload-Anfrage |
| `FFPROBE_PATH` | `ffprobe` | Programm/Pfad für die Dauerermittlung |
| `FFMPEG_PATH` | `ffmpeg` | Programm/Pfad für ein Vorschaubild aus dem ersten Frame |
| `FLASK_HOST` | `127.0.0.1` | `0.0.0.0` erlaubt LAN-Zugriff |
| `FLASK_PORT` | `5000` | lokaler Port |
| `FLASK_DEBUG` | `0` | Debug-Modus; im Normalbetrieb ausgeschaltet lassen |

Relative Pfade werden unabhängig vom aktuellen Terminalordner relativ zum Projektordner aufgelöst. `.env`, JSON-Metadaten, virtuelle Umgebung, lokale Programme und Uploads stehen in `.gitignore`.

## Automatisierte Tests

```powershell
pip install -r requirements-dev.txt
pytest -q
```

Die Tests verwenden temporäre JSON- und Mediendateien. Wenn die projektlokalen FFmpeg-Programme vorhanden sind, erzeugt ein Integrationstest zusätzlich eine echte H.264/AAC-Datei und prüft Dauer, Jahr und Vorschaubild.

## Übertragung auf Raspberry Pi OS Lite

Die folgenden Befehle gehen von Raspberry Pi OS Lite (64 Bit), dem Benutzer `medienserver`, dem Programmordner `/opt/medienserver` und persistenten Daten unter `/srv/medienserver-data` aus. Für einen Pi 3 sind vorbereitete, maßvoll aufgelöste Videos sinnvoll; der Pi transkodiert nicht.

Pakete und Dienstbenutzer einrichten:

```bash
sudo apt update
sudo apt install -y python3-venv nginx ffmpeg git
sudo adduser --system --group --home /opt/medienserver medienserver
sudo git clone https://github.com/mikaeth95/lbs_pp_medienserver.git /opt/medienserver
sudo mkdir -p /srv/medienserver-data/videos /srv/medienserver-data/thumbnails /srv/medienserver-data/metadata
sudo chown -R medienserver:www-data /opt/medienserver /srv/medienserver-data
sudo find /srv/medienserver-data -type d -exec chmod 2750 {} \;
sudo find /srv/medienserver-data -type f -exec chmod 0640 {} \;
```

Python-Umgebung installieren:

```bash
sudo -u medienserver python3 -m venv /opt/medienserver/.venv
sudo -u medienserver /opt/medienserver/.venv/bin/pip install -r /opt/medienserver/requirements.txt
sudo -u medienserver cp /opt/medienserver/.env.example /opt/medienserver/.env
sudo chmod 0640 /opt/medienserver/.env
```

In `/opt/medienserver/.env` mindestens diese Werte setzen:

```dotenv
SECRET_KEY=einen-langen-zufaelligen-wert-eintragen
METADATA_DIR=/srv/medienserver-data/metadata
VIDEO_DIR=/srv/medienserver-data/videos
THUMBNAIL_DIR=/srv/medienserver-data/thumbnails
MAX_UPLOAD_MB=2048
FFPROBE_PATH=/usr/bin/ffprobe
FFMPEG_PATH=/usr/bin/ffmpeg
```

Die mitgelieferten Konfigurationen aktivieren:

```bash
cd /opt/medienserver
sudo cp deployment/medienserver.service /etc/systemd/system/medienserver.service
sudo cp deployment/nginx-medienserver.conf /etc/nginx/sites-available/medienserver
sudo ln -s /etc/nginx/sites-available/medienserver /etc/nginx/sites-enabled/medienserver
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl daemon-reload
sudo systemctl enable --now medienserver nginx
```

Status und Protokoll prüfen:

```bash
systemctl status medienserver --no-pager
journalctl -u medienserver -n 100 --no-pager
```

Nginx liefert `/media/videos/` direkt aus und unterstützt dabei Byte-Range-Anfragen. Katalog, Upload und Verwaltung gehen über Gunicorn an Flask. Passe `server_name` in `deployment/nginx-medienserver.conf` an den vereinbarten Hostnamen oder die feste IP-Adresse an.

## Aktualisierung und Datensicherung auf dem Pi

Vor einer Aktualisierung zuerst sichern, dann Anwendung aktualisieren:

```bash
sudo systemctl stop medienserver
sudo tar -czf /srv/medienserver-backup-$(date +%F).tar.gz -C /srv medienserver-data
cd /opt/medienserver
sudo -u medienserver git pull --ff-only
sudo -u medienserver .venv/bin/pip install -r requirements.txt
sudo systemctl start medienserver
```

Kopiere Sicherungen regelmäßig auf einen anderen Datenträger oder Server und teste die Wiederherstellung. Eine vollständige Sicherung umfasst JSON-Dokumente, Videos, Vorschaubilder und `.env`. Da das Stoppen während des Backups kurzzeitig die Verwaltung unterbricht, sollte es außerhalb der Nutzungszeit erfolgen; bereits von Nginx ausgelieferte Dateien sind davon unabhängig.

## Prüfstand

Automatisiert geprüft werden der dokumentbasierte JSON-Speicher, öffentlich erreichbarer Upload, sichere Dateinamen, automatische Übernahme von Dauer und Erscheinungsjahr, Erzeugung des Vorschaubilds, kombinierte Suche/Filter, Bearbeiten, CSRF-Schutz, vollständiges Löschen und eine echte HTTP-Byte-Range-Antwort (`206 Partial Content`).

Noch auf echten Endgeräten zu prüfen sind Bild und Ton eines realen H.264/AAC-Videos, Springen und Vollbild auf den verwendeten Notebook-/Tablet-/Smartphone-Browsern, gleichzeitige Wiedergaben sowie Leistung und Nginx-Betrieb auf dem Raspberry Pi 3.
