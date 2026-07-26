# Exchange Latency Forensics Lab

### 📊 [**Read the report →**](https://shrutidc.github.io/nanochron/)

Capture a live market data feed, measure its end-to-end latency distribution
down to the tail, and run controlled experiments that prove which changes
actually made it faster.

Feed: **Coinbase Exchange** public WebSocket (`wss://ws-feed.exchange.coinbase.com`),
`matches` channel. No API key, no account. Every trade message carries an
exchange-side timestamp at microsecond resolution, which is what makes a real
end-to-end measurement possible.

**Headline result** — 7,171 trades across 10 products over 20 minutes:

| p50 | p99 | p99.9 | worst |
|---|---|---|---|
| 124.5 ms | 329.1 ms | 2,808.5 ms | 4,001.1 ms |

The median is unremarkable; the tail is **22× worse**. The feed runs steadily,
then stalls for seconds — and the interesting part is that neither message
volume nor batch size explains it. Details in [The finding](#the-finding).

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Run it

```bash
.venv/bin/python recorder.py --minutes 20 --out data
```

```bash
.venv/bin/python analyze.py --data data --out results.json
```

```bash
.venv/bin/python dashboard.py --results results.json --out docs/index.html
```

Open `docs/index.html` in any browser. It is fully self-contained — and it is
what GitHub Pages publishes (see below).

## The published report

**<https://shrutidc.github.io/nanochron/>**

`docs/index.html` is committed and served by GitHub Pages from `main` `/docs`.
A permanent URL over HTTPS, with no process running that can die — and it
works on a phone, tablet or laptop.

To refresh it with a new capture:

```bash
.venv/bin/python recorder.py --minutes 20 --out data && \
.venv/bin/python analyze.py --data data --out results.json && \
.venv/bin/python dashboard.py --results results.json --out docs/index.html && \
git add -A && git commit -m "Refresh capture" && git push
```

Note that a new capture is **not comparable to the published one** on absolute
latency: every session carries its own clock and path offset, which is the
pooling trap described below. The report always describes a single session.

## Live mode

The pipeline above produces a snapshot. To watch the distribution move
instead:

```bash
.venv/bin/python live.py
```

Then open <http://127.0.0.1:8420>. The recorder and the analysis run in one
process; the page polls once a second and re-renders. `--window` sets the
rolling window in minutes (default 5), `--save` also writes the session to
Parquet, `--port` moves the server.

Notes on the design:

* Statistics cover a **rolling window**, not a whole capture, so the page
  describes the feed as it is now rather than accumulating history forever.
* The charts are the *same code* as the static dashboard — `dashboard.py`
  owns the rendering and `live.py` supplies data in the identical shape, so
  the two views cannot drift apart. Verified: on the same input, `live.py`'s
  `compute()` reproduces `analyze.py` exactly across every percentile, burst
  bucket and histogram bin.
* A live session is one session by construction, so the cross-session
  clock-offset trap that `--session all` guards against cannot arise here.
* The page does not repaint while you are hovering a chart, and it preserves
  expanded tables and scroll position across refreshes.
* If the recorder dies, the page says **"Disconnected from the recorder"**
  and shows how stale the numbers are, rather than displaying old values as
  though they were current. It reconnects on its own when the server returns.
* Statistics use **scipy** — the same `spearmanr` and `wilcoxon` calls
  `analyze.py` makes, so the live and batch numbers are identical, not
  merely close. Results are cached per second and shared across viewers, so
  scipy runs about once a second no matter how many people are watching.
* Every chart sizes itself to its container, so text renders at its true size
  on a phone rather than being scaled down to ~3px. Verified at 320, 375, 768
  and desktop widths: no clipped labels, no sideways page scroll, wide tables
  scroll inside their own box.

## Demoing live mode to someone else

The published report above is the artifact to link to. This is for when you
want to show the *live* view to someone — during a call, say — without
standing up a server.

```bash
./share.sh
```

Prints a public HTTPS URL that works from any phone, tablet or laptop:

```
https://<random-words>.trycloudflare.com
```

It runs `live.py` on localhost and puts a Cloudflare Quick Tunnel in front of
it. No signup, no card, no hosting bill. Requires `brew install cloudflared`.

What it exposes: one read-only page and one read-only JSON endpoint, both
serving latency statistics about a public market-data feed. No upload path,
no filesystem access, and the tunnel reaches `127.0.0.1:PORT` only — not the
rest of the machine.

Two real limits: the URL is **random and changes on every restart**, and the
site is only up while your machine is awake and the script is running. Which
is fine for what this is — a demo you run on purpose, not a service. For a
permanent address, the published report above already is one.

Set `VANTAGE` to something truthful, because the page will repeat it:

```bash
VANTAGE="a Mac in Lubbock, TX" ./share.sh
```

## Hosting the live server (optional)

Not recommended, and worth saying why. A permanently hosted live instance
would foreground the project's *weakest* statistics: a 5-minute rolling window
holds a couple of thousand messages, which is not enough to support a p99.9 —
the dashboard correctly refuses to print one at that sample size. The
defensible claims live in the 20-minute batch capture, which is what the
published report shows. A live ticker also has to stay up to be worth
anything, and a dead demo link is worse than no link.

If you want it anyway, the constraint is that the live server is a **stateful
long-running process** — it holds a WebSocket to the exchange and keeps a
rolling window in memory. That rules out static hosts (GitHub Pages, Netlify)
and any platform that sleeps idle instances, which would sever the feed.

On *free* tiers specifically: most either sleep idle instances (so a visitor
meets a cold start and an empty rolling window) or are time-limited. The one
durably free option that fits this workload is a free-tier VM — Oracle Cloud's
Always Free ARM instance is the usual choice — which is just a small VPS, so
follow the container instructions below on it. Free-tier terms change often;
check current terms before relying on any of them.

A `Dockerfile` is included and runs unchanged on Fly.io, Render, Railway,
Cloud Run, or a plain VPS:

```bash
docker build -t latency-lab . && docker run -p 8080:8080 -e VANTAGE="my laptop" latency-lab
```

Points that matter when it is public:

* **Set `VANTAGE`.** Hosted, the page measures *the server's* link to the
  exchange, not the viewer's. Someone in Mumbai looking at a Frankfurt
  instance sees Frankfurt's latency. The page names its vantage point in the
  status bar and the footer so this cannot be misread — set it to the region
  you deploy to.
* `HOST` and `PORT` are read from the environment; the container defaults to
  `0.0.0.0:8080`. Locally the default stays loopback so running it does not
  quietly expose your machine.
* `/healthz` is a cheap liveness probe that never touches the stats cache, so
  health checks do not become a load source.
* The server is Python's `ThreadingHTTPServer` — one thread per connection.
  Fine for tens of concurrent viewers; put it behind a CDN or move to an ASGI
  server if you expect hundreds.
* Only `requirements-live.txt` is installed in the image. `duckdb` and
  `pandas` are batch-analysis dependencies that `live.py` never imports, and
  omitting them saves roughly 200MB.

## Optional: packet-level capture

Application timestamps tell you when *Python* saw the message. Packet
timestamps tell you when the *kernel* did. The gap between them is your own
processing cost, separated from network latency. This needs root:

```bash
sudo ./pcap_capture.sh 300 data/capture.pcap
```

Run it concurrently with the recorder, then:

```bash
.venv/bin/python pcap_join.py --pcap data/capture.pcap --data data
```

The feed is TLS, so packets cannot be matched to messages by content — this is
a nearest-preceding-packet estimate, and `pcap_join.py` says so in its output.
Treat it as a bound on kernel→userspace cost, not per-message truth.

## What each piece does

| File | Role |
|---|---|
| `recorder.py` | Subscribes, stamps every message with `time.time_ns()`, batches to Parquet (zstd). Parses each message with both `json` and `orjson` and records both durations — that's the paired A/B sample. |
| `analyze.py` | DuckDB over the Parquet files. Latency distribution, per-product breakdown, per-second volume series, burst/queueing analysis, and the statistical test. |
| `dashboard.py` | Renders `results.json` into a single self-contained HTML page written for a non-engineer. Owns the chart code that live mode reuses. |
| `stats_util.py` | Quantile summaries that carry their own sample support, shared by batch and live so the two can never disagree about what a percentile means. |
| `live.py` | Recorder + analysis + HTTP server in one process, serving a self-refreshing dashboard over a rolling window. |
| `share.sh` | Publishes the live dashboard at a public HTTPS URL via a Cloudflare Quick Tunnel — free, no account. |
| `pcap_capture.sh` / `pcap_join.py` | Optional kernel-timestamp capture and the network-vs-processing split. |

## Five things this project gets right that are easy to get wrong

These are not hypothetical — each one produced a wrong number during
development, and the code now guards against it.

**1. `last_match` is not a latency sample.**
On subscribe, Coinbase sends one `last_match` per product: the most recent
trade from *before* you connected. Its exchange timestamp can be minutes old.
Including those four messages produced a max latency of 24.8 seconds that was
pure artifact. `recorder.py` now records only `type == "match"`, and
`analyze.py` filters defensively as well.

**2. Capture sessions must not be pooled.**
Each recording session carries its own constant offset — the local clock is
NTP-disciplined and can step between runs, and the network path can change.
Two sessions recorded 14 minutes apart had baseline (floor) latencies of 18 ms
and 109 ms. Pooling them produced a bimodal histogram whose "spike" was just
the second session, and a p99 of 1,285 ms that described nothing physical.
`analyze.py` defaults to `--session latest`; `--session all` still works but
compares the per-session floors and refuses to stay quiet when they diverge:

```bash
.venv/bin/python analyze.py --session all   # warns if baselines differ
```

**3. The clock offset is disclosed, not hidden.**
Latency here is local wall clock minus exchange timestamp. Neither side is
PTP-synced, so an unknown constant offset of a few ms shifts the whole
distribution. That offset does **not** affect the distribution's shape, the
tail-vs-median spread, the volume correlation, or the A/B experiment — all of
which are relative measures. The dashboard states this in its footer rather
than implying a precision the setup cannot deliver.

**4. A percentile is only as good as the sample behind it.**
p99.9 over a 1,700-message window is interpolated from about *two* messages —
it is not an estimate of the tail, it is the second-largest value wearing a
statistical label. Displayed as a headline KPI it claims a precision the
sample cannot support, which is the same failure as quoting p = 7e-10 on an
effect size of 0.09.

So every percentile now ships with its **support** — how many observations sit
at or above it (`stats_util.py`) — and the dashboard grades what it will say:

| support | behaviour |
|---|---|
| ≥ 10 | reported normally |
| 3–9 | reported, marked *"based on just N messages — treat as provisional"* |
| < 3 | **refused** — shows `—` and *"too few to estimate"* |

The rule applies everywhere at once, so the page cannot contradict itself: if
p99 is unsupported, the KPI shows a dash, the histogram drops its p99 marker,
and the headline verdict falls back to the deepest percentile that *is*
supported (or says outright that there is not enough data yet). The 20-minute
batch capture reports p99.9 = 2,808 ms with a support of 8, so it is shown and
flagged provisional rather than quietly asserted.

**5. Position within a burst is a confounded variable — and it lies convincingly.**
Messages arrive in clumps. Correlating a message's *position* in its clump
against its latency gives ρ = 0.41 at p = 2e-45, which looks like a decisive
finding: "the tail is queueing, later messages drain slower." It is an
artifact. Position > 10 can only occur inside a clump of > 10, and it is the
big clumps that are delayed — so the correlation is measuring clump **size**
while appearing to describe **position**. Controlling for size kills it: within
clumps of ≥ 6, positions 1, 2–3, 4–5 and 6–10 all sit flat at ~38 ms. Inside a
single large clump, latency is nearly constant across every position (~313 ms)
and if anything *decreases* as the exchange timestamp advances.

`analyze.py` therefore works at the burst level — one row per clump, because
messages inside a clump are not independent observations and a per-message
p-value would be pseudo-replicated — and reports the within-clump latency
spread as the evidence for what is really happening.

## The finding

From a 20-minute capture of 7,171 trades across 10 products:

| | |
|---|---|
| median (p50) | 124.5 ms |
| p99 | 329.1 ms |
| p99.9 | 2,808.5 ms |
| worst | 4,001.1 ms |

The median is unremarkable and the p99 is only ~2.6× it — but p99.9 is **22×**
the median. The distribution is not heavy-tailed so much as *bimodal*: the feed
runs steadily, then occasionally stalls for seconds.

**The stalls are not explained by volume, and not by batch size either.** Per
second, message volume barely tracks tail latency (ρ = 0.13 — no strong
dependence). Median latency by clump size is nearly flat: 121.1 ms (single
message), 123.6, 124.5, 128.2, and 170.8 ms for clumps of 11+. The worst spike
in the capture — a 4-second stall — lands at a moment with no corresponding
volume spike at all, which the two-panel timeline shows plainly.

What *is* consistent: **within a clump, messages share almost exactly the same
delay** (median spread 0.17 ms). The feed stalls and then releases a whole
batch, rather than draining messages one at a time.

**On effect size versus significance.** Across 4,423 clumps, the correlation
between clump size and latency is ρ = 0.09 at p = 7.4e-10. That p-value is
overwhelming and the effect is meaningless — with thousands of samples, a
trivial trend clears any significance threshold. `analyze.py` grades the two
separately (`effect: negligible`, `significant: true`) and the dashboard says
both halves out loud. An earlier, shorter capture put the same correlation at
ρ = 0.23; it shrank as the sample grew, which is what a spurious effect does.

The actionable conclusion: the tail is upstream of this process. A faster parser
does not touch it — which the experiment then confirms.

## The experiment

One variable: the JSON parse path. Every message is parsed by both `json` and
`orjson` back to back, so both treatments see byte-identical input at the same
instant — a **paired** design, tested with a Wilcoxon signed-rank test
(Mann-Whitney U is also reported).

`orjson` parses the median message in 6.9 µs against the standard library's
20.5 µs — **2.97× faster**, at p < 1e-308. It is a real effect and not noise.

It is also nearly irrelevant to end-to-end latency. The saving is 13 µs per
message; at the observed peak of 105 messages/sec that is 1.4 ms of CPU per
second, against a p99.9 of 2,808 ms. Adopt it because it is free, not because
it fixes the tail — it does not.

The dashboard states both halves, because "statistically significant" and
"worth doing" are different questions, and a latency report that conflates them
is not worth reading.
