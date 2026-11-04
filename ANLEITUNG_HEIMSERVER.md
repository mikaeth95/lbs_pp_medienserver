# Medienserver am Ubuntu-Heimserver (Docker) – Schritt-für-Schritt-Anleitung

Vorübergehend läuft der Medienserver statt am Schul-Pi auf dem Heimserver `server-home`
(Ubuntu 24.04, `10.0.0.19`), damit Mitschüler ihn über das Internet per **HTTPS** testen können.
Diese Anleitung stellt den bisherigen systemd-Dienst **schrittweise und ohne Datenverlust** auf
Docker Compose um. Später geht der Server auf den Pi zurück (Teil 12).

```
Mitschüler (Browser, https://medienserver.michael-gonaus.dev)
        │
DNS bei Namecheap  →  aktuelle öffentliche A1-IPv4
        │              (ddclient am Ubuntu-Host meldet Änderungen)
A1 Hybrid Box: TCP 80 + 443  →  10.0.0.19
        │
┌─ Docker Compose (compose.home.yaml) ───────────────────────────────┐
│  caddy  : HTTPS, Let's-Encrypt-Zertifikat, liefert Videos aus      │
│     │      (einziger Container mit offenen Ports: 80, 443)         │
│  app    : Flask + Gunicorn, nur im internen Docker-Netz, kein Internet│
└────────────────────────────────────────────────────────────────────┘
        │  Bind Mounts
/opt/medienserver/data/   (JSON je Video, JSON je Benutzer)
/opt/medienserver/uploads/ (Videos, Vorschaubilder)
```

**Warum so?** (für die Präsentation)

- **Docker:** Die App läuft in einem genau festgelegten Paket (Python, ffmpeg, Gunicorn).
  Ein Update oder ein neuer Server ist `docker compose up -d --build` – die Daten liegen
  außerhalb der Container und bleiben erhalten.
- **HTTPS mit Caddy:** Passwörter und Videos gehen verschlüsselt über das Internet.
  Caddy holt das Zertifikat selbst und erneuert es automatisch.
- **Gunicorn nur intern:** Von außen ist nur Caddy erreichbar. Der App-Container hat
  nicht einmal Internetzugang.
- **Caddy liefert die Videos:** Flask prüft vorher die Anmeldung und antwortet mit
  `X-Accel-Redirect`; Caddy schickt dann die Datei inklusive Vorspulen (Range-Requests).
- **Eigene Subdomain:** `medienserver.michael-gonaus.dev` – die anderen Projekte auf der Domain
  bleiben unberührt. Dynamic DNS macht ddclient direkt am Host, Docker braucht dafür nichts.
- **Pi unverändert:** Der Raspberry Pi nutzt weiter `compose.yaml` (Nginx, HTTP im Schul-LAN).

**Am PC** = PowerShell im Projektordner,
**Am Server** = SSH-Terminal auf `server-home` als `michael`.

| Teil | Inhalt | Dienst danach |
| --- | --- | --- |
| 1 | Ausgangslage prüfen, Code holen, Sicherung | systemd |
| 2 | Docker installieren | systemd |
| 3 | DNS prüfen (ddclient) | systemd |
| 4 | `.env` ergänzen, Konfiguration und Rechte prüfen | systemd |
| 5 | App-Container parallel testen | systemd + Docker-App |
| 6 | Router, Firewall, Caddy starten | systemd + Docker |
| 7 | Funktionstest von außen | systemd + Docker |
| 8 | Erst jetzt: systemd-Dienst abschalten | Docker |
| 9 | Rollback | – |
| 10 | Backup und Wiederherstellung | Docker |
| 11 | Updates, Spickzettel, Rückweg | Docker |
| 12 | Zurück auf den Pi | – |

---

## Teil 1: Ausgangslage prüfen, Code holen, Sicherung

### Schritt 1.1 – Am PC: neuen Stand hochladen

```powershell
git status
git push
```

Kontrolliert, dass **keine** `.env`, kein Passwort und kein Token dabei ist.

### Schritt 1.2 – Am Server: Ausgangslage prüfen

```bash
systemctl is-active medienserver
sudo ss -lntp | grep -E ':(80|443|8000)\b'
id medienserver
getent group www-data
```

