Ich würde eine kleine, lokale Videothek als Webanwendung bauen: Raspberry Pi OS Lite + Nginx + Python/Flask + SQLite. Damit könnt ihr die Oberfläche genau auf Schulungsvideos, Themen und Sprachen abstimmen.

Die wichtigste technische Entscheidung: Der Pi liefert vorbereitete Videodateien aus; Notebook oder Smartphone übernehmen die Wiedergabe. Eine laufende Umwandlung der Videos – sogenanntes Transcoding – würde ich auf dem Pi 3 vermeiden.

Dafür würde ich diese Komponenten verwenden:

Baustein	Technik	Aufgabe
Betriebssystem	Raspberry Pi OS Lite	Betrieb ohne grafische Desktopoberfläche
Webserver	Nginx	Videos, Bilder und andere Dateien ausliefern
Anwendung	Python mit Flask und Gunicorn	Videokatalog, Suche und Verwaltung
Oberfläche	HTML/CSS und HTML5-Videoplayer	Bedienung auf Notebook, Tablet und Smartphone
Datenbank	SQLite	Kategorien und Metadaten speichern
Speicher	microSD fürs Betriebssystem, USB-Speicher für Videos	Betriebssystem und Videobestand getrennt halten

Raspberry Pi OS Lite ist für solche Server vorgesehen. Flask würde über Gunicorn hinter Nginx laufen; das entspricht dem vorgesehenen Aufbau für einen dauerhaft betriebenen Flask-Dienst. Raspberry-Pi-Dokumentation, Flask-Dokumentation

Die Website würde ich bewusst auf drei Ansichten begrenzen:

Videothek: Vorschaubilder, Suchfeld sowie kombinierbare Filter für Thema und Sprache. Beispielsweise „Excel“ und „Deutsch“.
Videoseite: Videoplayer, Titel, Beschreibung, Dauer, Erscheinungsjahr, Thema und Sprache.
Geschützte Verwaltung: Vorbereitete Videos hochladen, Metadaten bearbeiten und Vorschaubilder hinterlegen.

In SQLite stehen die Informationen und Dateipfade. Die Videos selbst liegen als Dateien auf dem USB-Speicher. Die Dauer lässt sich beim Import automatisch auslesen; Thema, Sprache und Erscheinungsjahr werden eingetragen.

Für die Videowiedergabe würde ich MP4 mit H.264-Video und AAC-Ton festlegen. Diese Kombination wird von den großen Browsern breit unterstützt. Die tatsächlichen Schulgeräte würde ich trotzdem früh testen. MDN: Videoformate

Falls eine Umwandlung nötig ist, erledigt ihr sie vor dem Upload auf einem PC mit FFmpeg. Dabei würde ich auch „Fast Start“ aktivieren, damit die für den Wiedergabestart benötigten Dateiinformationen vorne stehen. Nginx liefert anschließend die Videodaten direkt aus. Vor- und Zurückspringen muss über HTTP-Teilabrufe funktionieren und gehört ausdrücklich in euren Funktionstest. FFmpeg-Dokumentation

Für die Netzwerkintegration würde ich den Pi per LAN-Kabel anschließen und mit der Schul-IT eine feste DHCP-Zuordnung sowie einen lokalen Namen vereinbaren. Die Benutzer öffnen die Website im Browser. Das erfüllt eure Vorgabe „Browser oder App“ bereits.

Für einen zuverlässigen Betrieb würde ich zusätzlich einrichten:

Automatischer Start der Anwendung und Einbindung des USB-Speichers nach einem Neustart.
Passwortgeschützte Verwaltung; HTTPS für die Anmeldung.
Sicherung von Datenbank, Videos und Konfiguration einschließlich eines Wiederherstellungstests.

Jellyfin ist eine mögliche Alternative, wenn ihr möglichst wenig selbst programmieren möchtet. Allerdings stuft die offizielle Dokumentation Raspberry Pis als problematische Serverhardware ein. Deshalb würde ich Jellyfin auf eurem Pi 3 erst mit repräsentativen Videos und mehreren Geräten erproben. Meine Empfehlung für die kleine Eigenentwicklung folgt aus dieser Hardwaregrenze und euren überschaubaren Funktionen. Jellyfin: Hardwareauswahl

Bei euren zwei Stunden pro Woche würde ich so beginnen:

Pi einrichten und ein Video im Browser eines zweiten Geräts abspielen.
Wiedergabe, Ton und Springen auf Notebook und Smartphone prüfen.
Videokatalog mit Themen, DE/EN und Metadaten ergänzen.
Verwaltung, Autostart und Sicherung fertigstellen.
Mehrere gleichzeitige Wiedergaben testen und die gemessenen Grenzen dokumentieren.

Die technische Dokumentation würde ich von Anfang an mitschreiben. Daraus entstehen anschließend das Benutzerhandbuch auf Deutsch und Englisch und eure Präsentation. Für die vollständige Projektplanung fehlt auf diesem Foto noch Anlage A mit den Projektmanagement-Anforderungen.