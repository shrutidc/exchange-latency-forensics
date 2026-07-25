"""Latency analysis for the Exchange Latency Forensics Lab.

Reads the Parquet capture(s) through DuckDB, joins exchange-side timestamps
to local receive timestamps, and produces:

  * the end-to-end latency distribution (p50 / p90 / p99 / p99.9 / max)
  * per-second latency vs. message-volume series and their correlation
  * the paired parse-path experiment (stdlib json vs orjson) with a
    Wilcoxon signed-rank test (paired) and a Mann-Whitney U test
  * a JSON results file consumed by the dashboard builder

A note on clocks: end-to-end latency here is (local wall clock - exchange
timestamp). Both sides are NTP-disciplined but not PTP-synced, so the
distribution carries a constant unknown clock offset of up to a few ms.
That offset shifts the whole distribution; it does NOT affect the shape,
the tail-vs-median spread, spike correlation, or the A/B experiment, which
are all relative measures. We report the raw values and say so.

Usage:
    python analyze.py --data data --out results.json
"""

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy import stats


def complete_captures(data_dir: Path) -> list[str]:
    """Parquet files that are finished and readable.

    A ParquetWriter only writes the footer on close, so a capture that is
    still running has an unreadable file. Skip those instead of failing --
    analyzing earlier captures while a new one records is a normal thing to
    want to do.
    """
    good, skipped = [], []
    for p in sorted(data_dir.glob("ticks_*.parquet")):
        try:
            if pq.ParquetFile(p).metadata.num_rows > 0:
                good.append(str(p))
            else:
                skipped.append(p.name)
        except Exception:
            skipped.append(p.name)
    if skipped:
        print(f"skipping {len(skipped)} incomplete capture(s): {', '.join(skipped)}")
    if not good:
        raise SystemExit(f"no complete captures in {data_dir}")
    return good


def session_baselines(files: list[str]) -> list[dict]:
    """Per-capture baseline latency, used to decide whether pooling is valid.

    Each recording session carries its own constant offset: the local clock is
    NTP-disciplined (and can step between sessions) and the network path can
    change. That offset moves the whole distribution, so pooling two sessions
    with different baselines produces a fake bimodal histogram in which the
    second session shows up as a "spike" that has no physical meaning.

    The floor (the minimum observed latency) is the cleanest baseline estimate:
    load and queueing can only push latency up, so the minimum is dominated by
    the constant offset rather than by contention.
    """
    out = []
    for f in files:
        d = duckdb.sql(
            f"""SELECT (recv_ns - epoch_ns(CAST(exchange_time AS TIMESTAMP)))/1e6 AS l
                FROM read_parquet('{f}')
                WHERE msg_type = 'match' AND exchange_time IS NOT NULL"""
        ).df()["l"]
        if len(d):
            out.append({"file": Path(f).name, "n": int(len(d)),
                        "floor_ms": float(d.min()), "p50_ms": float(d.median())})
    return out


def select_files(data_dir: Path, session: str) -> tuple[list[str], list[dict], str | None]:
    """Resolve --session into a file list, and warn if pooling is unsafe."""
    every = complete_captures(data_dir)
    bases = session_baselines(every)
    if session == "latest":
        return [every[-1]], [b for b in bases if b["file"] == Path(every[-1]).name], None
    if session != "all":
        picked = [f for f in every if Path(f).name == session or f == session]
        if not picked:
            raise SystemExit(f"no capture matching {session!r} in {data_dir}")
        return picked, [b for b in bases if Path(picked[0]).name == b["file"]], None

    warn = None
    if len(bases) > 1:
        spread = max(b["floor_ms"] for b in bases) - min(b["floor_ms"] for b in bases)
        if spread > 20:
            warn = (
                f"Pooling {len(bases)} capture sessions whose baseline latency differs "
                f"by {spread:.0f} ms. Each session has its own constant clock/path "
                f"offset, so the pooled distribution is bimodal by construction and "
                f"its percentiles are not meaningful. Use --session latest instead."
            )
            print(f"WARNING: {warn}")
    return every, bases, warn


