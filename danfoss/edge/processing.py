"""Edge logic: validate readings, apply limits with debounce, create events."""
import math

SIGNALS = ["current", "power", "heatsink", "motor_temp", "vib_rms"]
RUN_ONLY = {"power", "vib_rms"}             # not meaningful while the motor is stopped/ramping
NAMES = ["OK", "WARNING", "CRITICAL"]
UNITS = {"current": "A", "power": "kW", "heatsink": "degC", "motor_temp": "degC", "vib_rms": "mm/s"}

def validate(r, plausible):
    """Return the list of signals that are missing, non-finite or outside the plausible range."""
    bad = []
    for k, (lo, hi) in plausible.items():
        v = r.get(k)
        if v is None or not math.isfinite(v) or not lo <= v <= hi:
            bad.append(k)
    return bad

def level(v, lim):
    return 2 if v >= lim["crit"] else 1 if v >= lim["warn"] else 0

class RuleEngine:
    """A level change must persist for `debounce` samples before an event is raised (avoids chatter)."""
    def __init__(self, limits, debounce=3):
        self.limits, self.debounce = limits, debounce
        self.cur = {s: 0 for s in SIGNALS}
        self.pend = {s: (0, 0) for s in SIGNALS}

    def update(self, r):
        events = []
        for s in SIGNALS:
            new = 0 if (s in RUN_ONLY and r["state"] != "run") else level(r[s], self.limits[s])
            if new == self.cur[s]:
                self.pend[s] = (new, 0); continue
            pl, cnt = self.pend[s]
            cnt = cnt + 1 if pl == new else 1
            self.pend[s] = (new, cnt)
            if cnt >= self.debounce:
                up = new > self.cur[s]
                if up:
                    msg = f"{s} {r[s]:.1f} {UNITS[s]} reached {NAMES[new].lower()} limit {self.limits[s]['warn' if new == 1 else 'crit']}"
                else:
                    msg = f"{s} back below {NAMES[self.cur[s]].lower()} limit"
                events.append(dict(ts=r["ts"], severity=NAMES[new] if up else "INFO", signal=s,
                                   value=round(r[s], 2), msg=msg))
                self.cur[s], self.pend[s] = new, (new, 0)
        return NAMES[max(self.cur.values())], events
