#!/usr/bin/env bash
# Meldet die aktuelle öffentliche IPv4 des Heimanschlusses an DuckDNS (DynDNS).
# Die A1-Adresse ändert sich ab und zu, der Name meinname.duckdns.org bleibt gleich.
# Aufruf am Heimserver (als root, z. B. per sudo crontab alle 5 Minuten):
#   bash /opt/medienserver/deployment/duckdns-update.sh
# Zugangsdaten stehen NICHT hier, sondern in /etc/medienserver/duckdns.env (chmod 600):
#   DUCKDNS_DOMAIN=meinname        (ohne .duckdns.org)
#   DUCKDNS_TOKEN=...              (von duckdns.org)
set -euo pipefail

CONFIG="${CONFIG:-/etc/medienserver/duckdns.env}"
# shellcheck disable=SC1090
source "$CONFIG"
: "${DUCKDNS_DOMAIN:?DUCKDNS_DOMAIN fehlt in $CONFIG}"
: "${DUCKDNS_TOKEN:?DUCKDNS_TOKEN fehlt in $CONFIG}"

# Leeres ip= : DuckDNS nimmt die Absenderadresse. -4 erzwingt IPv4.
# Die URL geht über stdin an curl (-K -), damit der Token nicht in der Prozessliste steht.
answer=$(printf 'url = "https://www.duckdns.org/update?domains=%s&token=%s&ip="\n' \
    "$DUCKDNS_DOMAIN" "$DUCKDNS_TOKEN" | curl -4 -fsS --max-time 30 -K -)

if [ "$answer" != "OK" ]; then
    echo "$(date '+%F %T') DuckDNS-Update fehlgeschlagen (Antwort: $answer)" >&2
    exit 1
fi
