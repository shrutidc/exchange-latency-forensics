#!/usr/bin/env bash
# Publish the live dashboard at a public HTTPS URL, free, with no account.
#
#   ./share.sh                       # uses VANTAGE below
#   VANTAGE="a Mac in Lubbock, TX" ./share.sh
#
# Starts live.py on localhost and opens a Cloudflare Quick Tunnel in front of
# it. Anyone with the printed URL can view the dashboard from any device.
# Ctrl-C stops both.
#
# What this exposes: a read-only page and one read-only JSON endpoint, both
# serving latency statistics about a public market-data feed. There is no
# upload path, no filesystem access and no other port. The tunnel reaches
# 127.0.0.1:PORT only -- it does not expose the rest of the machine.
#
# Limits worth knowing:
#   * The URL is random and CHANGES every restart. Quick Tunnels are meant for
#     demos and sharing, not a permanent address. For a stable domain, use a
#     named tunnel (needs a free Cloudflare account) or host it properly --
#     see the README.
#   * It is only reachable while this machine is awake and this script runs.
#     Closing the laptop takes the site down.
set -euo pipefail

PORT="${PORT:-8080}"
WINDOW_MINUTES="${WINDOW_MINUTES:-5}"
# Say where the measurement is actually taken from. These numbers describe
# THIS machine's link to the exchange, and a viewer elsewhere must not read
# them as their own. Keep it truthful.
VANTAGE="${VANTAGE:-this Mac, shared via Cloudflare}"

cd "$(dirname "$0")"
PY=".venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

command -v cloudflared >/dev/null || {
  echo "cloudflared not found. Install it with:  brew install cloudflared" >&2
  exit 1
}

cleanup() { kill ${SRV:-} ${TUN:-} 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "starting recorder + dashboard on 127.0.0.1:$PORT …"
HOST=127.0.0.1 PORT="$PORT" WINDOW_MINUTES="$WINDOW_MINUTES" VANTAGE="$VANTAGE" \
  "$PY" live.py > /tmp/latency-live.log 2>&1 &
SRV=$!

for _ in $(seq 1 30); do
  curl -sf -m 2 "http://127.0.0.1:$PORT/healthz" >/dev/null && break
  sleep 1
done
curl -sf -m 2 "http://127.0.0.1:$PORT/healthz" >/dev/null || {
  echo "server failed to start:" >&2; tail -20 /tmp/latency-live.log >&2; exit 1
}

echo "opening public tunnel …"
cloudflared tunnel --url "http://127.0.0.1:$PORT" --no-autoupdate \
  > /tmp/latency-tunnel.log 2>&1 &
TUN=$!

URL=""
for _ in $(seq 1 40); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/latency-tunnel.log | head -1 || true)
  [[ -n "$URL" ]] && break
  sleep 1
done

if [[ -z "$URL" ]]; then
  echo "tunnel did not come up:" >&2; tail -20 /tmp/latency-tunnel.log >&2; exit 1
fi

cat <<EOF

  ┌──────────────────────────────────────────────────────────────┐
     Live dashboard, shareable from any device:

       $URL

     Measured from : $VANTAGE
     Window        : ${WINDOW_MINUTES} minutes
     Logs          : /tmp/latency-live.log  /tmp/latency-tunnel.log

     Ctrl-C to stop. The URL changes each time you run this.
  └──────────────────────────────────────────────────────────────┘

EOF

wait $SRV
