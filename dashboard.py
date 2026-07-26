"""Render results.json as a self-contained HTML dashboard.

The audience is someone who does not read code: the page leads with a
plain-English verdict, then the numbers, then the charts that justify them.
Everything is inlined -- no CDN, no build step -- so the file can be emailed.

Usage:
    python dashboard.py --results results.json --out dashboard.html
"""

import argparse
import json
from pathlib import Path

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Exchange Latency Forensics</title>
<style>
  :root {
    color-scheme: light;
    --surface-1: #fcfcfb;
    --plane: #f9f9f7;
    --text-primary: #0b0b0b;
    --text-secondary: #52514e;
    --muted: #898781;
    --grid: #e1e0d9;
    --axis: #c3c2b7;
    --border: rgba(11,11,11,0.10);
    --series-1: #2a78d6;
    --series-2: #eb6834;
    --ord-1: #86b6ef;
    --ord-2: #5598e7;
    --ord-3: #2a78d6;
    --ord-4: #1c5cab;
    --ord-5: #104281;
    --good: #0ca30c;
    --critical: #d03b3b;
    --warning: #fab219;
  }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) {
      color-scheme: dark;
      --surface-1: #1a1a19;
      --plane: #0d0d0d;
      --text-primary: #ffffff;
      --text-secondary: #c3c2b7;
      --muted: #898781;
      --grid: #2c2c2a;
      --axis: #383835;
      --border: rgba(255,255,255,0.10);
      --series-1: #3987e5;
      --series-2: #d95926;
      --ord-1: #184f95;
      --ord-2: #256abf;
      --ord-3: #3987e5;
      --ord-4: #6da7ec;
      --ord-5: #9ec5f4;
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --surface-1: #1a1a19;
    --plane: #0d0d0d;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --grid: #2c2c2a;
    --axis: #383835;
    --border: rgba(255,255,255,0.10);
    --series-1: #3987e5;
    --series-2: #d95926;
    --ord-1: #184f95;
    --ord-2: #256abf;
    --ord-3: #3987e5;
    --ord-4: #6da7ec;
    --ord-5: #9ec5f4;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 32px 20px 72px;
    background: var(--plane); color: var(--text-primary);
    font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  .wrap { max-width: 1000px; margin: 0 auto; }
  h1 { font-size: 26px; margin: 0 0 6px; letter-spacing: -0.01em; }
  h2 { font-size: 17px; margin: 0 0 4px; letter-spacing: -0.005em; }
  .sub { color: var(--text-secondary); margin: 0 0 28px; font-size: 14px; }
  .card {
    background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 12px; padding: 20px 22px; margin-bottom: 18px;
  }
  .card > p.note { color: var(--text-secondary); font-size: 13.5px; margin: 4px 0 18px; }
  .verdict { display: flex; gap: 14px; align-items: flex-start; }
  .dot { width: 10px; height: 10px; border-radius: 50%; margin-top: 7px; flex: none; }
  .verdict h2 { margin-bottom: 2px; }
  .verdict p { margin: 0; color: var(--text-secondary); font-size: 14px; }
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
  .kpi { background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px; }
  .kpi .label { font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
  .kpi .value { font-size: 30px; font-weight: 600; margin: 6px 0 2px; letter-spacing: -0.02em; }
  .kpi .unit { font-size: 15px; font-weight: 400; color: var(--text-secondary); margin-left: 3px; }
  .kpi .hint { font-size: 12.5px; color: var(--text-secondary); }
  /* A percentile the sample cannot support: shown as a dash, not a number. */
  .kpi .value.dim { color: var(--muted); }
  .kpi .hint.warn { color: var(--warning); margin-top: 3px; }
  .chartbox { position: relative; }
  svg { display: block; width: 100%; height: auto; overflow: visible; }
  .legend { display: flex; gap: 18px; flex-wrap: wrap; margin: 0 0 12px; font-size: 13px; color: var(--text-secondary); }
  .legend span { display: inline-flex; align-items: center; gap: 7px; }
  .swatch { width: 11px; height: 11px; border-radius: 3px; flex: none; }
  .tip {
    position: absolute; pointer-events: none; opacity: 0; transition: opacity .1s;
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px;
    padding: 8px 10px; font-size: 12.5px; box-shadow: 0 4px 16px rgba(0,0,0,.14);
    white-space: nowrap; z-index: 5; color: var(--text-primary);
  }
  .tip b { font-weight: 600; }
  table { border-collapse: collapse; width: 100%; font-size: 13.5px; font-variant-numeric: tabular-nums; }
  th, td { text-align: right; padding: 7px 10px; border-bottom: 1px solid var(--grid); }
  th:first-child, td:first-child { text-align: left; font-variant-numeric: normal; }
  th { color: var(--muted); font-weight: 500; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; }
  details { margin-top: 16px; }
  summary { cursor: pointer; color: var(--text-secondary); font-size: 13px; }
  .foot { color: var(--muted); font-size: 12.5px; margin-top: 26px; line-height: 1.6; }
  /* Wide tables scroll inside their own box; the page never scrolls sideways. */
  .tablewrap { overflow-x: auto; -webkit-overflow-scrolling: touch; max-width: 100%; }
  @media (max-width: 620px) {
    body { padding: 18px 12px 48px; }
    h1 { font-size: 21px; }
    h2 { font-size: 15.5px; }
    .card { padding: 15px 14px; border-radius: 10px; }
    .kpi { padding: 13px 14px; }
    .kpi .value { font-size: 25px; }
    .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; }
    .sub { font-size: 13px; margin-bottom: 20px; }
    th, td { padding: 6px 8px; white-space: nowrap; }
  }
  code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Exchange Latency Forensics</h1>
  <p class="sub" id="subtitle"></p>
  <div id="app"></div>
  <p class="foot" id="foot"></p>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
// Reassignable: the static page sets this once from the embedded payload,
// the live page replaces it on every poll and calls build() again.
let R = null;
const $ = (t, a = {}, kids = []) => {
  const ns = ['svg','g','rect','path','line','text','circle','polyline'].includes(t);
  const el = ns ? document.createElementNS('http://www.w3.org/2000/svg', t)
                : document.createElement(t);
  for (const [k, v] of Object.entries(a)) {
    if (k === 'class') el.setAttribute('class', v);
    else if (k === 'text') el.textContent = v;
    else el.setAttribute(k, v);
  }
  for (const kid of [].concat(kids)) el.appendChild(kid);
  return el;
};
const fmt = (v, d = 2) => v.toLocaleString(undefined, {
  minimumFractionDigits: d, maximumFractionDigits: d });
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
// A p-value that underflows float64 prints as "0.0e+0", which claims a
// certainty no test can deliver. Report it as a bound instead.
const pfmt = v => v > 0 ? '= ' + v.toExponential(1) : '< 1e-308';

// Charts are drawn in a viewBox equal to the container's CSS width, so the
// SVG renders 1:1 and a declared 11px label is actually 11px -- on a phone a
// fixed 940-wide viewBox scales to ~0.3 and the same label lands at 3.4px,
// which is unreadable. `narrow` switches the cramped layouts (long category
// labels beside short bars) to a stacked arrangement.
// A percentile is only as good as the number of observations beyond it.
// p99.9 over a 1,700-message window rests on about two messages -- shown as
// a headline it claims precision the sample cannot support. Mirrors the
// thresholds in stats_util.py.
const LOW_SUPPORT = 10, MIN_SUPPORT = 3;
const supportOf = (s, k) => (s && s.support && s.support[k]) || 0;
const isSupported = (s, k) => supportOf(s, k) >= MIN_SUPPORT;
const isWeak = (s, k) => { const n = supportOf(s, k);
                           return n >= MIN_SUPPORT && n < LOW_SUPPORT; };

function geom(mount) {
  // Charts are drawn into their card BEFORE the card is attached, so the
  // mount measures 0 wide. Fall back to the page container minus the card's
  // horizontal padding -- measuring the detached node would silently give
  // every chart the 940px desktop layout on a phone.
  let w = (mount && mount.clientWidth) || 0;
  if (!w) {
    const app = document.getElementById('app');
    const pad = matchMedia('(max-width: 620px)').matches ? 30 : 46;
    w = Math.max(0, (app ? app.clientWidth : 0) - pad);
  }
  const W = Math.max(280, Math.round(w || 940));
  return { W, narrow: W < 560 };
}

/* ---------- tooltip helper shared by every chart ---------- */
function attachTip(box) {
  const tip = $('div', { class: 'tip' });
  box.appendChild(tip);
  return {
    show(html, x, y) {
      tip.innerHTML = html;
      tip.style.opacity = 1;
      const bw = box.clientWidth, tw = tip.offsetWidth;
      tip.style.left = Math.max(0, Math.min(x - tw / 2, bw - tw)) + 'px';
      tip.style.top = (y - tip.offsetHeight - 12) + 'px';
    },
    hide() { tip.style.opacity = 0; }
  };
}

/* ---------- 1. latency distribution ---------- */
function histogram(mount) {
  const h = R.histogram, g = geom(mount), W = g.W;
  const H = g.narrow ? 230 : 300;
  const P = { t: 12, r: 12, b: 42, l: g.narrow ? 36 : 56 };
  const iw = W - P.l - P.r, ih = H - P.t - P.b;
  const box = $('div', { class: 'chartbox' });
  const svg = $('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img',
    'aria-label': 'Distribution of end-to-end message latency' });
  const log = h.scale === 'log';
  const e = h.edges_ms, lo = e[0], hi = e[e.length - 1];
  const sx = v => {
    const f = log ? (Math.log10(v) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo))
                  : (v - lo) / (hi - lo);
    return P.l + f * iw;
  };
  const maxC = Math.max(...h.counts);
  const sy = c => P.t + ih - (c / maxC) * ih;

  for (let i = 0; i <= 4; i++) {                    // y gridlines
    const c = Math.round(maxC * i / 4), y = sy(c);
    svg.appendChild($('line', { x1: P.l, x2: P.l + iw, y1: y, y2: y,
      stroke: css('--grid'), 'stroke-width': 1 }));
    svg.appendChild($('text', { x: P.l - 10, y: y + 4, 'text-anchor': 'end',
      fill: css('--muted'), 'font-size': 11, text: c.toLocaleString() }));
  }
  const bars = $('g');
  h.counts.forEach((c, i) => {
    const x0 = sx(e[i]), x1 = sx(e[i + 1]);
    const w = Math.max(1, x1 - x0 - 2);             // 2px surface gap
    if (c === 0) return;
    const y = sy(c), hh = P.t + ih - y;
    const r = $('rect', { x: x0, y, width: w, height: Math.max(hh, 1),
      rx: Math.min(4, w / 2), fill: css('--series-1') });
    r.dataset.i = i;
    bars.appendChild(r);
  });
  svg.appendChild(bars);
  svg.appendChild($('line', { x1: P.l, x2: P.l + iw, y1: P.t + ih, y2: P.t + ih,
    stroke: css('--axis'), 'stroke-width': 1 }));

  // x ticks: decades in log mode, evenly spaced otherwise
  const ticks = [];
  if (log) {
    for (let d = Math.floor(Math.log10(lo)); d <= Math.ceil(Math.log10(hi)); d++) {
      const v = Math.pow(10, d);
      if (v >= lo && v <= hi) ticks.push(v);
    }
  } else {
    for (let i = 0; i <= 5; i++) ticks.push(lo + (hi - lo) * i / 5);
  }
  ticks.forEach(v => svg.appendChild($('text', { x: sx(v), y: P.t + ih + 20,
    'text-anchor': 'middle', fill: css('--muted'), 'font-size': 11,
    text: v >= 1 ? fmt(v, v >= 100 ? 0 : 1) : fmt(v, 2) })));
  svg.appendChild($('text', { x: P.l + iw / 2, y: H - 6, 'text-anchor': 'middle',
    fill: css('--text-secondary'), 'font-size': 12,
    text: (g.narrow ? 'latency (ms)' : 'end-to-end latency (ms)')
          + (log ? ' — log scale' : '') }));

  // p50 / p99 markers, direct-labelled
  // Only mark percentiles the sample supports -- drawing a p99 rule next to a
  // KPI that just refused to report p99 would contradict the page itself.
  [['p50', R.latency.p50, 'p50'], ['p99', R.latency.p99, 'p99']]
      .filter(([, , key]) => isSupported(R.latency, key))
      .forEach(([n, v], k) => {
    if (v < lo || v > hi) return;
    const x = sx(v);
    svg.appendChild($('line', { x1: x, x2: x, y1: P.t, y2: P.t + ih,
      stroke: css('--text-secondary'), 'stroke-width': 2, 'stroke-opacity': .55 }));
    // Flip the label to the left of its rule when the marker sits near the
    // right edge, so it never runs outside the plot.
    const nearRight = x > P.l + iw * 0.7;
    svg.appendChild($('text', {
      x: nearRight ? x - 6 : x + 6, y: P.t + 14 + k * 16,
      'text-anchor': nearRight ? 'end' : 'start',
      fill: css('--text-secondary'), 'font-size': 11.5, text: `${n} ${fmt(v)} ms` }));
  });

  box.appendChild(svg);
  const tip = attachTip(box);
  const total = R.latency.count;
  bars.addEventListener('mousemove', ev => {
    const r = ev.target.closest('rect'); if (!r) return;
    const i = +r.dataset.i, b = box.getBoundingClientRect();
    tip.show(`<b>${fmt(e[i])} – ${fmt(e[i + 1])} ms</b><br>${h.counts[i].toLocaleString()} messages`
      + ` &middot; ${fmt(100 * h.counts[i] / total, 1)}%`,
      ev.clientX - b.left, ev.clientY - b.top);
  });
  bars.addEventListener('mouseleave', tip.hide);
  mount.appendChild(box);
}

