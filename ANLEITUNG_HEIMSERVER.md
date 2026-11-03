# Medienserver am Ubuntu-Heimserver – Schritt-für-Schritt-Anleitung

Vorübergehend läuft der Medienserver statt am Schul-Pi auf dem Heimserver `server-home`
(Ubuntu, `10.0.0.19`), damit Mitschüler ihn über das Internet per **HTTPS** testen können.
Später geht er auf den Pi zurück (siehe Teil 11).

```
Mitschüler (Browser, https://meinname.duckdns.org)
        │
DuckDNS  →  aktuelle öffentliche A1-IPv4 (ändert sich, wird alle 5 min gemeldet)
        │
A1 Hybrid Box: TCP 80 + 443  →  10.0.0.19
        │
Caddy (HTTPS, Let's-Encrypt-Zertifikat, liefert Videos aus)
        │
Gunicorn nur auf 127.0.0.1:8000  →  Flask-App  →  JSON-Dateien + Videos
```

**Warum so?** (für die Präsentation)

- **HTTPS statt HTTP:** Passwörter und Videos gehen verschlüsselt über das Internet.
  Caddy holt das Zertifikat selbst und erneuert es automatisch.
- **Gunicorn nur intern:** Von außen ist nur Caddy erreichbar, die App selbst nicht.
- **Caddy liefert die Videos:** Flask prüft nur die Anmeldung, Caddy schickt die Datei
  (inkl. Vorspulen). So blockiert ein langes Video keinen App-Prozess.
- **DuckDNS:** Die öffentliche A1-Adresse ändert sich, der Name bleibt gleich.
- **Kein Docker:** Am Heimserver direkt mit systemd, das ist einfacher zu prüfen und Caddy
  kann die Zertifikate selbst verwalten. Die Ordnerstruktur (`data/`, `uploads/`) ist dieselbe
  wie am Pi, daher lassen sich die Daten später 1:1 zurückkopieren.

Wo **Am PC** steht, ist PowerShell im Projektordner gemeint, wo **Am Server** steht, das
SSH-Terminal auf `server-home` als Benutzer `michael`.

| Teil | Inhalt |
| --- | --- |
| 1 | Testfreigaben vom Porttest aufräumen |
| 2 | Code auf den Server bringen |
| 3 | App einrichten |
| 4 | Autostart mit systemd + Administrator |
| 5 | DynDNS (DuckDNS) |
| 6 | Caddy (HTTPS), Firewall, Router |
| 7 | Test von außen |
| 8 | Backup |
| 9 | Updates und Rollback |
| 10 | Spickzettel Betrieb + Rückweg |
| 11 | Zurück auf den Pi |

---

## Teil 1: Testfreigaben vom Porttest aufräumen

### Schritt 1.1 – Am Server: läuft der Python-Testserver noch?

```bash
sudo ss -lntp | grep 8765
```

- Keine Ausgabe → Testserver ist beendet, weiter mit 1.2.
- Eine Zeile mit `python3` → im Terminal, wo er läuft, `Strg+C` drücken.
  Läuft er in keinem offenen Terminal mehr: die `pid=…` aus der Zeile ablesen und
  `sudo kill <pid>` ausführen. Danach den leeren Testordner löschen: `rm -rf /tmp/medienserver-porttest`.

### Schritt 1.2 – Am Server: Firewall-Regeln für 8765 entfernen

```bash
sudo ufw status numbered
```

Die beiden 8765-Regeln löschen (**nicht** `ufw disable`, andere Regeln wie SSH bleiben stehen):

```bash
sudo ufw delete allow 8765/tcp
sudo ufw delete allow from 10.0.0.0/24 to any port 8765 proto tcp
sudo ufw status numbered
```

Erwartet: keine Zeile mit `8765` mehr. Meldet ein Befehl „Could not delete non-existent rule“,
war die Regel schon weg – das ist in Ordnung.

> Die Router-Regel „Medienserver“ (8765) bleibt vorerst stehen; ohne UFW-Freigabe und ohne
> laufenden Dienst ist dahinter nichts erreichbar. Sie wird in Teil 6 auf 443 umgestellt.

### Schritt 1.3 – Am Server: Netplan-Warnung beheben

```bash
ls -l /etc/netplan/
```

Steht bei `01-network-manager-all.yaml` etwas anderes als `-rw-------`:

```bash
sudo chmod 600 /etc/netplan/01-network-manager-all.yaml
sudo netplan get
```

Das ändert nur die Dateirechte, die Netzwerkverbindung bleibt bestehen
(**kein** `netplan apply` nötig). `netplan get` sollte jetzt ohne Warnung laufen.

