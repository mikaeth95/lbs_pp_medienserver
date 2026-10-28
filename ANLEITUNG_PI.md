# Medienserver am Raspberry Pi – Schritt-für-Schritt-Anleitung

Diese Anleitung geht davon aus, dass am Pi bereits Folgendes erledigt ist:

- per SSH verbunden (`ssh pi@10.0.13.3`, siehe `rasberry_login.txt`)
- Git installiert, Ordner `/home/pi/medienserver` ist auf Branch `master`
- Docker läuft, die App ist unter `http://10.0.13.3/` erreichbar, ein Admin ist angelegt

Arbeitet die Teile der Reihe nach ab. Wo **Am PC** steht, ist PowerShell im Projektordner
`C:\Users\Ich\Desktop\lbs_poechlarn\PP\medien_server` gemeint, wo **Am Pi** steht, das SSH-Terminal.

| Teil | Inhalt | Pflicht? |
| --- | --- | --- |
| 1 | Neue Dateien vom PC auf GitHub und auf den Pi bringen | ja |
| 2 | Autostart beim Booten | ja |
| 3 | Test mit Mitschülern | ja |
| 4 | Backup | ja |
| 5 | Von zu Hause erreichbar (Cloudflare Tunnel) | nur wenn verlangt |
| 6 | Eigenes WLAN (Hotspot) | nur als Plan B |
| 7 | Projektdokumentation | ja |

---

## Teil 1: Neue Dateien auf GitHub und auf den Pi bringen

Neu im Projekt sind:

- `ANLEITUNG_PI.md` – diese Anleitung
- `deployment/medienserver-docker.service` – Autostart-Dienst
- `deployment/backup.sh` – Backup-Skript
- `compose.yaml` – zusätzlicher, optionaler Dienst `tunnel` (startet nur, wenn ihr ihn einschaltet)
- `.gitattributes` – sorgt dafür, dass Skripte auch von Windows aus mit Linux-Zeilenenden auf GitHub landen
- `rasberry_login.txt` – SSH-Anleitung

### Schritt 1.1 – Am PC: prüfen, was sich geändert hat

```powershell
git status
```

Es sollten die Dateien oben auftauchen, dazu eventuell eure eigenen Änderungen.
Kontrolliert kurz, dass **keine** `.env` und kein Passwort dabei ist.

### Schritt 1.2 – Am PC: committen und hochladen

```powershell
git add .
git commit -m "Autostart, Backup, Tunnel und Pi-Anleitung"
git push
```

### Schritt 1.3 – Am Pi: neuen Stand holen und neu starten

```bash
cd /home/pi/medienserver
git pull
sudo docker compose up --build -d
sudo docker compose ps
```

`app` und `nginx` sollten laufen, `app` nach ca. 30 Sekunden als `healthy`.
Ein Container `tunnel` taucht hier noch **nicht** auf, das ist richtig.

---

## Teil 2: Autostart beim Booten

### Schritt 2.1 – Am Pi: Docker beim Booten starten

```bash
sudo systemctl enable docker
```

### Schritt 2.2 – Am Pi: Autostart-Dienst installieren

```bash
sudo cp /home/pi/medienserver/deployment/medienserver-docker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now medienserver-docker.service
sudo systemctl status medienserver-docker.service
```

Erwartet: `Active: active (exited)`. Das ist bei diesem Diensttyp richtig.
Mit `q` kommt ihr aus der Anzeige raus.

> Falls ihr den Dienst vorher schon von Hand mit `sudo tee …` angelegt habt: der `cp`-Befehl
> überschreibt ihn einfach mit derselben Version, das ist in Ordnung.

### Schritt 2.3 – Am Pi: Neustart testen

```bash
sudo reboot
```

Die SSH-Verbindung bricht ab. Etwa 1–2 Minuten warten, dann neu verbinden:

```bash
ssh pi@10.0.13.3
cd /home/pi/medienserver
sudo docker compose ps
```

Beide Container laufen → Autostart funktioniert. Zusätzlich im Browser `http://10.0.13.3/` öffnen.

### Ab jetzt zum Steuern

