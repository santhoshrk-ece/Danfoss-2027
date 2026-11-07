"""Health score logic - pure functions, no database, easy to test."""
LIMITS = {   # illustrative, NOT validated. Keep in sync with edge/config.yaml
    "current":    {"warn": 10.0, "crit": 16.0},
    "power":      {"warn": 4.0,  "crit": 6.4},
    "heatsink":   {"warn": 65,   "crit": 80},
    "motor_temp": {"warn": 80,   "crit": 100},
    "vib_rms":    {"warn": 1.8,  "crit": 4.5},
}
WEIGHTS = {"vib_rms": 0.30, "current": 0.25, "motor_temp": 0.20, "heatsink": 0.15, "power": 0.10}
RUN_ONLY = {"power", "vib_rms"}          # only meaningful while the motor is running

def signal_score(x, warn, crit):
    """100 up to 80% of warn -> 60 at warn -> 20 at crit -> 0 at 125% of crit (linear between)."""
    if x <= 0.8 * warn: return 100.0
    if x <= warn: return 100 - 40 * (x - 0.8 * warn) / (0.2 * warn)
    if x <= crit: return 60 - 40 * (x - warn) / (crit - warn)
    return max(0.0, 20 - 20 * (x - crit) / (0.25 * crit))

def level(x, lim):
    return 2 if x >= lim["crit"] else 1 if x >= lim["warn"] else 0

def band(score):
    return "Healthy" if score >= 80 else "Watch" if score >= 50 else "Critical"

def compute(values, state, trend_penalty=0.0, limits=LIMITS):
    """values: smoothed signal values. Returns None when there is nothing to score."""
    sig = {}
    for s, w in WEIGHTS.items():
        v = values.get(s)
        if v is None or (s in RUN_ONLY and state != "run"): continue
        sig[s] = dict(value=round(v, 2), score=round(signal_score(v, **limits[s])), level=level(v, limits[s]))
    if not sig: return None
    wsum = sum(WEIGHTS[s] for s in sig)
    avg = sum(WEIGHTS[s] * signal_score(sig[s]["value"], **limits[s]) for s in sig) / wsum
    worst = max(x["level"] for x in sig.values())
    cap = 30 if worst == 2 else 70 if worst == 1 else 100     # one bad signal can't be averaged away
    score = max(0.0, min(100.0, min(avg, cap) - trend_penalty))
    drivers = [s for s, x in sorted(sig.items(), key=lambda kv: kv[1]["score"]) if x["score"] < 100]
    return dict(score=round(score), band=band(score), signals=sig, average=round(avg),
                cap=cap if cap < 100 else None, trend_penalty=round(trend_penalty, 1), drivers=drivers)
