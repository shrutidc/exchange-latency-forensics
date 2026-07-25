"""Split network latency from local processing latency.

Reads a tcpdump capture (kernel arrival timestamps) and the Parquet tick
capture (application receive timestamps) and pairs them in time order to
estimate how long each message spent between "kernel saw the bytes" and
"Python had a parsed object".

This is deliberately an *estimate*, and the honest framing matters: the feed
is TLS, so we cannot match a specific packet to a specific trade message by
content. What we can do is compare the two arrival-time streams. For each
application receive timestamp we find the nearest preceding inbound packet
from the feed's server and take the difference. When one TCP segment carries
several messages, or one message spans segments, individual pairings are
wrong -- but the *distribution* of the gap still bounds local processing
cost, and its p99 is the number worth acting on.

Requires tshark (brew install wireshark) for pcap parsing, or falls back to
tcpdump -r text output.

Usage:
    python pcap_join.py --pcap data/capture.pcap --data data
"""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import duckdb
import numpy as np

from analyze import complete_captures


def read_pcap_times(pcap: Path) -> np.ndarray:
    """Return inbound packet arrival times as ns since epoch."""
    if shutil.which("tshark"):
        out = subprocess.run(
            ["tshark", "-r", str(pcap), "-T", "fields", "-e", "frame.time_epoch",
             "-Y", "tcp.len > 0"],
            capture_output=True, text=True, check=True,
        ).stdout
        vals = [float(line) for line in out.split() if line]
    elif shutil.which("tcpdump"):
        out = subprocess.run(
            ["tcpdump", "-r", str(pcap), "-n", "-tt", "--time-stamp-precision=nano"],
            capture_output=True, text=True, check=True,
        ).stdout
        vals = []
        for line in out.splitlines():
            tok = line.split(" ", 1)[0]
            try:
                vals.append(float(tok))
            except ValueError:
                continue
    else:
        raise SystemExit("need tshark or tcpdump to read the pcap")

    if not vals:
        raise SystemExit(f"no packets with payload found in {pcap}")
    return (np.array(vals) * 1e9).astype(np.int64)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pcap", type=Path, required=True)
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--out", type=Path, default=Path("pcap_results.json"))
    args = ap.parse_args()

    pkt_ns = np.sort(read_pcap_times(args.pcap))
    files = "', '".join(complete_captures(args.data))
    recv_ns = duckdb.sql(
        f"SELECT recv_ns FROM read_parquet(['{files}']) WHERE msg_type = 'match' "
        f"ORDER BY recv_ns"
    ).df()["recv_ns"].to_numpy()

    # Only compare over the window both captures cover.
    lo = max(pkt_ns[0], recv_ns[0])
    hi = min(pkt_ns[-1], recv_ns[-1])
    recv_ns = recv_ns[(recv_ns >= lo) & (recv_ns <= hi)]
    if recv_ns.size == 0:
        raise SystemExit(
            "pcap and tick capture do not overlap in time -- run them concurrently"
        )

    # Nearest preceding packet for each application receive timestamp.
    idx = np.searchsorted(pkt_ns, recv_ns, side="right") - 1
    valid = idx >= 0
    gap_us = (recv_ns[valid] - pkt_ns[idx[valid]]) / 1e3

    res = {
        "packets": int(pkt_ns.size),
        "messages_matched": int(gap_us.size),
        "window_s": float((hi - lo) / 1e9),
        "processing_gap_us": {
            "p50": float(np.percentile(gap_us, 50)),
            "p90": float(np.percentile(gap_us, 90)),
            "p99": float(np.percentile(gap_us, 99)),
            "p999": float(np.percentile(gap_us, 99.9)),
            "max": float(np.max(gap_us)),
        },
        "caveat": (
            "TLS prevents packet-to-message matching by content; this is a "
            "nearest-preceding-packet estimate. Treat the distribution as a "
            "bound on local kernel-to-userspace processing cost, not as a "
            "per-message truth."
        ),
    }
    args.out.write_text(json.dumps(res, indent=2))
    g = res["processing_gap_us"]
    print(f"packets={res['packets']} matched={res['messages_matched']}")
    print(f"kernel->app gap us  p50={g['p50']:.1f}  p99={g['p99']:.1f}  max={g['max']:.1f}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
