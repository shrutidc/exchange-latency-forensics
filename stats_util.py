"""Quantile summaries that carry their own sample support.

Shared by analyze.py (batch) and live.py (rolling window) so the two can
never disagree about what a percentile means. numpy only -- live.py runs in a
container that deliberately omits duckdb and pandas.

Why support is tracked at all
-----------------------------
A quantile estimate is only as good as the number of observations beyond it.
p99.9 over a 1,700-message window is interpolated from roughly TWO messages:
it is not an estimate of the tail, it is the second-largest value wearing a
statistical label. Reported as a headline number it claims a precision the
sample cannot support, which is the same failure this project catches
elsewhere (a p-value of 7e-10 on an effect size of 0.09).

So every percentile ships with `support` -- how many observations sit at or
above it -- and callers decide how much authority to give it:

    support >= LOW_SUPPORT   report normally
    MIN_SUPPORT..LOW_SUPPORT report, but mark it as weakly supported
    < MIN_SUPPORT            refuse to show a number

The thresholds are the usual rule of thumb for latency work: you want ~10
observations past a quantile before quoting it, and below ~3 the estimate is
determined by individual messages.
"""

import numpy as np

LOW_SUPPORT = 10
MIN_SUPPORT = 3

# (key, percentile) pairs reported for every distribution.
LEVELS = (("p50", 50.0), ("p90", 90.0), ("p99", 99.0), ("p999", 99.9))


def quantiles(arr: np.ndarray, unit: str) -> dict:
    """Percentile summary plus per-percentile sample support.

    Keys are unit-neutral and the unit travels alongside -- latency is in ms
    but the parse experiment is in µs, and a `p50_ms` key holding
    microseconds is how a wrong number ships.
    """
    arr = np.asarray(arr, dtype=float)
    out = {
        "unit": unit,
        "count": int(arr.size),
        "min": float(arr.min()),
        "max": float(arr.max()),
        "mean": float(arr.mean()),
    }
    support = {}
    for key, q in LEVELS:
        v = float(np.percentile(arr, q))
        out[key] = v
        # Observations at or above the estimate. For p99.9 this is the tail
        # count that the estimate actually rests on.
        support[key] = int(np.count_nonzero(arr >= v))
    out["support"] = support
    return out


def supported(summary: dict, key: str) -> bool:
    """True when this percentile has enough sample to be worth showing."""
    return summary.get("support", {}).get(key, 0) >= MIN_SUPPORT


def weak(summary: dict, key: str) -> bool:
    """True when it should be shown but flagged as weakly supported."""
    n = summary.get("support", {}).get(key, 0)
    return MIN_SUPPORT <= n < LOW_SUPPORT
