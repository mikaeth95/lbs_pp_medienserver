#!/bin/sh
# Meldet die öffentliche IPv4 des Heimanschlusses an den DNS-Anbieter (Dynamic DNS).
# Läuft als Dienst "ddns" in compose.home.yaml und prüft alle DDNS_INTERVAL Sekunden,
# ob sich die A1-Adresse geändert hat. Nur dann wird der Anbieter benachrichtigt.
#
# Einstellungen in /etc/medienserver/ddns.env (chmod 600, nie ins Repo):
#   DDNS_PROVIDER=namecheap   Domain liegt bei Namecheap BasicDNS/PremiumDNS
#     DDNS_HOST=medien          Subdomain (ergibt medien.example.com)
#     DDNS_DOMAIN=example.com
#     DDNS_PASSWORD=...         Namecheap → Advanced DNS → Dynamic DNS Password
#   DDNS_PROVIDER=duckdns     z. B. wenn medien.example.com per CNAME auf DuckDNS zeigt
#     DDNS_HOST=meinname        ohne .duckdns.org
#     DDNS_PASSWORD=...         DuckDNS-Token
set -eu

: "${DDNS_PROVIDER:?DDNS_PROVIDER fehlt}"
: "${DDNS_HOST:?DDNS_HOST fehlt}"
: "${DDNS_PASSWORD:?DDNS_PASSWORD fehlt}"
INTERVAL="${DDNS_INTERVAL:-300}"
IP_URL="${DDNS_IP_URL:-https://api.ipify.org}"

log() {
    echo "$(date '+%F %T') $*"
}

update() {
    ip="$1"
    case "$DDNS_PROVIDER" in
        namecheap)
            : "${DDNS_DOMAIN:?DDNS_DOMAIN fehlt}"
            url="https://dynamicdns.park-your-domain.com/update?host=${DDNS_HOST}&domain=${DDNS_DOMAIN}&password=${DDNS_PASSWORD}&ip=${ip}"
            expected="<ErrCount>0</ErrCount>"
            ;;
        duckdns)
            url="https://www.duckdns.org/update?domains=${DDNS_HOST}&token=${DDNS_PASSWORD}&ip=${ip}"
            expected="OK"
            ;;
        *)
            log "Unbekannter DDNS_PROVIDER '$DDNS_PROVIDER' (erlaubt: namecheap, duckdns)"
            exit 1
            ;;
    esac
    # Die URL geht über stdin an curl (-K -), damit das Passwort nicht in der Prozessliste steht.
    answer=$(printf 'url = "%s"\n' "$url" | curl -4 -fsS --max-time 30 -K -) || return 1
    case "$answer" in
        *"$expected"*) return 0 ;;
        # Antwort nicht ausgeben, sie kann Teile der Anfrage enthalten.
        *) return 1 ;;
    esac
}

last_ip=""
while true; do
    if ip=$(curl -4 -fsS --max-time 30 "$IP_URL") && [ -n "$ip" ]; then
        if [ "$ip" != "$last_ip" ]; then
            if update "$ip"; then
                log "DNS-Eintrag $DDNS_HOST ($DDNS_PROVIDER) zeigt jetzt auf $ip"
                last_ip="$ip"
            else
                log "Fehler: $DDNS_PROVIDER hat das Update abgelehnt oder ist nicht erreichbar"
            fi
        fi
    else
        log "Fehler: öffentliche IPv4 konnte nicht ermittelt werden"
    fi
    sleep "$INTERVAL"
done