def quantiles(arr: np.ndarray, unit: str) -> dict:
    """Percentile summary. Keys are unit-neutral and the unit is carried
    alongside -- latency is in ms but the parse experiment is in µs, and a
    `p50_ms` key holding microseconds is exactly how a wrong number ships."""
    return {
        "unit": unit,
        "count": int(arr.size),
        "min": float(np.min(arr)),
        "p50": float(np.percentile(arr, 50)),
        "p90": float(np.percentile(arr, 90)),
        "p99": float(np.percentile(arr, 99)),
        "p999": float(np.percentile(arr, 99.9)),
        "max": float(np.max(arr)),
        "mean": float(np.mean(arr)),
    }


def analyze(data_dir: Path, out_path: Path, session: str = "latest"):
    con = duckdb.connect()
    files, bases, pool_warning = select_files(data_dir, session)
    glob = "', '".join(files)  # spliced into read_parquet(['a', 'b', ...])

    # Join exchange timestamp to local receive timestamp. exchange_time is
    # ISO8601 with microseconds; recv_ns is local wall clock in ns.
    base = con.sql(
        f"""
        SELECT
            recv_ns,
            recv_mono_ns,
            CAST(exchange_time AS TIMESTAMP) AS ex_ts,
            epoch_ns(CAST(exchange_time AS TIMESTAMP)) AS ex_ns,
            (recv_ns - epoch_ns(CAST(exchange_time AS TIMESTAMP))) / 1e6 AS latency_ms,
            product_id, price, size, side, sequence, wire_bytes,
            parse_json_ns, parse_orjson_ns
        FROM read_parquet(['{glob}'])
        WHERE exchange_time IS NOT NULL
          -- `last_match` is the subscribe-time snapshot, not a live tick: its
          -- exchange timestamp predates our connection, so it would contribute
          -- a meaningless multi-second outlier straight into the tail.
          AND msg_type = 'match'
        ORDER BY recv_ns
        """
    )
    con.register("base_v", base)
    df = base.df()
    if df.empty:
        raise SystemExit(f"no rows found in {glob}")

    lat = df["latency_ms"].to_numpy()

    results = {
        "capture": {
            "files": [Path(f).name for f in files],
            "products": sorted(df["product_id"].dropna().unique().tolist()),
            "start_utc": str(df["ex_ts"].min()),
            "end_utc": str(df["ex_ts"].max()),
            "duration_s": float(
                (df["recv_ns"].max() - df["recv_ns"].min()) / 1e9
            ),
            "messages": int(len(df)),
        },
        "latency": quantiles(lat, "ms"),
        "latency_by_product": {
            p: quantiles(g["latency_ms"].to_numpy(), "ms")
            for p, g in df.groupby("product_id")
        },
    }

    # Histogram for the dashboard. Log-spaced bins show a heavy tail far better
    # than linear ones, but they require strictly positive values -- and latency
    # can come out negative when the local clock sits behind the exchange clock
    # (NTP offset, see module docstring). Fall back to linear bins in that case
    # rather than silently dropping the rows outside the bin range.
    scale = "log" if lat.min() > 0 else "linear"
    if scale == "log":
        bins = np.logspace(np.log10(lat.min()), np.log10(lat.max()), 60)
    else:
        bins = np.linspace(lat.min(), lat.max(), 60)
    # logspace round-trips through log10, so the outer edges can land a hair
    # inside the true range and silently drop the min/max rows. Pin them.
    bins[0], bins[-1] = lat.min(), np.nextafter(lat.max(), np.inf)
    counts, edges = np.histogram(lat, bins=bins)
    assert counts.sum() == lat.size, "histogram dropped rows"
    results["histogram"] = {
        "scale": scale,
        "edges_ms": edges.tolist(),
        "counts": counts.tolist(),
    }
    results["clock"] = {
        "negative_latency_rows": int((lat < 0).sum()),
        "sessions": bases,
        "pool_warning": pool_warning,
        "note": (
            "Latency is local wall clock minus exchange timestamp. Neither side "
            "is PTP-synced, so a constant NTP offset of up to a few ms shifts "
            "the whole distribution. Shape, tail-vs-median spread, volume "
            "correlation and the A/B experiment are all relative measures and "
            "are unaffected by that offset."
        ),
    }

    # --- Spike vs volume: per-second aggregation -------------------------
    per_sec = con.sql(
        """
        SELECT
            recv_ns // 1000000000 AS sec,
            COUNT(*) AS msgs,
            SUM(price * size) AS notional_usd,
            median(latency_ms) AS p50_ms,
            quantile_cont(latency_ms, 0.99) AS p99_ms,
            MAX(latency_ms) AS max_ms
        FROM base_v
        GROUP BY sec ORDER BY sec
        """
    ).df()

    # Correlate message volume against tail latency across seconds.
    if len(per_sec) > 10:
        rho, pval = stats.spearmanr(per_sec["msgs"], per_sec["p99_ms"])
        results["volume_correlation"] = {
            "spearman_rho": float(rho),
            "p_value": float(pval),
            "n_seconds": int(len(per_sec)),
            "interpretation": (
                "latency rises with volume"
                if rho > 0.2 and pval < 0.05
                else "latency falls with volume"
                if rho < -0.2 and pval < 0.05
                else "no strong volume dependence in this window"
            ),
        }
    sec0 = int(per_sec["sec"].iloc[0])
    results["timeseries"] = {
        "t_s": (per_sec["sec"] - sec0).astype(int).tolist(),
        "msgs": per_sec["msgs"].astype(int).tolist(),
        "p50_ms": per_sec["p50_ms"].round(3).tolist(),
        "p99_ms": per_sec["p99_ms"].round(3).tolist(),
        "max_ms": per_sec["max_ms"].round(3).tolist(),
    }

    # --- Burst analysis ---------------------------------------------------
    # Messages do not trickle in evenly: they land back-to-back in bursts.
    #
    # A warning about how NOT to read this. Correlating a message's POSITION in
    # its burst against its latency gives a strong positive result (rho ~ 0.4,
    # p ~ 1e-45) -- and that result is an artifact. Position > 10 can only occur
    # inside a burst of > 10, and it is the big bursts that are delayed; the
    # correlation is picking up burst SIZE while appearing to describe position.
    # Within a fixed burst size the position effect vanishes: every message in a
    # burst shares nearly the same latency, because the whole burst is delayed
    # as a unit.
    #
    # So the honest analysis works at the BURST level (one row per burst, not
    # per message -- per-message rows in the same burst are not independent
    # observations) and asks whether bigger bursts are later bursts. The
    # within-burst latency spread is reported as the evidence for "delivered
    # as a unit".
    burst = con.sql(
        """
        WITH gaps AS (
            SELECT *, recv_ns - lag(recv_ns) OVER (ORDER BY recv_ns) AS gap_ns
            FROM base_v
        ), marked AS (
            SELECT *, SUM(CASE WHEN gap_ns IS NULL OR gap_ns > 1000000 THEN 1 ELSE 0 END)
                        OVER (ORDER BY recv_ns ROWS UNBOUNDED PRECEDING) AS burst_id
            FROM gaps
        )
        SELECT burst_id,
               ROW_NUMBER() OVER (PARTITION BY burst_id ORDER BY recv_ns) AS pos,
               latency_ms
        FROM marked
        """
    ).df()
    # One row per burst: size, its latency, and how much latency varies inside it.
    per_burst = burst.groupby("burst_id")["latency_ms"].agg(
        size="size", median="median", lo="min", hi="max"
    )
    per_burst["spread_ms"] = per_burst["hi"] - per_burst["lo"]
    sizes = per_burst["size"]

    edges = [0, 1, 2, 5, 10, int(max(sizes.max(), 11))]
    labels = ["1 message", "2", "3-5", "6-10", "11 or more"]
    per_burst["bucket"] = pd.cut(
        per_burst["size"], bins=edges, labels=labels, include_lowest=True
    )
    # Emit an ordered LIST, not a dict. The dashboard lays these out top to
    # bottom and assigns the ordinal colour ramp by index, so order carries
    # meaning -- and a JSON object cannot carry it safely: JavaScript
    # enumerates integer-like keys ("2") before string keys ("1 message"),
    # regardless of insertion order, which silently scrambles the rows.
    by_size = []
    grouped = dict(list(per_burst.groupby("bucket", observed=True)))
    for lab in labels:
        g = grouped.get(lab)
        if g is None or g.empty:
            continue
        by_size.append({
            "label": str(lab),
            "bursts": int(len(g)),
            "messages": int(g["size"].sum()),
            "p50_ms": float(g["median"].median()),
            "p90_ms": float(np.percentile(g["median"], 90)),
            "max_ms": float(g["median"].max()),
        })

    # Correlate at the burst level -- messages inside one burst are not
    # independent samples, so a per-message correlation would be pseudo-
    # replicated and its p-value meaningless.
    rho_b, p_b = stats.spearmanr(per_burst["size"], per_burst["median"])
    multi = per_burst[per_burst["size"] > 1]["spread_ms"]

    results["bursts"] = {
        "n_bursts": int(len(per_burst)),
        "max_burst_size": int(sizes.max()),
        "pct_msgs_in_bursts": float(100 * (burst["pos"] > 1).sum() / len(burst)),
        "latency_by_burst_size": by_size,
        "size_correlation": {
            "spearman_rho": float(rho_b),
            "p_value": float(p_b),
            "n_bursts": int(len(per_burst)),
            # With thousands of bursts, a trivial correlation still clears
            # p < 0.05. Grade the effect size separately so the dashboard can
            # distinguish "detectable" from "worth acting on".
            "effect": (
                "negligible" if abs(rho_b) < 0.2
                else "weak" if abs(rho_b) < 0.4
                else "moderate" if abs(rho_b) < 0.6
                else "strong"
            ),
            "significant": bool(p_b < 0.05),
        },
        # If a burst is delivered as one unit, every message in it shares
        # essentially the same latency and this spread is near zero.
        "within_burst_spread_ms": (
            {"p50": float(np.percentile(multi, 50)),
             "p90": float(np.percentile(multi, 90)),
             "max": float(multi.max()),
             "n_multi_message_bursts": int(multi.size)}
            if multi.size else None
        ),
    }
    delivered_as_unit = (
        multi.size > 0 and float(np.percentile(multi, 90)) < 0.1 * results["latency"]["p50"]
    )
    results["bursts"]["delivered_as_unit"] = bool(delivered_as_unit)
    results["bursts"]["verdict"] = (
        "whole bursts are delayed together: messages inside a burst share nearly "
        "the same latency, and larger bursts are the delayed ones"
        if delivered_as_unit and rho_b > 0.2 and p_b < 0.05
        else "larger bursts are the delayed ones"
        if rho_b > 0.2 and p_b < 0.05
        else "burst size has a statistically detectable but negligible effect on "
             "latency; whole bursts still arrive delayed together"
        if p_b < 0.05 and delivered_as_unit
        else "burst size has a statistically detectable but negligible effect on latency"
        if p_b < 0.05
        else "burst size does not predict latency in this window"
    )

    # --- Parse-path experiment: stdlib json (A) vs orjson (B) ------------
    # Paired by construction: each message was parsed with both.
    a = df["parse_json_ns"].to_numpy() / 1e3  # µs
    b = df["parse_orjson_ns"].to_numpy() / 1e3
    w_stat, w_p = stats.wilcoxon(a, b)
    u_stat, u_p = stats.mannwhitneyu(a, b, alternative="two-sided")
    results["experiment"] = {
        "name": "parse path: stdlib json (A) vs orjson (B), paired per message",
        "n_pairs": int(a.size),
        "a_json": quantiles(a, "us"),
        "b_orjson": quantiles(b, "us"),
        "median_speedup": float(np.median(a) / np.median(b)),
        "median_delta_us": float(np.median(a - b)),
        "wilcoxon_p": float(w_p),
        "mannwhitney_p": float(u_p),
        "significant": bool(w_p < 0.001),
    }

    out_path.write_text(json.dumps(results, indent=2))
    lt = results["latency"]
    print(f"messages: {results['capture']['messages']}  "
          f"duration: {results['capture']['duration_s']:.0f}s")
    print(f"latency ms  p50={lt['p50']:.2f}  p99={lt['p99']:.2f}  "
          f"p99.9={lt['p999']:.2f}  max={lt['max']:.2f}")
    ex = results["experiment"]
    print(f"experiment: median A {np.median(a):.2f}us vs B {np.median(b):.2f}us  "
          f"speedup x{ex['median_speedup']:.2f}  wilcoxon p={ex['wilcoxon_p']:.2e}")
    print(f"wrote {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--out", type=Path, default=Path("results.json"))
    ap.add_argument(
        "--session", default="latest",
        help="'latest' (default), 'all' to pool every capture, or a filename. "
             "Pooling is only valid when sessions share a baseline offset.",
    )
    args = ap.parse_args()
    analyze(args.data, args.out, args.session)


if __name__ == "__main__":
    main()
