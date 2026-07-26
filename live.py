"""Live latency dashboard for the Exchange Latency Forensics Lab.

Runs the recorder and the analysis in one process and serves a page that
refreshes itself, so you watch the distribution move instead of reading a
snapshot after the fact.

    python live.py                       # http://127.0.0.1:8420
    python live.py --window 10 --save    # 10-minute window, also write Parquet

How it differs from the batch pipeline:

* Statistics are computed over a ROLLING WINDOW (default 5 minutes) rather
  than a whole capture, so the page describes the feed as it is now. Messages
  older than the window fall out of the ring buffer.
* Everything is one recording session by construction, which means the
  cross-session clock-offset problem that `analyze.py --session all` guards
  against cannot arise here. The constant offset is still present and still
  disclosed; it just cannot vary mid-page.
* The charts are the same code as the static dashboard. `dashboard.py` owns
  the rendering; this module only supplies data in the same shape, so the two
  views can never drift apart visually.

The Parquet writer is only touched when --save is passed: a ParquetWriter
does not write its footer until close, so a file being appended to by a live
session is unreadable until the session ends. The in-memory ring buffer, not
the file, is what the page reads.
"""

import argparse
import asyncio
import json
import threading
import time
from collections import deque
from datetime import datetime, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import orjson
import websockets

from dashboard import render_page
from recorder import WS_URL, ParquetSink

LIVE_BOOT = """
const statusEl = document.createElement('div');
statusEl.className = 'livebar';
document.querySelector('.wrap').insertBefore(statusEl, document.getElementById('app'));

let hovering = false, paused = false, lastOk = 0, failures = 0;
document.addEventListener('mouseover', e => {
  if (e.target.closest('.chartbox')) hovering = true;
});
document.addEventListener('mouseout', e => {
  if (e.target.closest('.chartbox') && !e.relatedTarget?.closest?.('.chartbox')) hovering = false;
});

function setStatus(state, detail) {
  const age = lastOk ? Math.round((Date.now() - lastOk) / 1000) : null;
  statusEl.innerHTML =
    `<span class="pulse ${state}"></span><b>${detail}</b>`
    + (age !== null ? `<span class="dim">updated ${age}s ago</span>` : '')
    + `<button id="pausebtn">${paused ? 'Resume' : 'Pause'}</button>`;
  document.getElementById('pausebtn').onclick = () => { paused = !paused; setStatus(state, detail); };
}

async function tick() {
  // Do not yank a chart out from under a reader mid-hover, and do not fight
  // an explicit pause.
  if (paused || hovering) return;
  try {
    const res = await fetch('/api/stats', { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    failures = 0; lastOk = Date.now();
    if (data.warming_up) {
      setStatus('warm', `Collecting data — ${data.messages} messages so far`);
      return;
    }
    R = data;
    build();
    setStatus('live', `Live · ${data.capture.messages.toLocaleString()} messages in the last `
      + `${Math.round(data.capture.duration_s)}s`);
  } catch (err) {
    failures++;
    // Say it plainly rather than showing stale numbers as if they were current.
    setStatus('down', failures > 2 ? 'Disconnected from the recorder' : 'Reconnecting…');
  }
}
setStatus('warm', 'Connecting…');
tick();
setInterval(tick, 1000);
"""

LIVE_CSS = """
  .livebar {
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 999px; padding: 9px 18px; margin: 0 0 18px;
    font-size: 13.5px; color: var(--text-primary); width: fit-content;
  }
  .livebar .dim { color: var(--muted); }
  .livebar button {
    font: inherit; font-size: 12.5px; cursor: pointer; color: var(--text-secondary);
    background: transparent; border: 1px solid var(--border);
    border-radius: 999px; padding: 3px 12px;
  }
  .livebar button:hover { color: var(--text-primary); }
  .pulse { width: 9px; height: 9px; border-radius: 50%; flex: none; background: var(--muted); }
  .pulse.live { background: var(--good); animation: bp 2s ease-in-out infinite; }
  .pulse.warm { background: var(--warning); }
  .pulse.down { background: var(--critical); }
  @keyframes bp { 0%,100% { opacity: 1 } 50% { opacity: .35 } }
  @media (prefers-reduced-motion: reduce) { .pulse.live { animation: none } }
"""