---

## Teil 2: Code auf den Server bringen

Der Weg ist derselbe wie beim Pi: PC → GitHub → Server.

### Schritt 2.1 – Am PC: aktuellen Stand hochladen

```powershell
git status
git push
```

Kontrolliert, dass **keine** `.env`, kein Passwort und kein Token dabei ist.

### Schritt 2.2 – Am Server: Pakete und Dienstbenutzer

```bash
sudo apt update
sudo apt install -y python3-venv ffmpeg git curl
sudo adduser --system --group --no-create-home --home /opt/medienserver medienserver
sudo git clone https://github.com/mikaeth95/lbs_pp_medienserver.git /opt/medienserver
```

Der eigene Benutzer `medienserver` darf sich nicht anmelden und hat keine Admin-Rechte.
Wird die App je gehackt, kommt der Angreifer nicht an `michael` oder `sudo` heran.

> Ist das GitHub-Repository privat, fragt `git clone` nach Zugangsdaten. Dann am Server
> einen [Fine-grained Token](https://github.com/settings/tokens?type=beta) mit nur
> Lesezugriff auf dieses eine Repo verwenden – nie euer GitHub-Passwort, und den Token nirgends speichern.

---

## Teil 3: App einrichten

### Schritt 3.1 – Am Server: Python-Umgebung

```bash
cd /opt/medienserver
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt
```

### Schritt 3.2 – Am Server: `.env` mit zufälligem SECRET_KEY anlegen

Der Schlüssel wird direkt in die Datei geschrieben und nie angezeigt:

```bash
cd /opt/medienserver
sudo install -m 600 /dev/null .env
sudo tee .env >/dev/null <<EOF
SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
MAX_UPLOAD_MB=2048
ALLOW_REGISTRATION=0
MEDIA_X_ACCEL=1
SESSION_COOKIE_SECURE=1
FFPROBE_PATH=/usr/bin/ffprobe
FFMPEG_PATH=/usr/bin/ffmpeg
EOF
```

| Einstellung | Warum |
| --- | --- |
| `ALLOW_REGISTRATION=0` | Die Seite ist aus dem ganzen Internet erreichbar. Konten legt nur der Admin an. Für einen Test mit Selbstregistrierung kurz auf `1` stellen und danach wieder auf `0`. |
| `MEDIA_X_ACCEL=1` | Flask prüft die Anmeldung, Caddy liefert die Videodatei. |
| `SESSION_COOKIE_SECURE=1` | Das Login-Cookie wird nur über HTTPS geschickt. |

Die Ordner `data/` und `uploads/` legt die App beim Start selbst unter `/opt/medienserver` an.

### Schritt 3.3 – Am Server: Ordner und Rechte

```bash
cd /opt/medienserver
sudo mkdir -p data/videos data/users uploads/videos uploads/thumbnails
sudo chown -R medienserver:www-data /opt/medienserver
sudo chmod 750 /opt/medienserver
sudo chmod 700 data
sudo chmod 2750 uploads uploads/videos uploads/thumbnails
```

- `www-data` ist die Gruppe, über die Caddy später Videos und Vorschaubilder lesen darf.
- `data/` (Benutzer mit Passwort-Hashes, Video-JSONs) darf nur die App selbst lesen.
- `.env` gehört nur `medienserver` (`600`), Caddy kann den SECRET_KEY also nicht lesen.

---

## Teil 4: Autostart mit systemd + Administrator

### Schritt 4.1 – Am Server: Dienst installieren

```bash
sudo cp /opt/medienserver/deployment/medienserver.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now medienserver
systemctl status medienserver --no-pager
```

Erwartet: `Active: active (running)`.

### Schritt 4.2 – Am Server: lokal prüfen

```bash
curl -sI http://127.0.0.1:8000/login | head -1
sudo ss -lntp | grep 8000
```

Erwartet: `HTTP/1.1 200 OK` und `127.0.0.1:8000` – **nicht** `0.0.0.0:8000`.
Fehler ansehen: `journalctl -u medienserver -n 100 --no-pager`.

### Schritt 4.3 – Am Server: ersten Administrator anlegen

```bash
cd /opt/medienserver
sudo -u medienserver .venv/bin/flask --app app create-user admin
```

Das Passwort wird zweimal abgefragt und nicht angezeigt. Weil die Seite öffentlich ist:
**langes, eigenes Passwort** (z. B. vier zufällige Wörter), nirgends sonst verwendet.

---

## Teil 5: DynDNS mit DuckDNS

Die öffentliche A1-Adresse (zuletzt `194.166.60.204`) ändert sich. DuckDNS gibt einen festen
Namen, und der Server meldet alle 5 Minuten seine aktuelle Adresse.

### Schritt 5.1 – Am PC: Namen anlegen

1. <https://www.duckdns.org> öffnen, mit GitHub- oder Google-Konto anmelden.
2. Unter **domains** einen Namen eintragen, z. B. `lbs-medienserver` → **add domain**.
3. Den **token** oben auf der Seite **nicht** abschreiben oder in den Chat kopieren – er wird
   gleich direkt am Server eingetragen.

Im Folgenden steht `meinname` für euren Namen (ohne `.duckdns.org`).

### Schritt 5.2 – Am Server: Zugangsdaten sicher ablegen

```bash
sudo install -d -m 700 /etc/medienserver
sudo install -m 600 /dev/null /etc/medienserver/duckdns.env
sudo nano /etc/medienserver/duckdns.env
```

Inhalt (Token aus der DuckDNS-Seite einfügen), dann `Strg+O`, `Enter`, `Strg+X`:

```
DUCKDNS_DOMAIN=meinname
DUCKDNS_TOKEN=hier-den-token-einfuegen
```

Die Datei liegt außerhalb des Projektordners und kommt so nie auf GitHub.

### Schritt 5.3 – Am Server: testen und automatisch ausführen

```bash
sudo bash /opt/medienserver/deployment/duckdns-update.sh && echo "Update ok"
curl -4 -s https://api.ipify.org; echo
getent hosts meinname.duckdns.org
```

Beide Adressen müssen gleich sein. Dann automatisch alle 5 Minuten:

```bash
sudo crontab -e
```

Ganz unten einfügen:

```
*/5 * * * * bash /opt/medienserver/deployment/duckdns-update.sh >> /var/log/medienserver-duckdns.log 2>&1
```

Das Log bleibt leer, solange alles klappt; es enthält nur Fehler.

---

## Teil 6: Caddy (HTTPS), Firewall und Router

### Schritt 6.1 – Am Server: Caddy installieren

Offizielle Paketquelle (aktuelle Version, Ubuntu-Paket ist zu alt):

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
caddy version
```

### Schritt 6.2 – Am Server: Caddy darf Videos lesen

```bash
sudo usermod -aG www-data caddy
```

### Schritt 6.3 – Am Server: Konfiguration einspielen

`meinname` durch euren DuckDNS-Namen ersetzen:

```bash
sudo cp /opt/medienserver/deployment/Caddyfile /etc/caddy/Caddyfile
sudo sed -i 's/MEDIENSERVER_DOMAIN/meinname.duckdns.org/' /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
```

Erwartet am Ende: `Valid configuration`. Noch **nicht** neu starten – erst Firewall und Router,
sonst kann Let's Encrypt den Server nicht erreichen.

### Schritt 6.4 – Am Server: Firewall für Webports öffnen

```bash
sudo ufw allow 80/tcp comment 'Caddy: Zertifikat + Umleitung auf HTTPS'
sudo ufw allow 443/tcp comment 'Caddy: Medienserver HTTPS'
sudo ufw status numbered
```

Nur diese beiden Ports. SSH (22) wird **nicht** am Router freigegeben.

### Schritt 6.5 – Router: Portweiterleitung umstellen

In der A1 Hybrid Box (<http://10.0.0.138>) → **Erweitert → Weiterleiten → IPv4-Portzuordnung**:

| Regel | Interner Host | Protokoll | Externer Port | Interner Port | Externe Quell-IP |
| --- | --- | --- | --- | --- | --- |
| `Medienserver` (vorhandene Regel ändern: 8765 → 443) | `10.0.0.19` | TCP | 443–443 | 443–443 | leer |
| `Medienserver-HTTP` (neu) | `10.0.0.19` | TCP | 80–80 | 80–80 | leer |

DHCP-Reservierung und alles andere am Router bleiben unverändert. Keine DMZ.
Meldet der Router einen Konflikt bei 80/443, ist dort meist der **Fernzugriff** auf die
Router-Oberfläche aktiv – der muss ohnehin aus bleiben.

### Schritt 6.6 – Am Server: Caddy starten und Zertifikat prüfen

```bash
sudo systemctl restart caddy
sudo journalctl -u caddy -f
```

Nach einigen Sekunden sollte `certificate obtained successfully` erscheinen. Mit `Strg+C` raus.

Kommt stattdessen `timeout during connect` oder `connection refused`: Router-Regeln und
`sudo ufw status` prüfen. Kommt der Fehler weiterhin, blockiert A1 eventuell die Ports 80/443
(bei 8765 hat es funktioniert). Dann nicht weiter herumprobieren, sondern melden – Plan B ist ein
Zertifikat über DNS (Caddy mit DuckDNS-Plugin), dafür muss kein Port 80/443 offen sein.

---

## Teil 7: Test von außen

**Immer mit mobilen Daten (WLAN aus)** testen. Aus dem Heim-WLAN funktioniert der
DuckDNS-Name je nach Router nicht (fehlendes NAT-Loopback) – das ist kein Fehler des Servers.

### Schritt 7.1 – Am Server: Schnelltest

```bash
curl -sI https://meinname.duckdns.org/login | head -1
curl -sI http://meinname.duckdns.org/ | grep -i '^location'
curl -sI https://meinname.duckdns.org/media/videos/test.mp4 | grep -i '^location'
```

Erwartet: `HTTP/2 200`, dann Umleitung auf `https://…`, dann Umleitung auf `/login`
(Videos ohne Anmeldung gesperrt). Klappt der erste Befehl aus dem Heimnetz nicht (NAT-Loopback),
diesen Teil am Handy prüfen.

### Schritt 7.2 – Testkonten anlegen

Als `admin` anmelden → **Benutzer** → für jede Testperson ein Konto anlegen
(Teilnehmer bzw. Trainer). Passwörter persönlich weitergeben, nicht in die Klassengruppe.

### Schritt 7.3 – Checkliste (Handy mit mobilen Daten)

| Test | OK? |
| --- | --- |
| `https://meinname.duckdns.org` zeigt ein Schloss, keine Zertifikatswarnung | ☐ |
| Anmeldung klappt, falsches Passwort wird abgelehnt | ☐ |
| Ohne Anmeldung: nur Login-Seite, auch Video-Links leiten auf Login um | ☐ |
| Video startet, Vorspulen und ±10-Sekunden-Tasten gehen | ☐ |
| Ton ist zu hören | ☐ |
| Trainer lädt ein Video hoch, Dauer + Vorschaubild werden erzeugt | ☐ |
| Teilnehmer sieht keinen Upload-Button | ☐ |
| 5 Personen schauen gleichzeitig – ruckelt es? | ☐ |
| Nach `sudo reboot` des Servers ist alles wieder da | ☐ |

### Schritt 7.4 – Reicht die Upload-Geschwindigkeit?

Jeder Zuschauer zieht die Videodaten über den **Upload** des Heimanschlusses. Ein 720p-Video
braucht grob 2–3 Mbit/s, fünf gleichzeitig also etwa 10–15 Mbit/s. Upload messen:

```bash
sudo apt install -y speedtest-cli
speedtest-cli --secure --simple
```

Reicht es knapp nicht: Videos vor dem Upload auf 720p umwandeln (Befehl in
[ANLEITUNG_PI.md, Schritt 3.3](ANLEITUNG_PI.md)).

---

## Teil 8: Backup

Gesichert werden `data/`, `uploads/` und `.env`. Die Sicherungen landen bei `michael`,
damit ihr sie per `scp` auf den PC holen könnt.

### Schritt 8.1 – Am Server: einmal von Hand

```bash
sudo PROJECT_DIR=/opt/medienserver BACKUP_DIR=/home/michael/medienserver-backups BACKUP_OWNER=michael \
    bash /opt/medienserver/deployment/backup.sh
ls -lh /home/michael/medienserver-backups
```

### Schritt 8.2 – Am Server: jede Nacht automatisch

```bash
sudo crontab -e
```

Unter der DuckDNS-Zeile einfügen:

```
30 2 * * * PROJECT_DIR=/opt/medienserver BACKUP_DIR=/home/michael/medienserver-backups BACKUP_OWNER=michael bash /opt/medienserver/deployment/backup.sh >> /var/log/medienserver-backup.log 2>&1
```

### Schritt 8.3 – Am PC: Kopie holen (im Heimnetz)

```powershell
mkdir C:\Users\Ich\Desktop\medienserver_backups -Force
scp "michael@10.0.0.19:medienserver-backups/medienserver_*.tar.gz" C:\Users\Ich\Desktop\medienserver_backups\
```

Nicht in den Projektordner legen – die Sicherung enthält `.env` mit dem SECRET_KEY.

### Schritt 8.4 – Wiederherstellen (nur im Notfall)

```bash
sudo systemctl stop medienserver
ls /home/michael/medienserver-backups
sudo tar -xzf /home/michael/medienserver-backups/medienserver_DATUM_UHRZEIT.tar.gz -C /opt/medienserver
sudo chown -R medienserver:www-data /opt/medienserver/data /opt/medienserver/uploads /opt/medienserver/.env
sudo systemctl start medienserver
```

Speicherplatz prüfen: `df -h /opt /home`. Jede Sicherung ist etwa so groß wie alle Videos.

---

## Teil 9: Updates und Rollback

### Update einspielen

```powershell
# Am PC
git add .
git commit -m "Was geändert wurde"
git push
```

```bash
# Am Server – vorher Backup (Schritt 8.1)
cd /opt/medienserver
sudo -u medienserver git log --oneline -1          # alten Stand notieren
sudo -u medienserver git pull --ff-only
sudo .venv/bin/pip install -r requirements.txt
sudo chown -R medienserver:www-data /opt/medienserver/.venv
sudo systemctl restart medienserver
systemctl status medienserver --no-pager
```

Hat sich `deployment/Caddyfile` geändert, Schritt 6.3 wiederholen und dann
`sudo systemctl reload caddy`. Hat sich `deployment/medienserver.service` geändert, Schritt 4.1 wiederholen.

### Rollback, falls das Update kaputt ist

```bash
cd /opt/medienserver
sudo -u medienserver git checkout <alter-stand>      # Kennung aus "git log" von oben
sudo systemctl restart medienserver
```

Später wieder auf den neuesten Stand: `sudo -u medienserver git checkout master`.
Daten (`data/`, `uploads/`) werden durch `git` nicht verändert.

---

## Teil 10: Spickzettel Betrieb

| Was | Befehl |
| --- | --- |
| Status | `systemctl status medienserver caddy --no-pager` |
| App neu starten | `sudo systemctl restart medienserver` |
| App stoppen / starten | `sudo systemctl stop medienserver` / `sudo systemctl start medienserver` |
| App-Log | `journalctl -u medienserver -n 100 --no-pager` |
| Caddy-Log (Zertifikat) | `journalctl -u caddy -n 100 --no-pager` |
| Zugriffslog | `sudo tail -f /var/log/caddy/medienserver.log` |
| Firewall | `sudo ufw status numbered` |
| Passwort Admin zurücksetzen | `cd /opt/medienserver && sudo -u medienserver .venv/bin/flask --app app set-password admin` |

### Was ist von außen offen?

| Stelle | Regel |
| --- | --- |
| Router | TCP 80 und 443 → `10.0.0.19` (sonst nichts, keine DMZ) |
| UFW | `80/tcp` und `443/tcp` erlaubt, bestehende Regeln (z. B. SSH im LAN) unverändert |
| Gunicorn | nur `127.0.0.1:8000`, von außen nicht erreichbar |
| SSH | **nicht** am Router freigegeben |

### Rückweg: Medienserver wieder aus dem Internet nehmen

1. Router: Regeln `Medienserver` und `Medienserver-HTTP` deaktivieren oder löschen.
2. Am Server:
   ```bash
   sudo ufw delete allow 80/tcp
   sudo ufw delete allow 443/tcp
   sudo systemctl disable --now caddy medienserver
   ```
3. DuckDNS-Zeile in `sudo crontab -e` löschen.

### Sicherheit – was geschützt ist, was offen bleibt

- Ohne Anmeldung ist nur die Login-Seite erreichbar, auch Video- und Bilddateien sind geschützt.
- HTTPS mit automatischem Zertifikat, Login-Cookie nur über HTTPS, CSRF-Schutz, Passwörter nur als Hash.
- Flask-Debug-Modus ist aus (Gunicorn startet die App, `main.py` wird nicht verwendet).
- **Offen:** Es gibt keine Sperre nach vielen falschen Passwörtern. Deshalb lange Passwörter,
  Selbstregistrierung aus, und die Freigabe nach dem Test wieder schließen (Rückweg oben).

---

## Teil 11: Zurück auf den Pi

Am Pi (Docker) liegen die Daten in denselben Ordnern (`data/videos`, `data/users`,
`uploads/videos`, `uploads/thumbnails`). Eine Sicherung vom Heimserver (Schritt 8.3) lässt sich
deshalb direkt einspielen – **ohne** die `.env` vom Heimserver, der Pi behält seine eigene
(dort gibt es kein HTTPS, `SESSION_COOKIE_SECURE=1` würde den Login verhindern):

```powershell
# Am PC
scp C:\Users\Ich\Desktop\medienserver_backups\medienserver_DATUM_UHRZEIT.tar.gz pi@10.0.13.3:/home/pi/
```

```bash
# Am Pi
sudo systemctl stop medienserver-docker
cd /home/pi/medienserver
tar -xzf /home/pi/medienserver_DATUM_UHRZEIT.tar.gz data uploads
sudo systemctl start medienserver-docker
```

Danach am Heimserver den Rückweg aus Teil 10 ausführen.