| Was | Befehl |
| --- | --- |
| Stoppen | `sudo systemctl stop medienserver-docker` |
| Starten | `sudo systemctl start medienserver-docker` |
| Status | `sudo docker compose ps` (im Ordner `/home/pi/medienserver`) |
| Fehler ansehen | `sudo docker compose logs --tail=100 app nginx` |

---

## Teil 3: Test mit Mitschülern

Handy/Laptop der Testpersonen müssen im **selben Netz** wie der Pi sein (Schul-WLAN/LAN).

### Schritt 3.1 – Testkonten

1. Mitschüler A öffnet `http://10.0.13.3/` → **Registrieren** → Typ **Teilnehmer**.
2. Mitschüler B registriert sich als **Trainer**.
3. Ihr meldet euch als **admin** an → **Benutzer** → **Offene Trainer-Anfragen** → B freischalten.

### Schritt 3.2 – Checkliste

| Test | Gerät | OK? |
| --- | --- | --- |
| Teilnehmer kann sich anmelden und Katalog sehen | Handy | ☐ |
| Suche nach Titel findet das Video | Handy | ☐ |
| Filter Thema + Sprache funktionieren zusammen | Laptop | ☐ |
| Video startet, Vorspulen und ±10-Sekunden-Tasten gehen | Handy | ☐ |
| Vollbild geht | Handy + Tablet | ☐ |
| **Ton** ist zu hören (Video mit Tonspur verwenden!) | Laptop | ☐ |
| Teilnehmer sieht **keinen** Upload-Button | Handy | ☐ |
| Trainer kann Video hochladen, Dauer + Vorschaubild werden erzeugt | Laptop | ☐ |
| Trainer kann nur **eigene** Videos bearbeiten/löschen | Laptop | ☐ |
| 3–5 Personen schauen gleichzeitig, ruckelt es? | mehrere | ☐ |
| Nach `sudo reboot` ist alles wieder da | – | ☐ |

### Schritt 3.3 – Videos richtig vorbereiten

