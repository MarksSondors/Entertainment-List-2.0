#!/bin/sh
# GoAccess: live traffic dashboard built from Traefik's access log, served at
# https://<DOMAIN>/traefik/stats/ (behind the Traefik dashboard password).
#
# Traefik appends to $LOG forever, so it's emptied once it passes MAX_LOG_MB;
# GoAccess keeps the parsed stats in /data and carries on with the new lines.
set -e

LOG=/var/log/traefik/access.log
MAX_LOG_MB="${MAX_LOG_MB:-50}"
HOST=$(printf '%s' "$DOMAIN" | sed -e 's#^https\{0,1\}://##' -e 's#/*$##')

if [ -z "$HOST" ]; then
  echo "DOMAIN is not set" >&2
  exit 1
fi

touch "$LOG"

(
  while true; do
    sleep 600
    size=$(stat -c %s "$LOG" 2>/dev/null || echo 0)
    if [ "$size" -gt $((MAX_LOG_MB * 1024 * 1024)) ]; then
      : > "$LOG"
      echo "Emptied $LOG after it reached ${MAX_LOG_MB} MB (stats are kept in /data)"
    fi
  done
) &

exec goaccess "$LOG" \
  --log-format=TRAEFIKCLF \
  --real-time-html \
  --port=7890 \
  --ws-url="wss://${HOST}:443/traefik/stats/ws" \
  --origin="https://${HOST}" \
  --output=/report/index.html \
  --html-report-title="${HOST} traffic" \
  --persist --restore --db-path=/data --keep-last=90
