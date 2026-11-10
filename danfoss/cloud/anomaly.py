"""Anomaly detection - pure functions (no database), so they can be unit-tested and evaluated offline.

Idea: "unusual FOR THIS DRIVE AT THIS OPERATING CONDITION", not "above a fixed number".
 * vib_rms     : robust z-score against the drive's own baseline in the same load bin.
 * motor_temp, heatsink : a first-order thermal model (temperature follows filtered I^2) is fitted on the
                  baseline period; anomaly = hotter than the model predicts. Judges temperature relative to load.
An anomaly must PERSIST (M of the last K samples beyond threshold) before it is raised, so one-second shocks
do not cause alarms. Thermal checks are skipped where the model was never trained (outside learned load range)."""
import math, statistics

Z_THRESH = 3.5
BIN_EDGES = [(0.25, "<25"), (0.5, "25-50"), (0.75, "50-75"), (1.0, "75-100")]
TAU = {"motor_temp": 90.0, "heatsink": 40.0}                  # thermal time constants (s), engineering assumption
PERSIST = {"vib_rms": (30, 20), "motor_temp": (60, 45), "heatsink": (60, 45)}   # (window K, needed M)
UNIT = {"vib_rms": "mm/s", "motor_temp": "°C", "heatsink": "°C"}

def load_bin(cur, rated):
    r = cur / rated
    for edge, name in BIN_EDGES:
        if r < edge: return name
    return ">100"

def _mad(v, med): return statistics.median(abs(x - med) for x in v)

def ema_load(rows, tau):
    """Exponentially filtered I^2 - what the thermal mass 'feels'. dt-aware, so gaps are handled."""
    out, x, prev = [], None, None
    for r in rows:
        u = r["current"] ** 2
        x = u if x is None else x + (u - x) * (1 - math.exp(-max(0.0, r["ts"] - prev) / tau))
        prev = r["ts"]; out.append(x)
    return out

def _fit(xs, ys):
    n = len(xs); mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx < 1e-6: return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return my - b * mx, b

def learn(rows, rated_a, t0, warmup_s, learn_s):
    """rows: ordered readings from the commissioning start t0. Warm-up is ignored; the next learn_s seconds
    are ASSUMED healthy and define 'normal'. Returns a JSON-serialisable model or None if data is insufficient."""
    lo, hi = t0 + warmup_s, t0 + warmup_s + learn_s
    train = [i for i, r in enumerate(rows) if lo <= r["ts"] < hi]
    if len(train) < 0.7 * learn_s: return None
    model = {"vib": {}, "thermal": {}, "trained_until": hi, "n": len(train)}
    bins = {}
    for i in train:
        r = rows[i]
        if r["state"] == "run": bins.setdefault(load_bin(r["current"], rated_a), []).append(r["vib_rms"])
    for b, v in bins.items():
        if len(v) >= 40:
            med = statistics.median(v); model["vib"][b] = {"median": med, "mad": _mad(v, med), "n": len(v)}
    for sig, tau in TAU.items():
        ema = ema_load(rows, tau)
        xs, ys = [ema[i] for i in train], [rows[i][sig] for i in train]
        f = _fit(xs, ys)
        if not f: continue
        a, b = f; res = [y - (a + b * x) for x, y in zip(xs, ys)]; med = statistics.median(res)
        model["thermal"][sig] = {"a": a, "b": b, "tau": tau, "res_med": med, "res_mad": _mad(res, med), "x_max": max(xs)}
    return model if (model["vib"] or model["thermal"]) else None

def _series(rows, model, rated_a):
    out = {}
    if model.get("vib"):
        s = []
        for r in rows:
            if r["state"] != "run": continue
            b = load_bin(r["current"], rated_a); m = model["vib"].get(b)
            if not m: continue
            z = 0.6745 * (r["vib_rms"] - m["median"]) / max(m["mad"], 0.05 * m["median"], 0.05)
            s.append((r["ts"], r["vib_rms"], z, b))
        out["vib_rms"] = s
    for sig, m in model.get("thermal", {}).items():
        s, warm = [], (rows[0]["ts"] + 3 * m["tau"] if rows else 0)    # filter needs ~3 time constants to forget its start
        for r, x in zip(rows, ema_load(rows, m["tau"])):
            if r["ts"] < warm or x > 1.3 * m["x_max"]: continue    # not warmed up / outside the trained load range: no decision
            z = 0.6745 * (r[sig] - (m["a"] + m["b"] * x) - m["res_med"]) / max(m["res_mad"], 0.5)
            s.append((r["ts"], r[sig], z, ""))
        out[sig] = s
    return out

def _flag(sig, z):
    return z > Z_THRESH if sig != "vib_rms" else abs(z) > Z_THRESH   # thermal: only 'hotter than expected'

def detect(rows, model, rated_a, state, since, persist=None):
    """rows: recent ordered readings (>= ~900 s for thermal filter warm-up). state: {signal: is_open};
    since: {signal: ts of last event}. Only samples newer than `since` can create events.
    Returns the list of new events; `state` is updated in place."""
    persist = persist or PERSIST
    events = []
    for sig, s in _series(rows, model, rated_a).items():
        K, M = persist[sig]
        pre = [0]
        for _, _, z, _ in s: pre.append(pre[-1] + (1 if _flag(sig, z) else 0))
        op, sn = state.get(sig, False), since.get(sig, model["trained_until"])
        for i, (ts, val, z, b) in enumerate(s):
            if ts <= sn or i + 1 < K: continue                     # old sample, or not enough context in this window
            cnt = pre[i + 1] - pre[max(0, i + 1 - K)]
            if not op and cnt >= M:
                op = True
                msg = (f"vib_rms {val:.2f} mm/s deviates from this drive's normal at {b}% load (z={z:+.1f})"
                       if sig == "vib_rms" else
                       f"{sig} {val:.1f} °C is hotter than the thermal model expects for the recent load (z={z:+.1f})")
                events.append(dict(ts=ts, severity="ANOMALY", signal=sig, value=round(val, 2), msg=msg))
            elif op and cnt <= M // 3:
                op = False
                events.append(dict(ts=ts, severity="ANOMALY_CLEAR", signal=sig, value=round(val, 2),
                                   msg=f"{sig} back within this drive's normal range"))
        state[sig] = op
    return events

def snapshot(rows, model, rated_a):
    """Recent robust z per signal (median of the last 30 valid samples) for display and the score penalty."""
    return {sig: round(statistics.median(z for _, _, z, _ in s[-30:]), 1)
            for sig, s in _series(rows, model, rated_a).items() if s}