/* ---------- 2. latency & volume over time (stacked, shared x — never dual-axis) ---------- */
function timeline(mount) {
  const ts = R.timeseries, t = ts.t_s;
  const panels = [
    { key: 'p99_ms', label: 'tail latency, p99 per second (ms)', color: css('--series-1') },
    { key: 'msgs',   label: 'messages received per second',      color: css('--series-2') },
  ];
  const g = geom(mount), W = g.W, H = g.narrow ? 150 : 190;
  const P = { t: 14, r: 12, b: 30, l: g.narrow ? 40 : 56 };
  const iw = W - P.l - P.r, ih = H - P.t - P.b;
  const tmin = t[0], tmax = t[t.length - 1];
  const sx = v => P.l + (v - tmin) / (tmax - tmin || 1) * iw;

  panels.forEach((pn, pi) => {
    const vals = ts[pn.key];
    const vmax = Math.max(...vals) * 1.08 || 1;
    const sy = v => P.t + ih - (v / vmax) * ih;
    const box = $('div', { class: 'chartbox' });
    const svg = $('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': pn.label });

    for (let i = 0; i <= 3; i++) {
      const v = vmax * i / 3, y = sy(v);
      svg.appendChild($('line', { x1: P.l, x2: P.l + iw, y1: y, y2: y,
        stroke: css('--grid'), 'stroke-width': 1 }));
      svg.appendChild($('text', { x: P.l - 10, y: y + 4, 'text-anchor': 'end',
        fill: css('--muted'), 'font-size': 11,
        text: vmax > 20 ? Math.round(v).toLocaleString() : fmt(v, 1) }));
    }
    // Break the line wherever the capture has a gap (separate recording
    // sessions), so we never draw a segment across time we did not observe.
    const GAP_S = 5;
    let seg = [];
    const flush = () => {
      if (seg.length > 1) {
        svg.appendChild($('polyline', { points: seg.join(' '), fill: 'none',
          stroke: pn.color, 'stroke-width': 2,
          'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
      } else if (seg.length === 1) {
        const [cx, cy] = seg[0].split(',');
        svg.appendChild($('circle', { cx, cy, r: 2, fill: pn.color }));
      }
      seg = [];
    };
    vals.forEach((v, i) => {
      if (i > 0 && t[i] - t[i - 1] > GAP_S) flush();
      seg.push(`${sx(t[i])},${sy(v)}`);
    });
    flush();
    svg.appendChild($('line', { x1: P.l, x2: P.l + iw, y1: P.t + ih, y2: P.t + ih,
      stroke: css('--axis'), 'stroke-width': 1 }));
    svg.appendChild($('text', { x: P.l, y: P.t - 2, fill: css('--text-secondary'),
      'font-size': 12, text: pn.label }));
    if (pi === panels.length - 1) {
      for (let i = 0; i <= 5; i++) {
        const v = tmin + (tmax - tmin) * i / 5;
        // Pin the end ticks inward so they don't hang off the plot edges.
        svg.appendChild($('text', { x: sx(v), y: P.t + ih + 18,
          'text-anchor': i === 0 ? 'start' : i === 5 ? 'end' : 'middle',
          fill: css('--muted'), 'font-size': 11, text: Math.round(v) + 's' }));
      }
    }
    // crosshair + tooltip
    const cross = $('line', { y1: P.t, y2: P.t + ih, stroke: css('--text-secondary'),
      'stroke-width': 1, 'stroke-opacity': 0 });
    const dot = $('circle', { r: 4, fill: pn.color, stroke: css('--surface-1'),
      'stroke-width': 2, 'fill-opacity': 0 });
    svg.appendChild(cross); svg.appendChild(dot);
    const hit = $('rect', { x: P.l, y: P.t, width: iw, height: ih, fill: 'transparent' });
    svg.appendChild(hit);
    box.appendChild(svg);
    const tip = attachTip(box);
    hit.addEventListener('mousemove', ev => {
      const b = box.getBoundingClientRect();
      const frac = (ev.clientX - b.left) / b.width;
      const tv = tmin + (tmax - tmin) * (frac - P.l / W) / (iw / W);
      let i = 0, best = Infinity;
      t.forEach((tt, k) => { const d = Math.abs(tt - tv); if (d < best) { best = d; i = k; } });
      cross.setAttribute('x1', sx(t[i])); cross.setAttribute('x2', sx(t[i]));
      cross.setAttribute('stroke-opacity', .35);
      dot.setAttribute('cx', sx(t[i])); dot.setAttribute('cy', sy(vals[i]));
      dot.setAttribute('fill-opacity', 1);
      tip.show(`<b>t = ${t[i]}s</b><br>p99 ${fmt(ts.p99_ms[i])} ms `
        + `&middot; ${ts.msgs[i].toLocaleString()} msg/s`,
        ev.clientX - b.left, ev.clientY - b.top);
    });
    hit.addEventListener('mouseleave', () => {
      tip.hide(); cross.setAttribute('stroke-opacity', 0); dot.setAttribute('fill-opacity', 0);
    });
    mount.appendChild(box);
  });
}

/* ---------- 3. burst position (ordered categories -> ordinal ramp) ---------- */
function bursts(mount) {
  const B = R.bursts;
  const cols = [css('--ord-1'), css('--ord-2'), css('--ord-3'),
                css('--ord-4'), css('--ord-5')];
  const rows = B.latency_by_burst_size
    .map((v, i) => ({ name: v.label, v, color: cols[i % cols.length] }));
  const g = geom(mount), W = g.W;
  // Narrow: label sits above its bar, so the bar keeps the full width.
  // Wide: label to the left, value to the right of the bar end.
  const band = g.narrow ? 46 : 38;
  const H = band * rows.length + (g.narrow ? 40 : 46);
  const P = { t: 12, r: g.narrow ? 12 : 120, b: 34, l: g.narrow ? 4 : 120 };
  const iw = W - P.l - P.r, ih = H - P.t - P.b;
  const vmax = Math.max(...rows.map(r => r.v.p50_ms)) * 1.15 || 1;
  const box = $('div', { class: 'chartbox' });
  const svg = $('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img',
    'aria-label': 'Median burst latency by burst size' });
  const bh = g.narrow ? 20 : 26;
  rows.forEach((r, i) => {
    const top = P.t + i * band;
    const y = g.narrow ? top + 18 : top + (band - bh) / 2;
    const w = Math.max(2, (r.v.p50_ms / vmax) * iw);
    const label = `${fmt(r.v.p50_ms)} ms  (${r.v.bursts.toLocaleString()} bursts)`;
    if (g.narrow) {
      svg.appendChild($('text', { x: P.l, y: top + 11,
        fill: css('--text-primary'), 'font-size': 12.5, text: r.name }));
      svg.appendChild($('text', { x: W - P.r, y: top + 11, 'text-anchor': 'end',
        fill: css('--text-secondary'), 'font-size': 11.5, text: label }));
    } else {
      svg.appendChild($('text', { x: P.l - 12, y: y + bh / 2 + 4, 'text-anchor': 'end',
        fill: css('--text-primary'), 'font-size': 13, text: r.name }));
      svg.appendChild($('text', { x: P.l + w + 10, y: y + bh / 2 + 4,
        fill: css('--text-secondary'), 'font-size': 12.5, text: label }));
    }
    const rect = $('rect', { x: P.l, y, width: w, height: bh, rx: 4, fill: r.color });
    rect.dataset.i = i;
    svg.appendChild(rect);
  });
  if (!g.narrow) {
    svg.appendChild($('line', { x1: P.l, x2: P.l, y1: P.t, y2: P.t + ih,
      stroke: css('--axis'), 'stroke-width': 1 }));
  }
  svg.appendChild($('text', { x: P.l + iw / 2, y: H - 4, 'text-anchor': 'middle',
    fill: css('--text-secondary'), 'font-size': 12,
    text: g.narrow ? 'median latency by clump size (ms)'
                   : 'median burst latency, grouped by how many messages arrived together (ms)' }));
  box.appendChild(svg);
  const tip = attachTip(box);
  svg.addEventListener('mousemove', ev => {
    const t = ev.target.closest('rect'); if (!t || !t.dataset.i) { tip.hide(); return; }
    const r = rows[+t.dataset.i], b = box.getBoundingClientRect();
    tip.show(`<b>burst of ${r.name}</b><br>median ${fmt(r.v.p50_ms)} ms · p90 ${fmt(r.v.p90_ms)} ms`
      + `<br>${r.v.bursts.toLocaleString()} bursts, ${r.v.messages.toLocaleString()} messages`,
      ev.clientX - b.left, ev.clientY - b.top);
  });
  svg.addEventListener('mouseleave', tip.hide);
  mount.appendChild(box);
}

/* ---------- 4. the A/B experiment ---------- */
function experiment(mount) {
  const x = R.experiment;
  const rows = [
    { name: 'A — stdlib json', v: x.a_json, color: css('--series-1') },
    { name: 'B — orjson',      v: x.b_orjson, color: css('--series-2') },
  ];
  const g = geom(mount), W = g.W;
  const band = g.narrow ? 52 : 60;
  const H = band * rows.length + (g.narrow ? 34 : 30);
  const P = { t: 10, r: g.narrow ? 12 : 90, b: 34, l: g.narrow ? 4 : 130 };
  const iw = W - P.l - P.r;
  const vmax = Math.max(...rows.map(r => r.v.p99)) * 1.1;
  const sx = v => P.l + (v / vmax) * iw;
  const box = $('div', { class: 'chartbox' });
  const svg = $('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img',
    'aria-label': 'Parse time by treatment' });
  const bh = g.narrow ? 20 : 26;
  rows.forEach((r, i) => {
    const top = P.t + i * band;
    const y = g.narrow ? top + 22 : top + (band - bh) / 2;
    const w50 = Math.max(2, sx(r.v.p50) - P.l);
    const x99 = sx(r.v.p99);
    if (g.narrow) {
      svg.appendChild($('text', { x: P.l, y: top + 12,
        fill: css('--text-primary'), 'font-size': 12.5, text: r.name }));
      svg.appendChild($('text', { x: W - P.r, y: top + 12, 'text-anchor': 'end',
        fill: css('--text-secondary'), 'font-size': 12,
        text: `${fmt(r.v.p50)} µs` }));
    } else {
      svg.appendChild($('text', { x: P.l - 12, y: y + bh / 2 + 4, 'text-anchor': 'end',
        fill: css('--text-primary'), 'font-size': 13, text: r.name }));
      svg.appendChild($('text', { x: P.l + w50 + 10, y: y + bh / 2 + 4,
        fill: css('--text-secondary'), 'font-size': 12.5,
        text: `${fmt(r.v.p50)} µs` }));
    }
    // p50 bar, with a p99 whisker so the tail is visible too
    svg.appendChild($('rect', { x: P.l, y, width: w50, height: bh, rx: 4, fill: r.color }));
    svg.appendChild($('line', { x1: x99, x2: x99, y1: y - 4, y2: y + bh + 4,
      stroke: css('--text-secondary'), 'stroke-width': 2 }));
    svg.appendChild($('text', { x: Math.min(x99, W - 14), y: y - 8,
      'text-anchor': 'middle', fill: css('--muted'), 'font-size': 10.5, text: 'p99' }));
  });
  svg.appendChild($('text', { x: P.l + iw / 2, y: H - 4, 'text-anchor': 'middle',
    fill: css('--text-secondary'), 'font-size': 12,
    text: g.narrow ? 'parse time (µs) — bar p50, tick p99'
                   : 'median parse time per message (µs) — bar = p50, tick = p99' }));
  box.appendChild(svg);
  mount.appendChild(box);
}

