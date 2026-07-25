"""Live market-data recorder for the Exchange Latency Forensics Lab.

Subscribes to the Coinbase Exchange public WebSocket feed and records every
message with a high-resolution local receive timestamp (time.time_ns(),
CLOCK_REALTIME in nanoseconds). Rows are batched and flushed to Parquet so a
long capture never has to fit in one in-memory dataframe.

The A/B experiment hook: every message is parsed twice, once with the stdlib
`json` parser and once with `orjson`, and both parse durations are recorded
per message. That gives a *paired* sample for the parse-path experiment —
same bytes, same moment, two treatments — which is far more statistically
efficient than comparing two separate capture sessions.

Usage:
    python recorder.py --minutes 5 --out data/capture
    python recorder.py --minutes 5 --products BTC-USD ETH-USD SOL-USD
"""

import argparse
import asyncio
import json
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import orjson
import pyarrow as pa
import pyarrow.parquet as pq
import websockets

WS_URL = "wss://ws-feed.exchange.coinbase.com"

SCHEMA = pa.schema(
    [
        ("recv_ns", pa.int64()),          # local wall-clock at message receipt, ns
        ("recv_mono_ns", pa.int64()),     # local monotonic clock, ns (for inter-arrival)
        ("exchange_time", pa.string()),   # exchange-side ISO8601 timestamp (µs precision)
        ("msg_type", pa.string()),
        ("product_id", pa.string()),
        ("trade_id", pa.int64()),
        ("price", pa.float64()),
        ("size", pa.float64()),
        ("side", pa.string()),
        ("sequence", pa.int64()),
        ("wire_bytes", pa.int32()),       # raw message size on the wire
        ("parse_json_ns", pa.int64()),    # stdlib json.loads duration (treatment A)
        ("parse_orjson_ns", pa.int64()),  # orjson.loads duration (treatment B)
    ]
)

BATCH_ROWS = 5000


class ParquetSink:
    """Append-only Parquet writer flushing in row batches."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.writer = pq.ParquetWriter(path, SCHEMA, compression="zstd")
        self.rows: list[dict] = []
        self.total = 0

    def add(self, row: dict):
        self.rows.append(row)
        if len(self.rows) >= BATCH_ROWS:
            self.flush()

    def flush(self):
        if not self.rows:
            return
        table = pa.Table.from_pylist(self.rows, schema=SCHEMA)
        self.writer.write_table(table)
        self.total += len(self.rows)
        self.rows.clear()

    def close(self):
        self.flush()
        self.writer.close()


async def record(products: list[str], minutes: float, out: Path):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out / f"ticks_{stamp}.parquet"
    sink = ParquetSink(path)

    subscribe = {
        "type": "subscribe",
        "product_ids": products,
        "channels": ["matches", "heartbeat"],
    }

    deadline = time.monotonic() + minutes * 60
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    print(f"recording {products} for {minutes} min -> {path}")
    n = 0
    async with websockets.connect(WS_URL, max_queue=None, compression=None) as ws:
        await ws.send(json.dumps(subscribe))
        while time.monotonic() < deadline and not stop.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            recv_ns = time.time_ns()
            recv_mono_ns = time.monotonic_ns()
            data = raw if isinstance(raw, bytes) else raw.encode()

            t0 = time.perf_counter_ns()
            msg = json.loads(data)
            t1 = time.perf_counter_ns()
            orjson.loads(data)
            t2 = time.perf_counter_ns()

            # Only live trades. `last_match` is the snapshot Coinbase sends once
            # per product at subscribe time; it carries the timestamp of a trade
            # that happened before we connected, so treating it as a latency
            # sample invents an arbitrarily large outlier.
            if msg.get("type") != "match":
                continue

            sink.add(
                {
                    "recv_ns": recv_ns,
                    "recv_mono_ns": recv_mono_ns,
                    "exchange_time": msg.get("time"),
                    "msg_type": msg["type"],
                    "product_id": msg.get("product_id"),
                    "trade_id": msg.get("trade_id"),
                    "price": float(msg.get("price") or 0),
                    "size": float(msg.get("size") or 0),
                    "side": msg.get("side"),
                    "sequence": msg.get("sequence"),
                    "wire_bytes": len(data),
                    "parse_json_ns": t1 - t0,
                    "parse_orjson_ns": t2 - t1,
                }
            )
            n += 1
            if n % 1000 == 0:
                print(f"  {n} trades recorded", flush=True)

    sink.close()
    print(f"done: {sink.total} trades -> {path}")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=5.0)
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument(
        "--products", nargs="+", default=["BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD"]
    )
    args = ap.parse_args()
    asyncio.run(record(args.products, args.minutes, args.out))


if __name__ == "__main__":
    main()