Erwartet:

- `active`
- nur `127.0.0.1:8000` (Gunicorn). Auf **80 und 443 darf noch nichts laufen.** Steht dort
  z. B. ein früher installierter Caddy oder Nginx: `sudo systemctl disable --now caddy`
  (bzw. `nginx`) – nicht deinstallieren nötig.
- `uid=122(medienserver) …` und `www-data:x:33:`. Die beiden Zahlen werden in Teil 4 gebraucht.

### Schritt 1.3 – Am Server: Code aktualisieren

```bash
cd /opt/medienserver
sudo -u medienserver git pull --ff-only
ls compose.home.yaml deployment/Caddyfile deployment/backup-home.sh
```

Der laufende systemd-Dienst merkt davon nichts, er läuft mit dem alten Stand weiter.

### Schritt 1.4 – Am Server: Sicherung **vor** der Umstellung

```bash
sudo apt update
sudo apt install -y rsync
sudo bash /opt/medienserver/deployment/backup-home.sh
ls -lh /home/michael/medienserver-backups/
sudo bash /opt/medienserver/deployment/backup-home.sh check /home/michael/medienserver-backups/medienserver-home_*.tar
```

Das Skript hält den systemd-Dienst für ein paar Sekunden an, damit nichts halb kopiert wird,
und startet ihn sofort wieder. `check` muss mit `Archiv ist in Ordnung` enden.
Dieses Archiv ist euer Rückweg, falls bei der Umstellung etwas schiefgeht.

---

## Teil 2: Docker installieren und prüfen

Offizielle Docker-Paketquelle (das Ubuntu-Paket `docker.io` hat oft kein aktuelles Compose):

```bash
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

Prüfen:

```bash
sudo docker run --rm hello-world
sudo docker compose version
```

Erwartet: `Hello from Docker!` und eine Compose-Version ab 2.24.

> `michael` wird bewusst **nicht** in die Gruppe `docker` aufgenommen – wer Docker steuern darf,
> hat praktisch root-Rechte. Darum steht vor jedem Docker-Befehl `sudo`.

---

## Teil 3: DNS prüfen (ddclient)

Dynamic DNS ist am Ubuntu-Host bereits mit **ddclient** eingerichtet und hält
`medienserver.michael-gonaus.dev` bei Namecheap auf der aktuellen A1-Adresse. Docker braucht dafür nichts,
und an den anderen DNS-Einträgen der Domain ändert sich nichts. Nur prüfen:

```bash
sudo apt install -y bind9-dnsutils
systemctl is-active ddclient
curl -4 -s https://api.ipify.org; echo
dig +short medienserver.michael-gonaus.dev A
dig +short michael-gonaus.dev CAA
```

Erwartet:

- `active`
- zweimal **dieselbe** IPv4-Adresse (ipify und `dig`)
- bei `CAA` keine Ausgabe oder ein Eintrag mit `letsencrypt.org`. Steht dort nur eine andere
  Zertifizierungsstelle, bekommt Caddy kein Zertifikat – dann vorher melden.

> **Besonderheit `.dev`:** Alle Browser öffnen `.dev`-Adressen nur über HTTPS (HSTS-Preload).
> Ohne gültiges Zertifikat ist die Seite also gar nicht erreichbar, auch nicht per `http://`.
> Let's Encrypt prüft den Server trotzdem über Port 80 – deshalb werden in Teil 6 beide
> Ports 80 und 443 weitergeleitet.

Die ddclient-Konfiguration `/etc/ddclient.conf` (enthält das Namecheap-Passwort) bleibt, wo sie
ist, und wird vom Backup mitgesichert (Teil 10).

---

## Teil 4: `.env` ergänzen, Konfiguration und Rechte prüfen

### Schritt 4.1 – Am Server: `.env` ergänzen

Die vorhandene `.env` mit dem `SECRET_KEY` bleibt, es kommen nur Zeilen dazu
(`122`/`33` aus Schritt 1.2):

