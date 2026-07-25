#!/usr/bin/env bash
# Packet-level capture for the Exchange Latency Forensics Lab.
#
# WHY: recorder.py timestamps a message when the Python interpreter hands it
# to us -- after the kernel received it, after TLS decryption, after the
# websockets library reassembled the frame. tcpdump timestamps the packet
# when the kernel sees it on the wire. The gap between the two IS your
# local processing latency, separated from network latency.
#
# This needs root to open the capture device, so run it yourself:
#
#     sudo ./pcap_capture.sh 300 data/capture.pcap
#
# Run it in one terminal and recorder.py in another, over the same window.
# Then: python pcap_join.py --pcap data/capture.pcap --data data
#
set -euo pipefail

SECONDS_TO_RUN="${1:-300}"
OUT="${2:-data/capture.pcap}"
IFACE="${IFACE:-$(route get default 2>/dev/null | awk '/interface:/{print $2}')}"

mkdir -p "$(dirname "$OUT")"

if [[ $EUID -ne 0 ]]; then
  echo "error: needs root to capture. Re-run:  sudo $0 $*" >&2
  exit 1
fi

echo "interface : $IFACE"
echo "duration  : ${SECONDS_TO_RUN}s"
echo "output    : $OUT"
echo

# Port 443 only, and only inbound to us: the exchange feed is TLS. We cannot
# read payloads (that's the point of TLS) but we get exact kernel arrival
# timestamps and byte counts, which is all the network-vs-processing split
# needs. -n skips DNS, --time-stamp-precision=nano gives ns resolution,
# -s 96 keeps headers only so the file stays small.
timeout "${SECONDS_TO_RUN}" tcpdump \
  -i "$IFACE" \
  -n \
  -s 96 \
  --time-stamp-precision=nano \
  -w "$OUT" \
  'tcp port 443 and inbound' || true

echo
echo "wrote $OUT"
ls -lh "$OUT"
echo
echo "next: python pcap_join.py --pcap $OUT --data data"