class Window:
    """Thread-safe rolling window of recent messages.

    The WebSocket client (asyncio thread) appends; the HTTP handler threads
    read. Everything is plain lists of scalars so a snapshot is a cheap copy
    under the lock, and the numpy work happens outside it.
    """

    def __init__(self, seconds: float):
        self.seconds = seconds
        self.rows = deque()
        self.lock = threading.Lock()
        self.total_seen = 0
        self.started = time.time()

    def add(self, row: dict):
        with self.lock:
            self.rows.append(row)
            self.total_seen += 1
            cutoff = row["recv_ns"] - int(self.seconds * 1e9)
            while self.rows and self.rows[0]["recv_ns"] < cutoff:
                self.rows.popleft()

    def snapshot(self) -> list[dict]:
        with self.lock:
            return list(self.rows)


def quantiles(a: np.ndarray, unit: str) -> dict:
    return {
        "unit": unit, "count": int(a.size),
        "min": float(a.min()), "p50": float(np.percentile(a, 50)),
        "p90": float(np.percentile(a, 90)), "p99": float(np.percentile(a, 99)),
        "p999": float(np.percentile(a, 99.9)), "max": float(a.max()),
        "mean": float(a.mean()),
    }


def compute(rows: list[dict]) -> dict:
    """Build a results.json-shaped payload from the current window.

    Mirrors analyze.py deliberately -- same keys, same units, same guards --
    so the shared renderer needs no live-specific branches.
    """
    MIN_ROWS = 20
    if len(rows) < MIN_ROWS:
        return {"warming_up": True, "messages": len(rows), "need": MIN_ROWS}

    lat = np.array([r["latency_ms"] for r in rows])
    recv = np.array([r["recv_ns"] for r in rows])
    out = {
        "capture": {
            "files": ["(live)"],
            "products": sorted({r["product_id"] for r in rows if r["product_id"]}),
            "start_utc": datetime.fromtimestamp(recv.min() / 1e9, timezone.utc)
                .strftime("%Y-%m-%d %H:%M:%S"),
            "end_utc": datetime.fromtimestamp(recv.max() / 1e9, timezone.utc)
                .strftime("%Y-%m-%d %H:%M:%S"),
            "duration_s": float((recv.max() - recv.min()) / 1e9),
            "messages": len(rows),
        },
        "latency": quantiles(lat, "ms"),
    }

    by_product = {}
    for p in out["capture"]["products"]:
        v = np.array([r["latency_ms"] for r in rows if r["product_id"] == p])
        if v.size:
            by_product[p] = quantiles(v, "ms")
    out["latency_by_product"] = by_product

    # Same log/linear choice and pinned endpoints as analyze.py, for the same
    # reasons: log bins need positive values, and logspace can round-trip the
    # outer edges just inside the data range and drop the min/max rows.
    scale = "log" if lat.min() > 0 else "linear"
    bins = (np.logspace(np.log10(lat.min()), np.log10(lat.max()), 60) if scale == "log"
            else np.linspace(lat.min(), lat.max(), 60))
    bins[0], bins[-1] = lat.min(), np.nextafter(lat.max(), np.inf)
    counts, edges = np.histogram(lat, bins=bins)
    out["histogram"] = {"scale": scale, "edges_ms": edges.tolist(),
                        "counts": counts.tolist()}

    # Per-second series.
    sec = (recv // 1_000_000_000).astype(np.int64)
    uniq = np.unique(sec)
    t0 = uniq.min()
    msgs, p50s, p99s, maxs = [], [], [], []
    for s in uniq:
        v = lat[sec == s]
        msgs.append(int(v.size))
        p50s.append(round(float(np.percentile(v, 50)), 3))
        p99s.append(round(float(np.percentile(v, 99)), 3))
        maxs.append(round(float(v.max()), 3))
    out["timeseries"] = {"t_s": (uniq - t0).astype(int).tolist(), "msgs": msgs,
                         "p50_ms": p50s, "p99_ms": p99s, "max_ms": maxs}

    if uniq.size > 10:
        rho, pv = _spearman(np.array(msgs, float), np.array(p99s, float))
        out["volume_correlation"] = {
            "spearman_rho": rho, "p_value": pv, "n_seconds": int(uniq.size),
            "interpretation": ("latency rises with volume" if rho > 0.2 and pv < 0.05
                               else "latency falls with volume" if rho < -0.2 and pv < 0.05
                               else "no strong volume dependence in this window"),
        }

    out["bursts"] = _bursts(recv, lat, out["latency"]["p50"])

    a = np.array([r["parse_json_ns"] for r in rows]) / 1e3
    b = np.array([r["parse_orjson_ns"] for r in rows]) / 1e3
    w_p = _wilcoxon_p(a, b)
    out["experiment"] = {
        "name": "parse path: stdlib json (A) vs orjson (B), paired per message",
        "n_pairs": int(a.size),
        "a_json": quantiles(a, "us"), "b_orjson": quantiles(b, "us"),
        "median_speedup": float(np.median(a) / np.median(b)),
        "median_delta_us": float(np.median(a - b)),
        "wilcoxon_p": w_p, "mannwhitney_p": w_p,
        "significant": bool(w_p < 0.001),
    }

    out["clock"] = {
        "negative_latency_rows": int((lat < 0).sum()),
        "sessions": None, "pool_warning": None,
        "note": ("Latency is local wall clock minus exchange timestamp. Neither "
                 "side is PTP-synced, so a constant NTP offset of up to a few ms "
                 "shifts the whole distribution. This is a single live session, so "
                 "that offset is constant across everything shown here and cannot "
                 "vary between the numbers above."),
    }
    return out


def _bursts(recv: np.ndarray, lat: np.ndarray, p50: float) -> dict:
    """Burst stats at the BURST level -- see analyze.py for why per-message
    correlation of position vs latency is an artifact."""
    order = np.argsort(recv)
    recv, lat = recv[order], lat[order]
    gap = np.diff(recv, prepend=recv[0] - 10**9)
    bid = np.cumsum(gap > 1_000_000)

    ids, starts = np.unique(bid, return_index=True)
    sizes = np.diff(np.append(starts, len(bid)))
    med = np.array([np.median(lat[s:s + n]) for s, n in zip(starts, sizes)])
    spread = np.array([lat[s:s + n].max() - lat[s:s + n].min()
                       for s, n in zip(starts, sizes)])

    buckets = [("1 message", 1, 1), ("2", 2, 2), ("3-5", 3, 5),
               ("6-10", 6, 10), ("11 or more", 11, 10**9)]
    by_size = []
    for label, lo, hi in buckets:
        m = (sizes >= lo) & (sizes <= hi)
        if not m.any():
            continue
        by_size.append({
            "label": label, "bursts": int(m.sum()),
            "messages": int(sizes[m].sum()),
            "p50_ms": float(np.median(med[m])),
            "p90_ms": float(np.percentile(med[m], 90)),
            "max_ms": float(med[m].max()),
        })

    rho, pv = _spearman(sizes.astype(float), med) if sizes.size > 2 else (0.0, 1.0)
    multi = spread[sizes > 1]
    as_unit = bool(multi.size and float(np.percentile(multi, 90)) < 0.1 * p50)
    effect = ("negligible" if abs(rho) < 0.2 else "weak" if abs(rho) < 0.4
              else "moderate" if abs(rho) < 0.6 else "strong")
    return {
        "n_bursts": int(sizes.size), "max_burst_size": int(sizes.max()),
        "pct_msgs_in_bursts": float(100 * (len(lat) - sizes.size) / len(lat)),
        "latency_by_burst_size": by_size,
        "size_correlation": {"spearman_rho": rho, "p_value": pv,
                             "n_bursts": int(sizes.size), "effect": effect,
                             "significant": bool(pv < 0.05)},
        "within_burst_spread_ms": ({"p50": float(np.percentile(multi, 50)),
                                    "p90": float(np.percentile(multi, 90)),
                                    "max": float(multi.max()),
                                    "n_multi_message_bursts": int(multi.size)}
                                   if multi.size else None),
        "delivered_as_unit": as_unit,
        "verdict": "live window",
    }


def _rank(x: np.ndarray) -> np.ndarray:
    """Average ranks, matching scipy's tie handling."""
    order = np.argsort(x, kind="mergesort")
    r = np.empty(x.size, float)
    sx = x[order]
    i = 0
    while i < x.size:
        j = i
        while j + 1 < x.size and sx[j + 1] == sx[i]:
            j += 1
        r[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return r


def _spearman(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """Spearman rho with a normal-approximation p-value.

    scipy is used by analyze.py, but this runs on every poll, so it stays in
    numpy. The approximation is fine here: n is in the hundreds or thousands,
    and the page only ever reads the exponent.
    """
    n = a.size
    if n < 3:
        return 0.0, 1.0
    ra, rb = _rank(a), _rank(b)
    ra -= ra.mean(); rb -= rb.mean()
    denom = np.sqrt((ra ** 2).sum() * (rb ** 2).sum())
    if denom == 0:
        return 0.0, 1.0
    rho = float((ra * rb).sum() / denom)
    rho = max(-1.0, min(1.0, rho))
    if abs(rho) >= 1.0:
        return rho, 0.0
    t = abs(rho) * np.sqrt((n - 2) / (1 - rho ** 2))
    return rho, float(2 * _norm_sf(t))


def _norm_sf(z: float) -> float:
    """Upper-tail normal probability via erfc."""
    import math
    return 0.5 * math.erfc(z / math.sqrt(2))


def _wilcoxon_p(a: np.ndarray, b: np.ndarray) -> float:
    """Two-sided Wilcoxon signed-rank p-value, normal approximation."""
    d = a - b
    d = d[d != 0]
    n = d.size
    if n < 10:
        return 1.0
    r = _rank(np.abs(d))
    w = r[d > 0].sum()
    mu = n * (n + 1) / 4
    sd = np.sqrt(n * (n + 1) * (2 * n + 1) / 24)
    if sd == 0:
        return 1.0
    return float(2 * _norm_sf(abs(w - mu) / sd))


async def feed(window: Window, products: list[str], sink: ParquetSink | None):
    """Consume the exchange feed forever, reconnecting on drop."""
    backoff = 1
    while True:
        try:
            async with websockets.connect(WS_URL, max_queue=None,
                                          compression=None) as ws:
                await ws.send(json.dumps({"type": "subscribe",
                                          "product_ids": products,
                                          "channels": ["matches", "heartbeat"]}))
                backoff = 1
                print(f"connected: {len(products)} products", flush=True)
                async for raw in ws:
                    recv_ns = time.time_ns()
                    data = raw if isinstance(raw, bytes) else raw.encode()
                    t0 = time.perf_counter_ns()
                    msg = json.loads(data)
                    t1 = time.perf_counter_ns()
                    orjson.loads(data)
                    t2 = time.perf_counter_ns()
                    if msg.get("type") != "match" or not msg.get("time"):
                        continue
                    ex_ns = int(
                        datetime.strptime(msg["time"], "%Y-%m-%dT%H:%M:%S.%f%z")
                        .timestamp() * 1e9
                    )
                    row = {
                        "recv_ns": recv_ns,
                        "recv_mono_ns": time.monotonic_ns(),
                        "exchange_time": msg["time"],
                        "msg_type": "match",
                        "product_id": msg.get("product_id"),
                        "trade_id": msg.get("trade_id"),
                        "price": float(msg.get("price") or 0),
                        "size": float(msg.get("size") or 0),
                        "side": msg.get("side"),
                        "sequence": msg.get("sequence"),
                        "wire_bytes": len(data),
                        "parse_json_ns": t1 - t0,
                        "parse_orjson_ns": t2 - t1,
                        "latency_ms": (recv_ns - ex_ns) / 1e6,
                    }
                    window.add(row)
                    if sink is not None:
                        sink.add({k: v for k, v in row.items() if k != "latency_ms"})
        except asyncio.CancelledError:
            raise
        except Exception as e:                       # noqa: BLE001 - keep serving
            print(f"feed error ({e}); reconnecting in {backoff}s", flush=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *a, window: Window, page: bytes, **kw):
        self.window, self.page = window, page
        super().__init__(*a, **kw)

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/stats":
            try:
                body = orjson.dumps(compute(self.window.snapshot()))
            except Exception as e:                   # noqa: BLE001
                body = orjson.dumps({"error": str(e)})
                return self._send(500, body, "application/json")
            return self._send(200, body, "application/json")
        if self.path.split("?")[0] in ("/", "/index.html"):
            return self._send(200, self.page, "text/html; charset=utf-8")
        self._send(404, b"not found", "text/plain")

    def log_message(self, *a):
        pass                                          # keep the console for the feed


def build_page() -> bytes:
    html = render_page("null", LIVE_BOOT)
    return html.replace("</style>", LIVE_CSS + "</style>", 1).encode()


async def main_async(args):
    window = Window(args.window * 60)
    sink = None
    if args.save:
        args.out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        sink = ParquetSink(args.out / f"ticks_{stamp}.parquet")
        print(f"also saving to {args.out}/ticks_{stamp}.parquet")

    srv = ThreadingHTTPServer(
        (args.host, args.port),
        partial(Handler, window=window, page=build_page()),
    )
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"live dashboard: http://{args.host}:{args.port}  "
          f"(rolling {args.window:g}-minute window)", flush=True)

    try:
        await feed(window, args.products, sink)
    finally:
        srv.shutdown()
        if sink is not None:
            sink.close()
            print("\nparquet closed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8420)
    ap.add_argument("--window", type=float, default=5.0,
                    help="rolling window in minutes (default 5)")
    ap.add_argument("--save", action="store_true",
                    help="also write the session to Parquet")
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--products", nargs="+",
                    default=["BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD",
                             "XRP-USD", "LTC-USD", "ADA-USD", "AVAX-USD",
                             "LINK-USD", "DOT-USD"])
    args = ap.parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