```bash
cd /opt/medienserver
sudo cp -p .env /home/michael/env-vor-docker.bak
sudo grep -q '^COMPOSE_FILE=' .env || sudo tee -a .env >/dev/null <<'EOF'

# Docker Compose am Heimserver
COMPOSE_FILE=compose.home.yaml
PUID=122
PGID=33
MEDIENSERVER_DOMAIN=medienserver.michael-gonaus.dev
EOF
sudo grep -v '^SECRET_KEY=' .env
```

Die Zeilen werden nur angehängt, wenn sie noch fehlen; vorhandene Einträge bleiben unverändert.
Der letzte Befehl zeigt alles außer dem Geheimnis. `SESSION_COOKIE_SECURE=1`,
`ALLOW_REGISTRATION=1`, `MEDIA_X_ACCEL=1` und `TRUST_PROXY=1` sind in `compose.home.yaml` fest eingestellt
und lassen sich über `.env` nicht versehentlich abschalten.

Durch `COMPOSE_FILE` verwendet `sudo docker compose …` in `/opt/medienserver` automatisch
`compose.home.yaml` – die Pi-Datei `compose.yaml` wird am Heimserver nie gestartet.

### Schritt 4.2 – Am Server: Konfiguration validieren

```bash
cd /opt/medienserver
sudo docker compose config --quiet && echo "Compose ok"
sudo docker compose config --services
```

Erwartet: `Compose ok` und `app`, `caddy`.

> **Nicht** `docker compose config` ohne `--quiet` ausführen und die Ausgabe weitergeben:
> sie enthält den `SECRET_KEY`.

### Schritt 4.3 – Am Server: Rechte prüfen

```bash
cd /opt/medienserver
stat -c '%a %U:%G %n' data data/videos data/users uploads uploads/videos uploads/thumbnails
sudo find data uploads \( ! -user medienserver -o ! -group www-data \) -print | head
sudo find uploads \( -type d ! -perm -g=rx -o -type f ! -perm -g=r \) -print | head
```

- Alle Zeilen der ersten Ausgabe: `medienserver:www-data`.
- Die beiden `find`-Befehle geben **nichts** aus.

Warum das wichtig ist: Der App-Container läuft als `122:33` (= `medienserver:www-data`) und
braucht dieselben Rechte wie bisher der systemd-Dienst. Caddy darf die Videos nur über die
Gruppe `www-data` lesen. Falls `find` etwas ausgibt, nur die Rechte korrigieren (es wird nichts gelöscht):

```bash
sudo chown -R medienserver:www-data data uploads
sudo chmod -R g+rX uploads
```

---

## Teil 5: App-Container parallel testen

Der systemd-Dienst läuft weiter. Zuerst wird nur der App-Container gebaut und gestartet –
er ist noch von nirgends erreichbar, nur zum Prüfen.

```bash
cd /opt/medienserver
sudo docker compose build
sudo docker compose up -d app
sudo docker compose ps
```

Nach etwa 30 Sekunden muss bei `app` **healthy** stehen. Dann prüfen:

```bash
sudo docker compose exec app id
sudo docker compose exec app python -c "import app; a = app.create_app(); a.app_context().push(); print(len(app.list_users()), 'Benutzer,', len(app.list_videos()), 'Videos')"
```

Erwartet: `uid=122 gid=33` und dieselbe Anzahl Benutzer und Videos wie bisher.
Fehler: `sudo docker compose logs --tail=100 app`.

> Beide Dienste greifen jetzt auf dieselben Ordner zu. Das ist unkritisch (jede Änderung
> wird als ganze Datei geschrieben), trotzdem in dieser Phase nichts hochladen oder ändern.

---

## Teil 6: Router, Firewall, Caddy starten

### Schritt 6.1 – Router: Portweiterleitung