4K-Videos sind für den Pi und das WLAN zu groß. Am PC vor dem Upload auf 720p umwandeln
(`ffmpeg` liegt bei euch unter `tools\ffmpeg\bin\`):

```powershell
.\tools\ffmpeg\bin\ffmpeg.exe -i eingabe.mp4 -vf "scale=-2:720" -c:v libx264 -preset medium -crf 23 -c:a aac -b:a 128k -movflags +faststart ausgabe_720p.mp4
```

---

## Teil 4: Backup

Gesichert werden `data/` (Video-Infos, Benutzer), `uploads/` (Videos, Vorschaubilder) und `.env`.
Das Skript legt die Sicherungen unter `/home/pi/backups` ab und behält die **3 neuesten**.

### Schritt 4.1 – Am Pi: Backup einmal von Hand testen

```bash
bash /home/pi/medienserver/deployment/backup.sh
ls -lh /home/pi/backups
```

Erwartet: `Backup erstellt: /home/pi/backups/medienserver_….tar.gz (… MB)`.

> Wichtig: immer mit `bash …` aufrufen, nicht mit `./backup.sh`.
> So muss die Datei nicht ausführbar gemacht werden, und `git pull` bleibt konfliktfrei.

### Schritt 4.2 – Am Pi: jede Nacht automatisch sichern

```bash
crontab -e
```

Beim ersten Mal nach dem Editor fragen lassen → `1` (nano) wählen.
Ganz unten diese Zeile einfügen, dann `Strg+O`, `Enter`, `Strg+X`:

```
30 2 * * * bash /home/pi/medienserver/deployment/backup.sh >> /home/pi/backups/backup.log 2>&1
```

Das sichert jede Nacht um 02:30 Uhr. Prüfen:

```bash
crontab -l
```

Am nächsten Tag: `cat /home/pi/backups/backup.log`.

### Schritt 4.3 – Am PC: Sicherung vom Pi holen

Ein Backup auf derselben SD-Karte hilft nicht, wenn die SD-Karte kaputtgeht.
Darum ab und zu (z. B. vor jeder Präsentation) eine Kopie auf den PC holen:

```powershell
mkdir C:\Users\Ich\Desktop\medienserver_backups -Force
scp "pi@10.0.13.3:/home/pi/backups/medienserver_*.tar.gz" C:\Users\Ich\Desktop\medienserver_backups\
```

Den Backup-Ordner **nicht** in den Projektordner legen (sonst landet er womöglich auf GitHub,
und `.env` mit dem SECRET_KEY ist darin enthalten).

### Schritt 4.4 – Wiederherstellen (nur im Notfall)

```bash
sudo systemctl stop medienserver-docker
cd /home/pi/medienserver
ls /home/pi/backups
tar -xzf /home/pi/backups/medienserver_DATUM_UHRZEIT.tar.gz -C /home/pi/medienserver
sudo systemctl start medienserver-docker
```

`DATUM_UHRZEIT` durch den Namen aus `ls` ersetzen. Auf einem frisch aufgesetzten Pi vorher
das Repo klonen: `git clone https://github.com/mikaeth95/lbs_pp_medienserver.git /home/pi/medienserver`.

### Speicherplatz im Blick behalten

```bash
df -h /
sudo docker system df
```

Jede Sicherung ist ungefähr so groß wie alle Videos zusammen. Bei 3 Sicherungen braucht ihr also
etwa 4× den Platz eurer Videos. Wird es knapp: `KEEP=1` in der Crontab-Zeile davor schreiben
(`30 2 * * * KEEP=1 bash …`) oder alte Docker-Images löschen mit `sudo docker image prune`.

---

## Teil 5: Von zu Hause erreichbar (Cloudflare Tunnel) – nur wenn verlangt

**Vorher mit Herrn Haider / Herrn Höllein klären:** Reicht „im Schulnetz erreichbar“?
Dann diesen Teil überspringen.

Der Tunnel gibt euch eine öffentliche **https**-Adresse wie `https://irgendwas-zufaellig.trycloudflare.com`.
Kein Konto, keine Portfreigabe am Router nötig. Einschränkungen:

- Die Adresse **ändert sich bei jedem Neustart** des Tunnels. Nach einem Reboot neu nachsehen und weitergeben.
- Uploads über den Tunnel sind auf **100 MB** begrenzt (Grenze von Cloudflare). Große Videos im Schulnetz über `http://10.0.13.3/` hochladen.
- Die Seite ist dann aus dem ganzen Internet erreichbar. Starkes Admin-Passwort verwenden.
  Videos und Seiten sind nur nach Anmeldung sichtbar, Trainer müssen vom Admin freigeschaltet werden.
- Die Cloudflare-Bedingungen sind eigentlich nicht für viel Video-Traffic gedacht. Für ein kleines
  Schulprojekt ist das in der Praxis kein Problem, für einen Dauerbetrieb aber keine Lösung.

### Schritt 5.1 – Am Pi: Tunnel einschalten

```bash
cd /home/pi/medienserver
printf '\nCOMPOSE_PROFILES=tunnel\n' >> .env
sudo docker compose up -d
sudo docker compose ps
```

Jetzt laufen drei Container: `app`, `nginx`, `tunnel`. Durch den Eintrag in `.env` startet der Tunnel
auch beim Autostart mit.

### Schritt 5.2 – Am Pi: öffentliche Adresse anzeigen

Etwa 10 Sekunden warten, dann:

```bash
sudo docker compose logs tunnel | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1
```

Diese Adresse am Handy **mit mobilen Daten (WLAN aus)** testen. Wenn es geht, an die Mitschüler weitergeben.

Kommt keine Adresse: `sudo docker compose logs --tail=50 tunnel` ansehen und mir die Ausgabe schicken.

### Schritt 5.3 – Tunnel wieder ausschalten

```bash
cd /home/pi/medienserver
sed -i '/^COMPOSE_PROFILES=tunnel$/d' .env
sudo docker compose --profile tunnel down
sudo docker compose up -d
```

---

## Teil 6: Eigenes WLAN (Hotspot) – nur als Plan B

Nur nötig, wenn der Pi im Schulnetz nicht erreichbar ist (z. B. bei der Präsentation in einem anderen Raum).
Dann macht der Pi ein eigenes WLAN auf, und alle Geräte verbinden sich damit.

> **Achtung:** Ist der Pi selbst per WLAN mit dem Schulnetz verbunden, wird diese Verbindung durch den
> Hotspot ersetzt, und eure SSH-Sitzung bricht ab. Den Hotspot daher nur mit **LAN-Kabel**
> einrichten, oder direkt am Pi mit Bildschirm und Tastatur.

### Schritt 6.1 – Am Pi: prüfen, ob NetworkManager läuft und wie der Pi verbunden ist

```bash
systemctl is-active NetworkManager
nmcli device
```

- `active` → weiter mit 6.2.
- Bei `inactive` (älteres Raspberry Pi OS) funktioniert die Anleitung so nicht – meldet euch, dann gibt es eine andere.
- In der Liste seht ihr, ob `eth0` (Kabel) oder `wlan0` (WLAN) `connected` ist.

### Schritt 6.2 – Am Pi: Hotspot einschalten

Passwort mindestens 8 Zeichen, selbst wählen:

```bash
sudo nmcli device wifi hotspot ifname wlan0 ssid Medienserver password "EuerPasswort123"
```

Der Pi hat im Hotspot-WLAN die Adresse **10.42.0.1**. Mit dem Handy ins WLAN `Medienserver`
gehen und `http://10.42.0.1/` öffnen.

### Schritt 6.3 – Hotspot wieder ausschalten

```bash
sudo nmcli connection down Hotspot
```

Danach verbindet sich der Pi wieder mit dem normalen WLAN (bei WLAN-Betrieb eventuell `sudo reboot`).

---

## Teil 7: Projektdokumentation

### Schritt 7.1 – Arbeitspaket-Spezifikation (Punkt 5 der Aufgabenstellung)

Eine A4-Seite mit einer Tabelle. **NF** = Nachfolger, **VG** = Vorgänger (jeweils AP-Nr.).
Die Task-IDs stehen in OpenProject und teilweise schon in `Zeiterfassung_Medienserver.xlsx`
(z. B. 145, 217, 218, 224, 230–244). Titel, Zeiten und Abhängigkeiten aus OpenProject übernehmen.

Vorlage:

| Titel | Name | AP Nr (Task ID) | Zeit | NF | VG | Verantwortlicher |
| --- | --- | --- | --- | --- | --- | --- |
| | | | | | | |
| | | | | | | |
| | | | | | | |

Tipp: In OpenProject die Arbeitspaket-Liste mit den Spalten ID, Titel, geschätzte Zeit, Vorgänger,
Nachfolger, Verantwortlich filtern und als Excel/CSV exportieren – dann muss nichts abgetippt werden.

### Schritt 7.2 – Präsentation aktualisieren

`praesentation_stichwortzettel.md` beschreibt noch den alten Stand. Anpassen:

- **Anmeldung gibt es jetzt:** drei Rollen (Administrator, Trainer, Teilnehmer), Trainer werden vom Admin freigeschaltet.
  Die Punkte „Kein Benutzerkonto nötig“ und „Eine Anmeldung gibt es bisher nicht“ stimmen nicht mehr.
- **Tests:** jetzt 63 automatische Tests statt 12.
- **Pi-Inbetriebnahme:** läuft jetzt (Docker-Build funktioniert, Autostart per systemd, Update per `git pull`).
- **Backup:** nächtliches Backup mit `deployment/backup.sh`, Kopie auf den PC.
- Ergebnisse der Checkliste aus Teil 3 (Ton, Geräte, Anzahl gleichzeitiger Zuschauer) eintragen.

### Schritt 7.3 – Organisatorisches

- Protokollführer für das nächste Meeting festlegen.
- Zeiten in `Zeiterfassung_Medienserver.xlsx` nachtragen (die Einträge enden am 30.09.).
- `to_do.md` abhaken: Docker ✔, Autostart ✔, Benutzeranmeldung ✔, Backup ✔, Hotspot (Plan B) ✔.

---

## Kurzfassung für später

**Update einspielen**

```powershell
# Am PC
git add .
git commit -m "Was geändert wurde"
git push
```

```bash
# Am Pi
cd /home/pi/medienserver
git pull
sudo docker compose up --build -d
```

**Regeln**

- Kein `sudo` vor `git`.
- Am Pi nichts im Code ändern, nur am PC.
- `.env`, `data/`, `uploads/` sind nicht auf GitHub und dürfen dort auch nicht hin.
- Nicht `docker system prune -a` verwenden (löscht den Build-Cache, nächster Build dauert wieder Minuten).
