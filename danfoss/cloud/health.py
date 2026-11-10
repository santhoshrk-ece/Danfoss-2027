"""Health score v2 - pure functions, no database.

  Health = 100 - P_limit - P_anomaly - P_trend      (never below 0)

  P_limit   (0-60) Is a safe limit being approached? Time-aware for current/power (datasheet: 160% only for
                   1 min in 10): critical if > 160% of rated for 60 s, warning if > rated for 5 min. Temperatures and
                   vibration use smoothed values. A recent warning/critical limit event keeps a decaying penalty
                   for 10 min ("recent incident"), so a short excursion is not forgotten instantly.
  P_anomaly (0-25) Is it behaving unlike itself at this load? (anomaly.py)
  P_trend   (0-15) Is it getting steadily worse? (trend.py)

Bands: 85+ Healthy, 60-84 Watch, below 60 Action needed. Every penalty carries a human-readable reason."""
import statistics

DEFAULTS = {"heatsink": {"warn": 65, "crit": 80}, "motor_temp": {"warn": 90, "crit": 110},
            "vib_rms": {"warn": 1.8, "crit": 4.5}}                      # illustrative, NOT validated
OVERLOAD = 1.6
HOLD_S = 600
UNITS = {"current": "A", "power": "kW", "heatsink": "°C", "motor_temp": "°C", "vib_rms": "mm/s"}
LABEL = {"current": "Current", "power": "Power", "heatsink": "Heatsink temp", "motor_temp": "Motor temp",
         "vib_rms": "Vibration RMS"}

def limits_for(rated_a, rated_kw, overrides=None):
    lim = {k: dict(v) for k, v in DEFAULTS.items()}
    for k, v in (overrides or {}).items():
        if k in lim: lim[k].update(v)
    lim["current"] = {"warn": rated_a, "crit": OVERLOAD * rated_a}
    lim["power"] = {"warn": rated_kw, "crit": OVERLOAD * rated_kw}
    return lim

def pen(v, warn, crit):
    return 0.0 if v < warn else 60.0 if v >= crit else 15 + 25 * (v - warn) / (crit - warn)

def band(score):
    return "Healthy" if score >= 85 else "Watch" if score >= 60 else "Action needed"

def _sustained(rows, key, seconds):
    """Minimum over the last `seconds`, only if the drive ran continuously and data covers the window."""
    if not rows: return 0.0
    last = rows[-1]["ts"]; w = [r for r in rows if r["ts"] >= last - seconds]
    if len(w) < 0.8 * seconds or any(r["state"] != "run" for r in w): return 0.0
    return min(r[key] for r in w)

def _med(rows, key, seconds, run_only=False):
    if not rows: return None
    last = rows[-1]["ts"]
    v = [r[key] for r in rows if r["ts"] >= last - seconds and r[key] is not None and (not run_only or r["state"] == "run")]
    return statistics.median(v) if v else None

def compute(rows, lim, rated_a, rated_kw, events=(), anomaly_open=None, trend=None, now_ts=None):
    """rows: ascending recent readings (dicts). events: recent edge events [{ts, severity, signal}].
    anomaly_open: {signal: z} for open anomaly episodes. trend: {"penalty": float, "text": str}."""
    if not rows: return None
    reasons, sigs, pens = [], {}, {}
    for key, rated in (("current", rated_a), ("power", rated_kw)):
        crit_v, warn_v = _sustained(rows, key, 60), _sustained(rows, key, 300)
        if crit_v >= OVERLOAD * rated:
            p, why = 60.0, f"{LABEL[key]} above {OVERLOAD:.0%} of rated for 60 s (datasheet allows this only briefly)"
        elif warn_v >= rated:
            p = 15 + 25 * min(1.0, (warn_v - rated) / ((OVERLOAD - 1) * rated)); why = f"{LABEL[key]} above rated for 5 min"
        else: p, why = 0.0, ""
        pens[key] = p
        sigs[key] = dict(value=round(_med(rows, key, 10) or 0, 2), penalty=round(p))
        if p: reasons.append(dict(kind="limit", penalty=round(p), text=why))
    for key, secs, run_only in (("vib_rms", 30, True), ("motor_temp", 120, False), ("heatsink", 120, False)):
        v = _med(rows, key, secs, run_only)
        if v is None: continue
        p = pen(v, **lim[key]); pens[key] = p
        sigs[key] = dict(value=round(v, 2), penalty=round(p))
        if p: reasons.append(dict(kind="limit", penalty=round(p),
                                  text=f"{LABEL[key]} {v:.1f} {UNITS[key]} vs warning {lim[key]['warn']} / critical {lim[key]['crit']}"))
    active = [p for p in pens.values() if p > 0]
    p_now = max(active) if active else 0.0
    hold, hold_txt = 0.0, ""
    if now_ts is not None:
        for e in events:
            age = now_ts - e["ts"]
            if 0 <= age < HOLD_S and e["severity"] in ("WARNING", "CRITICAL"):
                h = (40 if e["severity"] == "CRITICAL" else 15) * (1 - age / HOLD_S)
                if h > hold: hold, hold_txt = h, f"Recent {e['severity'].lower()} limit event ({e['signal']}) {age / 60:.0f} min ago, penalty fades over 10 min"
    p_limit = min(60.0, max(p_now, hold) + 5 * max(0, len(active) - 1))
    if hold > p_now and hold_txt: reasons.append(dict(kind="limit", penalty=round(hold), text=hold_txt))
    p_anom = 0.0
    for sig, z in (anomaly_open or {}).items():
        p = 10 + min(15.0, 1.5 * max(0.0, abs(z) - 3.5)); p_anom = max(p_anom, p)
        reasons.append(dict(kind="anomaly", penalty=round(p), text=f"{LABEL[sig]} unusual for this drive (z={z:+.1f})"))
    p_tr = float((trend or {}).get("penalty", 0))
    if p_tr: reasons.append(dict(kind="trend", penalty=round(p_tr), text=trend.get("text", "Sustained upward trend")))
    score = max(0, round(100 - p_limit - p_anom - p_tr))
    reasons.sort(key=lambda r: -r["penalty"])
    return dict(score=score, band=band(score), p_limit=round(p_limit), p_anomaly=round(p_anom), p_trend=round(p_tr),
                reasons=reasons, signals=sigs)
