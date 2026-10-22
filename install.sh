#!/bin/sh
# Install JobScout on a Linux server (run as root next to jobscout.py and .env).
# Sends the digest every day at 11:00 Moscow time, regardless of the server time zone.
set -e

DIR=/opt/jobscout
mkdir -p "$DIR"
cp "$(dirname "$0")/jobscout.py" "$DIR/"
[ -f "$(dirname "$0")/.env" ] && cp "$(dirname "$0")/.env" "$DIR/.env" && chmod 600 "$DIR/.env"
chmod 700 "$DIR"

command -v python3 >/dev/null || { apt-get update && apt-get install -y python3; }

cat > /etc/cron.d/jobscout <<'EOF'
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 * * * * root [ "$(TZ=Europe/Moscow date +\%H)" = "11" ] && cd /opt/jobscout && python3 jobscout.py --log
EOF
chmod 644 /etc/cron.d/jobscout

echo "Dry run (nothing is sent):"
cd "$DIR" && python3 jobscout.py --dry-run 2>&1 | tail -n 25
echo
echo "Done. Send now: cd $DIR && python3 jobscout.py   Log: $DIR/log.txt   Disable: rm /etc/cron.d/jobscout"
