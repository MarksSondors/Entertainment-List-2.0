#!/bin/sh
# GoAccess: live traffic dashboard built from Traefik's JSON access log, served at
# https://<DOMAIN>/traefik/stats/ (behind the Traefik dashboard password).
#
# - Traefik appends to $LOG forever, so it's emptied once it passes MAX_LOG_MB;
#   GoAccess keeps the parsed stats in /data and carries on with the new lines.
# - Countries come from the free DB-IP "IP to Country Lite" database
#   (CC BY 4.0, https://db-ip.com), refreshed monthly.
# - "Page types" and "Signed-in users" come from the X-Entlist-View and
#   X-Entlist-User response headers set by Django (AccessLogTagsMiddleware).
set -e

LOG=/var/log/traefik/access.log
MAX_LOG_MB="${MAX_LOG_MB:-50}"
GEO_DB=/data/country.mmdb
HOST=$(printf '%s' "$DOMAIN" | sed -e 's#^https\{0,1\}://##' -e 's#/*$##')

if [ -z "$HOST" ]; then
  echo "DOMAIN is not set" >&2
  exit 1
fi

touch "$LOG"

# GoAccess only parses JSON lines; drop anything left over from the old
# plain-text log format (rewritten in place so Traefik keeps its file handle).
if grep -qv '^{' "$LOG"; then
  grep '^{' "$LOG" > /tmp/access.json || true
  cat /tmp/access.json > "$LOG"
  rm -f /tmp/access.json
  echo "Removed non-JSON lines from $LOG"
fi

geo_db_is_stale() {
  [ ! -s "$GEO_DB" ] || [ -n "$(find "$GEO_DB" -mtime +32)" ]
}

download_geo_db() {
  this_month=$(date -u +%Y-%m)
  last_month=$(date -u -d "@$(( $(date +%s) - 31 * 86400 ))" +%Y-%m)
  for month in "$this_month" "$last_month"; do
    if wget -q -O /tmp/country.mmdb.gz "https://download.db-ip.com/free/dbip-country-lite-${month}.mmdb.gz" \
      && gunzip -f /tmp/country.mmdb.gz; then
      mv /tmp/country.mmdb "$GEO_DB"
      echo "Downloaded country database ($month)"
      return 0
    fi
  done
  rm -f /tmp/country.mmdb.gz /tmp/country.mmdb
  return 1
}

if geo_db_is_stale; then
  download_geo_db || echo "Country database download failed; continuing without it"
fi

# Served next to the report by nginx; panel renames for the HTML report.
cp /goaccess-labels.js /report/labels.js

(
  set +e
  checks=0
  while true; do
    sleep 600
    checks=$((checks + 1))

    size=$(stat -c %s "$LOG" 2>/dev/null || echo 0)
    if [ "$size" -gt $((MAX_LOG_MB * 1024 * 1024)) ]; then
      : > "$LOG"
      echo "Emptied $LOG after it reached ${MAX_LOG_MB} MB (stats are kept in /data)"
    fi

    # Once a day: refresh a month-old country database, then restart GoAccess
    # (it saves its stats on SIGTERM and Docker starts it again) to load it.
    if [ $((checks % 144)) -eq 0 ] && geo_db_is_stale && download_geo_db; then
      kill -TERM 1
    fi
  done
) &

set --
if [ -s "$GEO_DB" ]; then
  set -- --geoip-database="$GEO_DB"
fi

exec goaccess "$LOG" \
  --log-format='{"ClientHost":"%h","time":"%x","RequestMethod":"%m","RequestPath":"%U","RequestProtocol":"%H","DownstreamStatus":"%s","DownstreamContentSize":"%b","Duration":"%n","request_User-Agent":"%u","request_Referer":"%R","downstream_X-Entlist-View":"%v","downstream_X-Entlist-User":"%e"}' \
  --datetime-format='%Y-%m-%dT%H:%M:%SZ' \
  --num-tests=0 \
  "$@" \
  --sort-panel=VIRTUAL_HOSTS,BY_CUMTS,DESC \
  --html-custom-js=labels.js \
  --real-time-html \
  --port=7890 \
  --ws-url="wss://${HOST}:443/traefik/stats/ws" \
  --origin="https://${HOST}" \
  --output=/report/index.html \
  --html-report-title="${HOST} traffic" \
  --persist --restore --db-path=/data --keep-last=90