/* ---------- page assembly ---------- */
function table(headers, rows) {
  // Wide numeric tables scroll inside their own container; the page body
  // must never scroll sideways.
  const wrap = $('div', { class: 'tablewrap' });
  const t = $('table');
  t.appendChild($('tr', {}, headers.map(h => $('th', { text: h }))));
  rows.forEach(r => t.appendChild($('tr', {}, r.map(c => $('td', { text: c })))));
  wrap.appendChild(t);
  return wrap;
}

function build() {
  const app = document.getElementById('app');
  // Idempotent: the live page calls this repeatedly. Remember which tables
  // the reader had expanded, and the scroll position, so a refresh does not
  // collapse the section they were reading or jump them back to the top.
  const wasOpen = [...app.querySelectorAll('details')].map(d => d.open);
  const scrollY = window.scrollY;
  app.textContent = '';

  const c = R.capture, L = R.latency, X = R.experiment;
  document.getElementById('subtitle').textContent =
    `${c.messages.toLocaleString()} trade messages from ${c.products.join(', ')} · `
    + `${Math.round(c.duration_s)}s capture · ${c.start_utc} → ${c.end_utc} UTC`;

  // pooling warning -- shown before anything else, because it invalidates
  // every percentile on the page.
  if (R.clock && R.clock.pool_warning) {
    const w = $('div', { class: 'card verdict' });
    w.appendChild($('div', { class: 'dot' }));
    w.lastChild.style.background = css('--critical');
    const wt = $('div');
    wt.appendChild($('h2', { text: 'These numbers pool separate capture sessions — read with care' }));
    wt.appendChild($('p', { text: R.clock.pool_warning }));
    if (R.clock.sessions && R.clock.sessions.length > 1) {
      wt.appendChild(table(['Capture', 'Messages', 'Baseline (floor) ms', 'p50 ms'],
        R.clock.sessions.map(s =>
          [s.file, s.n.toLocaleString(), fmt(s.floor_ms), fmt(s.p50)])));
    }
    w.appendChild(wt);
    app.appendChild(w);
  }

  // verdict -- lead with the deepest percentile the sample actually supports,
  // rather than asserting p99.9 off two observations.
  const deep = isSupported(L, 'p999') ? { key: 'p999', v: L.p999, label: '1 in 1,000' }
             : isSupported(L, 'p99')  ? { key: 'p99',  v: L.p99,  label: '1 in 100' }
             : null;
  const tailRatio = deep ? deep.v / L.p50 : null;
  const bad = tailRatio !== null && tailRatio > 3;
  const vc = $('div', { class: 'card verdict' });
  vc.appendChild($('div', { class: 'dot' }));
  vc.lastChild.style.background = bad ? css('--warning')
                                : tailRatio === null ? css('--muted') : css('--good');
  const vt = $('div');
  vt.appendChild($('h2', { text: tailRatio === null
    ? 'Not enough messages yet to describe the tail'
    : bad ? 'The tail is much slower than the typical message'
          : 'The feed is consistent — the tail tracks the typical message' }));
  vt.appendChild($('p', { text:
    `Half of all messages arrive within ${fmt(L.p50)} ms of the exchange timestamp. `
    + (deep
        ? `The slowest ${deep.label} takes ${fmt(deep.v)} ms — ${fmt(tailRatio, 1)}× the median — `
          + `and the single worst message took ${fmt(L.max)} ms. `
          + (isWeak(L, deep.key)
              ? `That tail figure rests on only ${supportOf(L, deep.key)} messages, so treat it as provisional. `
              : '')
        : `The window holds ${L.count.toLocaleString()} messages — too few to estimate a tail `
          + `percentile, though the worst message so far took ${fmt(L.max)} ms. `)
    + (R.volume_correlation
        ? `Across the capture, ${R.volume_correlation.interpretation} `
          + `(Spearman ρ = ${fmt(R.volume_correlation.spearman_rho, 2)}, `
          + `p ${pfmt(R.volume_correlation.p_value)}).`
        : '') }));
  vc.appendChild(vt);
  app.appendChild(vc);

  // KPI row
  const k = $('div', { class: 'kpis' });
  [['Median (p50)', 'p50', L.p50, 'half of messages are faster'],
   ['p99', 'p99', L.p99, '1 in 100 is slower'],
   ['p99.9', 'p999', L.p999, '1 in 1,000 is slower'],
   // `max` is a single observation by definition; its hint already says so,
   // so it is exempt from the support test rather than always failing it.
   ['Worst', null, L.max, 'slowest single message']
  ].forEach(([lab, key, v, hint]) => {
    const t = $('div', { class: 'kpi' });
    t.appendChild($('div', { class: 'label', text: lab }));
    const n = key ? supportOf(L, key) : null;
    if (key && !isSupported(L, key)) {
      // Refuse to print a number the window cannot support.
      t.appendChild($('div', { class: 'value dim', text: '—' }));
      t.appendChild($('div', { class: 'hint',
        text: `only ${n} message${n === 1 ? '' : 's'} above this — too few to estimate` }));
    } else {
      const val = $('div', { class: 'value', text: fmt(v) });
      val.appendChild($('span', { class: 'unit', text: 'ms' }));
      t.appendChild(val);
      t.appendChild($('div', { class: 'hint', text: hint }));
      if (key && isWeak(L, key)) {
        t.appendChild($('div', { class: 'hint warn',
          text: `based on just ${n} messages — treat as provisional` }));
      }
    }
    k.appendChild(t);
  });
  app.appendChild(k);

  // distribution
  let card = $('div', { class: 'card' });
  card.appendChild($('h2', { text: 'Where the time actually goes' }));
  card.appendChild($('p', { class: 'note', text:
    'Every message, bucketed by how long it took to reach us. The mean hides '
    + 'the tail; this shows it. Hover any bar for the exact count.' }));
  histogram(card);
  const d1 = $('details');
  d1.appendChild($('summary', { text: 'Show the numbers as a table' }));
  d1.appendChild(table(['Product', 'Messages', 'p50 ms', 'p99 ms', 'p99.9 ms', 'max ms'],
    Object.entries(R.latency_by_product).map(([p, q]) =>
      [p, q.count.toLocaleString(), fmt(q.p50), fmt(q.p99), fmt(q.p999), fmt(q.max)])
      .concat([['ALL', L.count.toLocaleString(), fmt(L.p50), fmt(L.p99),
                fmt(L.p999), fmt(L.max)]])));
  card.appendChild(d1);
  app.appendChild(card);

  // timeline
  card = $('div', { class: 'card' });
  card.appendChild($('h2', { text: 'Does the feed slow down when the market gets busy?' }));
  card.appendChild($('p', { class: 'note', text:
    'Tail latency and message volume, second by second, on a shared timeline. '
    + 'Two separate panels on purpose — overlaying them on one pair of axes would '
    + 'invent a correlation the data may not contain.' }));
  const lg = $('div', { class: 'legend' });
  [['p99 latency', css('--series-1')], ['messages/sec', css('--series-2')]].forEach(([n, col]) => {
    const s = $('span');
    const sw = $('div', { class: 'swatch' }); sw.style.background = col;
    s.appendChild(sw); s.appendChild(document.createTextNode(n)); lg.appendChild(s);
  });
  card.appendChild(lg);
  timeline(card);
  app.appendChild(card);

  // bursts -- what actually causes the tail
  if (R.bursts) {
    const B = R.bursts;
    card = $('div', { class: 'card' });
    card.appendChild($('h2', { text: 'What actually causes the slow messages' }));
    const sp = B.within_burst_spread_ms;
    card.appendChild($('p', { class: 'note', text:
      'Trades do not arrive evenly — they land in clumps, and '
      + `${fmt(B.pct_msgs_in_bursts, 0)}% of messages arrived back-to-back with another `
      + `(largest clump: ${B.max_burst_size} messages at once). Grouping by clump size shows `
      + 'where the slow messages live:' }));
    bursts(card);
    const bv = $('div', { class: 'verdict', style: 'margin-top:4px' });
    bv.appendChild($('div', { class: 'dot' }));
    const C = B.size_correlation;
    const real = C.significant && C.effect !== 'negligible';
    const tiny = C.significant && C.effect === 'negligible';
    bv.lastChild.style.background = real ? css('--warning') : css('--good');
    const bt = $('div');
    bt.appendChild($('h2', { text: real
      ? 'Big clumps are the slow ones — and they are held up as a unit'
      : tiny
      ? 'Clump size barely matters — but clumps are held up as a unit'
      : 'Clump size does not predict latency in this window' }));
    const stats = `(Spearman ρ = ${fmt(C.spearman_rho, 2)}, `
      + `p ${pfmt(C.p_value)}, across ${C.n_bursts.toLocaleString()} clumps `
      + 'rather than per message — messages inside one clump are not independent '
      + 'observations)';
    const unit = sp
      ? `Within a single clump the messages share almost the same delay: the median spread `
        + `inside a clump is ${fmt(sp.p50)} ms, so a batch is held up together rather than `
        + `draining one message at a time. `
      : '';
    bt.appendChild($('p', { text: real
      ? `Small clumps arrive at the normal speed; the large ones carry the tail ${stats}. `
        + unit
        + 'That points upstream of this process — neither a faster parser nor a bigger '
        + 'receive buffer would move it.'
      : tiny
      // Significant but negligible: say both halves out loud, because a bare
      // p-value here would overstate the finding badly.
      ? `Bigger clumps are very slightly slower, and with ${C.n_bursts.toLocaleString()} `
        + `clumps that trend is statistically detectable ${stats} — but the effect is far too `
        + 'small to act on, and only the largest clumps separate from the rest at all. '
        + 'A real p-value is not the same thing as a real effect. '
        + unit
        + 'The stalls are upstream of this process, and they are not explained by how much '
        + 'arrives at once.'
      : `Clump size shows no consistent relationship with latency `
        + `(ρ = ${fmt(C.spearman_rho, 2)}, p ${pfmt(C.p_value)}). ` + unit }));
    bv.appendChild(bt);
    card.appendChild(bv);
    const d3 = $('details');
    d3.appendChild($('summary', { text: 'Show the numbers as a table' }));
    d3.appendChild(table(
      ['Messages arriving together', 'Clumps', 'Messages', 'Median ms', 'p90 ms', 'Worst ms'],
      B.latency_by_burst_size.map(v =>
        [v.label, v.bursts.toLocaleString(), v.messages.toLocaleString(),
         fmt(v.p50_ms), fmt(v.p90_ms), fmt(v.max_ms)])));
    card.appendChild(d3);
    app.appendChild(card);
  }

  // experiment
  card = $('div', { class: 'card' });
  card.appendChild($('h2', { text: 'The experiment: does a faster JSON parser matter?' }));
  card.appendChild($('p', { class: 'note', text:
    'One variable changed: the library used to parse each message. Every message was '
    + 'parsed both ways, back to back, so the two treatments saw identical input — '
    + `a paired test over ${X.n_pairs.toLocaleString()} messages.` }));
  experiment(card);
  const verdict = $('div', { class: 'verdict', style: 'margin-top:8px' });
  verdict.appendChild($('div', { class: 'dot' }));
  verdict.lastChild.style.background = X.significant ? css('--good') : css('--muted');
  const vt2 = $('div');
  vt2.appendChild($('h2', { text: X.significant
    ? `Real improvement: ${fmt(X.median_speedup, 2)}× faster parsing`
    : 'No measurable difference' }));
  vt2.appendChild($('p', { text: X.significant
    ? `orjson parses the median message ${fmt(X.median_delta_us)} µs faster than the `
      + `standard library. A Wilcoxon signed-rank test on the paired samples gives `
      + `p ${pfmt(X.wilcoxon_p)}, so this is a genuine difference and not noise. `
      + `In context: at the observed peak of ${Math.max(...R.timeseries.msgs).toLocaleString()} `
      + `messages/sec that saves roughly `
      + `${fmt(X.median_delta_us * Math.max(...R.timeseries.msgs) / 1000, 2)} ms of CPU per second — `
      + `worth having, but far smaller than the ${fmt(deep ? deep.v : L.max)} ms `
      + `${deep ? (deep.key === 'p999' ? 'p99.9' : 'p99') : 'worst case'} above, which means `
      + `the tail is not being caused by parsing.`
    : `The difference is within noise (p ${pfmt(X.wilcoxon_p)}).` }));
  verdict.appendChild(vt2);
  card.appendChild(verdict);
  const d2 = $('details');
  d2.appendChild($('summary', { text: 'Show the numbers as a table' }));
  d2.appendChild(table(['Treatment', 'n', 'p50 µs', 'p99 µs', 'p99.9 µs', 'max µs'], [
    ['A — stdlib json', X.n_pairs.toLocaleString(), fmt(X.a_json.p50),
      fmt(X.a_json.p99), fmt(X.a_json.p999), fmt(X.a_json.max)],
    ['B — orjson', X.n_pairs.toLocaleString(), fmt(X.b_orjson.p50),
      fmt(X.b_orjson.p99), fmt(X.b_orjson.p999), fmt(X.b_orjson.max)],
  ]));
  card.appendChild(d2);
  app.appendChild(card);

  document.getElementById('foot').textContent = R.clock ? R.clock.note : '';

  // Restore reader state after the rebuild.
  const nowDetails = [...app.querySelectorAll('details')];
  wasOpen.forEach((o, i) => { if (nowDetails[i]) nowDetails[i].open = o; });
  if (scrollY) window.scrollTo(0, scrollY);
}

// Charts size themselves to their container, so a rotation or window resize
// needs a redraw. Debounced, and only when the width actually changed --
// mobile browsers fire resize on scroll as the URL bar hides.
let __lastW = 0, __rt = null;
addEventListener('resize', () => {
  const w = document.getElementById('app').clientWidth;
  if (w === __lastW) return;
  __lastW = w;
  clearTimeout(__rt);
  __rt = setTimeout(() => { if (R) build(); }, 150);
});
__BOOT__
</script>
</body>
</html>
"""

STATIC_BOOT = """R = JSON.parse(document.getElementById('data').textContent);
build();"""


def render_page(data_json: str, boot: str = STATIC_BOOT) -> str:
    """Fill the page template. `data_json` is inlined for the static build and
    left as `null` for the live server, which fetches its data instead."""
    # Guard against the JSON payload closing the <script> tag early.
    return (TEMPLATE
            .replace("__DATA__", data_json.replace("</", "<\\/"))
            .replace("__BOOT__", boot))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=Path("results.json"))
    ap.add_argument("--out", type=Path, default=Path("dashboard.html"))
    args = ap.parse_args()

    args.out.write_text(render_page(args.results.read_text()))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
