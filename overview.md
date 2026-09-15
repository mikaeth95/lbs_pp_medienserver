Medienserver

Projektziel
Im Projekt Medienserver entwickeln wir eine lokale Plattform für Schulungsvideos. Die Anwendung läuft auf einem Raspberry Pi und kann über den Browser auf Computern, Tablets und Smartphones geöffnet werden.
Funktionen
1. Videos hochladen und verwalten
2. Videos direkt im Browser abspielen
3. Suche nach Titel, Beschreibung und Thema
4. Filterung nach Thema und Sprache
5. Automatische Ermittlung von Dauer und Erscheinungsjahr
6. Automatische Erstellung eines Vorschaubildes
7. Bearbeiten und Löschen vorhandener Videos

Techstack
Backend: Python und Flask
Frontend: HTML, CSS und JavaScript
Datenspeicherung: JSON Dateien
Videoverarbeitung: FFmpeg und FFprobe
Webserver: Nginx und Gunicorn
Hardware: Raspberry Pi
Videoformat: MP4 mit H.264 Bild und AAC Ton

Ablauf
Ein Video wird über die Verwaltungsseite hochgeladen. Anschließend liest das System automatisch die technischen Informationen aus und erstellt ein Vorschaubild. Danach erscheint das Video im Katalog und kann von allen Geräten im lokalen Netzwerk angesehen werden.