In der A1 Hybrid Box (<http://10.0.0.138>) → **Erweitert → Weiterleiten → IPv4-Portzuordnung**:

| Regel | Interner Host | Protokoll | Externer Port | Interner Port | Externe Quell-IP |
| --- | --- | --- | --- | --- | --- |
| `Medienserver` (vorhandene Regel ändern: 8765 → 443) | `10.0.0.19` | TCP | 443–443 | 443–443 | leer |
| `Medienserver-HTTP` (neu) | `10.0.0.19` | TCP | 80–80 | 80–80 | leer |

DHCP-Reservierung und alles andere bleiben unverändert. Keine DMZ, kein SSH.
Meldet der Router einen Konflikt bei 80/443, ist meist der **Fernzugriff** auf die
Router-Oberfläche aktiv – der muss ohnehin aus bleiben.

### Schritt 6.2 – Am Server: Firewall

```bash
sudo ufw status numbered
sudo ufw delete allow 8765/tcp
sudo ufw delete allow from 10.0.0.0/24 to any port 8765 proto tcp
sudo ufw allow 80/tcp comment 'Medienserver: Zertifikat + Umleitung auf HTTPS'
sudo ufw allow 443/tcp comment 'Medienserver: HTTPS'
sudo ufw status numbered
```

„Could not delete non-existent rule“ heißt nur, dass die alte Testregel schon weg war.

> **Wichtig zu wissen:** Ports, die Docker veröffentlicht, umgehen UFW (Docker trägt seine
> Regeln direkt in iptables ein). Die beiden `ufw allow`-Regeln dokumentieren den gewollten
> Zustand und gelten auch beim Rollback auf systemd. Der eigentliche Schutz ist, dass in
> `compose.home.yaml` **nur Caddy** Ports veröffentlicht – niemals `ports:` bei `app` ergänzen.

### Schritt 6.3 – Am Server: Caddy starten

Vorher noch einmal `dig +short medienserver.michael-gonaus.dev` – die Adresse muss stimmen (Teil 3).

```bash
cd /opt/medienserver
sudo docker compose up -d
sudo docker compose ps
sudo docker compose logs -f caddy
```

Nach einigen Sekunden sollte `certificate obtained successfully` erscheinen. Mit `Strg+C` raus.
`app` und `caddy` laufen und sind **healthy**.

Kommt `timeout during connect` oder `connection refused`: Router-Regeln, `dig` und
`sudo ss -lntp | grep -E ':(80|443)\b'` (muss `docker-proxy` zeigen) prüfen.
Bleibt der Fehler, blockiert A1 eventuell 80/443 (bei 8765 hat es funktioniert). Dann
`sudo docker compose stop caddy` und melden – nicht oft wiederholen, Let's Encrypt sperrt
nach mehreren Fehlversuchen für eine Stunde.

---

## Teil 7: Funktionstest

### Schritt 7.1 – Am Server

```bash
sudo ss -lntp | grep -E ':(80|443|8000)\b'
curl -sI https://medienserver.michael-gonaus.dev/login | head -1
curl -sI http://medienserver.michael-gonaus.dev/login | grep -i '^location'
curl -sI https://medienserver.michael-gonaus.dev/media/videos/test.mp4 | grep -i '^location'
```

Erwartet:

- `docker-proxy` auf `0.0.0.0:80` und `0.0.0.0:443`; `127.0.0.1:8000` ist noch der alte systemd-Dienst.
- `HTTP/2 200`, dann Umleitung auf `https://…`, dann Umleitung auf `/login` (Videos ohne Anmeldung gesperrt).

Funktionieren die `curl`-Befehle aus dem Heimnetz nicht, fehlt dem Router NAT-Loopback –
das ist kein Fehler des Servers, dann am Handy prüfen.

### Schritt 7.2 – Am Handy mit mobilen Daten (WLAN aus)

| Test | OK? |
| --- | --- |
| `https://medienserver.michael-gonaus.dev` zeigt ein Schloss, keine Zertifikatswarnung | ☐ |
| Anmeldung mit vorhandenem Konto klappt, falsches Passwort wird abgelehnt | ☐ |
| Registrieren als Teilnehmer klappt, Trainer-Anfrage erscheint beim Admin | ☐ |
| Ohne Anmeldung: nur Login/Registrieren, auch Video-Links leiten auf Login um | ☐ |
| Video startet, Vorspulen und ±10-Sekunden-Tasten gehen, Ton ist da | ☐ |
| Trainer lädt ein Video hoch, Dauer und Vorschaubild werden erzeugt | ☐ |
| Teilnehmer sieht keinen Upload-Button | ☐ |
| Abmelden klappt | ☐ |

Erst wenn alles passt, weiter mit Teil 8.

---

## Teil 8: Erst jetzt den systemd-Dienst abschalten

```bash
sudo systemctl disable --now medienserver
systemctl is-active medienserver
sudo ss -lntp | grep ':8000\b'
```

Erwartet: `inactive` und keine Ausgabe für Port 8000. Die Dienstdatei bleibt liegen
(für den Rollback), Daten werden nicht angefasst.

Die Seite am Handy neu laden – sie läuft jetzt nur noch über Docker. Dann Neustart testen:

```bash
sudo reboot
```

1–2 Minuten warten, neu verbinden:

```bash
cd /opt/medienserver
sudo docker compose ps
```

Beide Container laufen wieder (`restart: unless-stopped`), die Seite ist erreichbar.

---

## Teil 9: Rollback auf den systemd-Dienst

Falls Docker Probleme macht – jederzeit möglich, die Daten bleiben unverändert:

```bash
cd /opt/medienserver
sudo docker compose down
sudo systemctl enable --now medienserver
systemctl is-active medienserver
```

- `docker compose down` entfernt nur Container und Netzwerke. `data/`, `uploads/`, `caddy/`
  und `.env` bleiben, weil sie Bind Mounts am Host sind.
- Danach läuft die App wieder nur auf `127.0.0.1:8000`, also **nicht** mehr von außen erreichbar.
  Router-Regeln deaktivieren, solange kein HTTPS davor steht.
- Die neuen Zeilen in `.env` stören den systemd-Dienst nicht. Die Originaldatei liegt
  zusätzlich in `/home/michael/env-vor-docker.bak`.
- Ist doch etwas mit den Daten passiert: Archiv aus Schritt 1.4 zurückspielen (Schritt 10.5).

Nie `docker compose down -v` oder `rm -rf data uploads` verwenden.

---

## Teil 10: Backup und Wiederherstellung

`deployment/backup-home.sh` sichert:

| Was | Wo am Server |
| --- | --- |
| Video-Metadaten (JSON je Video) | `data/videos/` |
| Benutzerkonten (JSON je Benutzer) | `data/users/` |
| Videos und Vorschaubilder | `uploads/` |
| Konfiguration mit `SECRET_KEY` | `.env` |
| Zertifikate und ACME-Konto von Caddy | `caddy/` |
| ddclient-Konfiguration (Namecheap Dynamic DNS) | `/etc/ddclient.conf` |

Zusätzlich steht im Archiv eine `MANIFEST.txt` mit Git-Stand und Docker-Images. Der Code
selbst ist auf GitHub, die Images lassen sich mit `docker compose build` jederzeit neu bauen –
nur die Daten sind einmalig.

**Konsistenz bei laufenden Uploads:** Das Skript kopiert zuerst alles bei laufender App in einen
Zwischenstand. Dann stoppt es nur den App-Container; Gunicorn nimmt laufende Uploads noch an
(bis zu 2 Minuten), bevor er sich beendet. Danach werden nur noch die Änderungen nachkopiert und die
App sofort wieder gestartet – die Unterbrechung dauert meist nur Sekunden. Erst dann wird das
Archiv gepackt. Im Archiv gibt es also kein Video ohne JSON und kein JSON ohne Video.

### Schritt 10.1 – Am Server: Backup von Hand

```bash
sudo bash /opt/medienserver/deployment/backup-home.sh
ls -lh /home/michael/medienserver-backups/
```

### Schritt 10.2 – Am Server: jede Nacht automatisch

```bash
sudo crontab -e
```

Ganz unten einfügen (`KEEP=7` = die letzten 7 Tage behalten, beliebig änderbar):

```
30 2 * * * KEEP=7 bash /opt/medienserver/deployment/backup-home.sh >> /var/log/medienserver-backup.log 2>&1
```

Am nächsten Tag: `sudo tail /var/log/medienserver-backup.log`.

Speicherbedarf: der Zwischenstand (`.staging`) plus jedes Archiv ist ungefähr so groß wie alle
Videos zusammen, bei `KEEP=7` also etwa 8× die Videos. Prüfen mit `df -h /home /opt`.
Wird es knapp, `KEEP` verkleinern.

### Schritt 10.3 – Am PC: Kopie holen (im Heimnetz)

Ein Backup auf derselben Festplatte hilft nicht, wenn die Festplatte kaputtgeht:

```powershell
mkdir C:\Users\Ich\Desktop\medienserver_backups -Force
scp "michael@10.0.0.19:medienserver-backups/medienserver-home_*.tar" C:\Users\Ich\Desktop\medienserver_backups\
```

Nicht in den Projektordner legen – das Archiv enthält `SECRET_KEY` und DNS-Zugangsdaten.

### Schritt 10.4 – Am Server: Archiv prüfen (ändert nichts)

```bash
ls -1t /home/michael/medienserver-backups/medienserver-home_*.tar | head -1
sudo bash /opt/medienserver/deployment/backup-home.sh check /home/michael/medienserver-backups/medienserver-home_DATUM_UHRZEIT.tar
```

Geprüft wird: Archiv vollständig lesbar, `.env` mit `SECRET_KEY` vorhanden, jede Video-JSON
gültig und ihre Video- und Bilddatei im Archiv, Benutzerkonten gültig, mindestens ein aktiver
Administrator. Am Ende steht `Archiv ist in Ordnung`. Empfehlung: einmal pro Woche.

### Schritt 10.5 – Am Server: Wiederherstellen

```bash
sudo bash /opt/medienserver/deployment/backup-home.sh restore /home/michael/medienserver-backups/medienserver-home_DATUM_UHRZEIT.tar
cd /opt/medienserver && sudo docker compose ps
```

Ablauf: Archiv prüfen → alle Container stoppen → bisherige `data/`, `uploads/`, `caddy/`, `.env`
nach `/opt/medienserver/vor-restore-<Zeitpunkt>/` **verschieben** (nichts wird gelöscht) →
Archiv auspacken (Besitzer und Rechte bleiben erhalten) → Container starten.
`/etc/ddclient.conf` wird nur zurückgeschrieben, wenn sie fehlt – eine vorhandene bleibt unverändert.

Erst wenn alles geprüft ist, darf der `vor-restore-…`-Ordner von Hand gelöscht werden.

**Wiederherstellung üben:** Einmal (z. B. vor der Präsentation, außerhalb der Nutzungszeit)
das neueste Archiv mit `restore` einspielen und Login + Video testen. Die Unterbrechung dauert
etwa eine Minute; falls etwas nicht passt, liegt der alte Stand im `vor-restore-…`-Ordner.

**Neuer Server (Festplatte kaputt):** Ubuntu + Docker installieren (Teil 2), Repo nach
`/opt/medienserver` klonen, Benutzer `medienserver` anlegen
(`sudo adduser --system --group --no-create-home --home /opt/medienserver medienserver`),
dann `restore` mit dem Archiv vom PC und `sudo docker compose up -d --build`.
ddclient installieren (`sudo apt install ddclient`, Fragen des Assistenten überspringen) –
`restore` hat `/etc/ddclient.conf` schon zurückgeschrieben; danach `sudo systemctl restart ddclient`. Hat
`medienserver` dort eine andere UID, `PUID` in `.env` anpassen und
`sudo chown -R medienserver:www-data data uploads` ausführen.

---

## Teil 11: Updates, Spickzettel, Rückweg

### Update einspielen

```powershell
# Am PC
git add .
git commit -m "Was geändert wurde"
git push
```

```bash
# Am Server – vorher Backup (Schritt 10.1)
cd /opt/medienserver
sudo -u medienserver git log --oneline -1          # alten Stand notieren
sudo -u medienserver git pull --ff-only
sudo docker compose up -d --build
sudo docker compose ps
```

### Rollback eines Updates

```bash
cd /opt/medienserver
sudo -u medienserver git checkout <alter-stand>      # Kennung aus "git log" von oben
sudo docker compose up -d --build
```

Später wieder auf den neuesten Stand: `sudo -u medienserver git checkout master`.
`git` und `docker compose build` verändern `data/` und `uploads/` nicht.

### Spickzettel (immer in `/opt/medienserver`)

| Was | Befehl |
| --- | --- |
| Status | `sudo docker compose ps` |
| Alles stoppen / starten | `sudo docker compose stop` / `sudo docker compose up -d` |
| App neu starten | `sudo docker compose restart app` |
| Logs App / Caddy | `sudo docker compose logs --tail=100 app` (bzw. `caddy`) |
| Dynamic DNS | `systemctl status ddclient --no-pager` und `dig +short medienserver.michael-gonaus.dev` |
| Benutzer anlegen | `sudo docker compose exec app flask --app app create-user NAME --role teilnehmer` |
| Passwort Admin zurücksetzen | `sudo docker compose exec app flask --app app set-password admin` |
| Speicher | `df -h /opt /home` und `sudo docker system df` |
| Alte Images aufräumen | `sudo docker image prune` (nicht `docker system prune -a --volumes`) |

### Was ist von außen offen?

| Stelle | Regel |
| --- | --- |
| Router | TCP 80 und 443 → `10.0.0.19`, sonst nichts, keine DMZ |
| Docker | nur Container `caddy` veröffentlicht 80/443; `app` hat keine Ports und kein Internet |
| UFW | `80/tcp`, `443/tcp`; bestehende Regeln (z. B. SSH im LAN) unverändert |
| SSH | **nicht** am Router freigegeben |
| DNS | nur der neue Eintrag `medien`; HSTS gilt nur für diese Subdomain |

### Sicherheit – was geschützt ist, was offen bleibt

- Ohne Anmeldung sind nur Login- und Registrierungsseite erreichbar. Vor jeder Video- und Bildauslieferung
  prüft Flask die Anmeldung; erst dann liefert Caddy die Datei.
- HTTPS mit automatischem Zertifikat, Login-Cookie nur über HTTPS (`Secure`), CSRF-Schutz,
  Passwörter nur als Hash, Selbstregistrierung nur als Teilnehmer, Flask-Debug aus.
- Container ohne Sonderrechte (`cap_drop: ALL`, `no-new-privileges`), App als `medienserver`.
- Keine Geheimnisse im Image oder in Git: `SECRET_KEY` in `.env` (`chmod 600`),
  Namecheap-Passwort nur in `/etc/ddclient.conf` am Host.
- **Offen:** Es gibt keine Sperre nach vielen falschen Passwörtern. Deshalb lange Passwörter
  und die Freigabe nach dem Testzeitraum wieder schließen.

### Rückweg: Medienserver wieder aus dem Internet nehmen

1. Router: Regeln `Medienserver` und `Medienserver-HTTP` deaktivieren.
2. Am Server:
   ```bash
   cd /opt/medienserver
   sudo docker compose down
   sudo ufw delete allow 80/tcp
   sudo ufw delete allow 443/tcp
   ```
3. DNS: Der Eintrag `medienserver.michael-gonaus.dev` kann bleiben (ohne Weiterleitung ist dahinter nichts erreichbar). Andere Einträge der Domain nicht anfassen.

---

## Teil 12: Zurück auf den Pi

Am Pi liegen die Daten in denselben Ordnern. Eingespielt werden nur `data/` und `uploads/` –
der Pi behält seine eigene `.env` (dort gibt es kein HTTPS). Archiv vom PC auf den Pi kopieren:

```powershell
scp C:\Users\Ich\Desktop\medienserver_backups\medienserver-home_DATUM_UHRZEIT.tar pi@10.0.13.3:/home/pi/
```

```bash
# Am Pi, als pi (ohne sudo, damit die Dateien pi gehören)
bash /home/pi/medienserver/deployment/backup.sh      # vorher den Pi-Stand sichern
sudo systemctl stop medienserver-docker
cd /home/pi/medienserver
tar -xf /home/pi/medienserver-home_DATUM_UHRZEIT.tar --strip-components=1 project/data project/uploads
chmod -R a+rX uploads
sudo systemctl start medienserver-docker
```

Gleichnamige Dateien am Pi (z. B. `data/users/admin.json`) werden dabei durch den Stand
vom Heimserver ersetzt – deshalb vorher das Pi-Backup. Videos haben eindeutige Namen und
kommen sich nicht in die Quere.
`chmod -R a+rX uploads` ist nötig, weil Nginx am Pi als eigener Benutzer liest und die
Dateien vom Heimserver nur für die Gruppe `www-data` lesbar sind.
Danach am Heimserver den Rückweg aus Teil 11 ausführen.
