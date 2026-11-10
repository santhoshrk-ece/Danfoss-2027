"""Data sources. The edge only calls source.read(t) -> dict.
SimSource is used now; later a ModbusSource (drive) + SensorSource (temp/vibration) expose the same call."""
import math, random
import numpy as np

FS = 1000                                   # vibration samples per second
RATED_RPM, RATED_KW, RATED_I = 1450.0, 4.0, 10.0

class SimSource:
    """profile: healthy | wear (growing bearing-like vibration tone) | overload (every 3rd cycle) |
    fouling (heatsink runs hotter than the load explains)."""
    def __init__(self, t0, profile="healthy", fault_after_s=None, fault_growth_s=1800.0, seed=None):
        self.t0, self.profile, self.fault_after_s, self.growth = t0, profile, fault_after_s, fault_growth_s
        self.rng, self.np = random.Random(seed), np.random.default_rng(seed)
        self.heatsink, self.motor_temp, self.last_t = 30.0, 35.0, t0
        self.phase, self.ph2 = self.rng.uniform(0, 6.28), self.rng.uniform(0, 6.28)

    def fault_fraction(self, t):
        if self.fault_after_s is None or self.profile not in ("wear", "fouling"): return 0.0
        return min(1.0, max(0.0, (t - self.t0 - self.fault_after_s) / self.growth))

    def truth(self):
        """What the simulator injects - used ONLY by the evaluation tools, never by the detectors."""
        if self.fault_after_s is not None and self.profile in ("wear", "fouling"):
            return {"kind": "fault_planned", "fault": self.profile, "start": self.t0 + self.fault_after_s,
                    "growth_s": self.growth}

    def cycle(self, t):
        """120 s duty cycle -> (speed fraction s, load factor L, state)."""
        c, k = (t - self.t0) % 120, int((t - self.t0) // 120)
        if c < 10: return 0.0, 0.0, "idle"
        if c < 25: return (c - 10) / 15, 0.4, "ramp"
        if c < 95:
            L = 0.75 + 0.1 * math.sin(c / 7)
            if self.profile == "overload" and k % 3 == 2: L = 2.0 + 0.05 * math.sin(c)
            return 1.0, L, "run"
        return (120 - c) / 25, 0.4, "ramp"

    def read(self, t):
        s, L, state = self.cycle(t)
        freq, speed = 50 * s, RATED_RPM * s
        cur = max(0.0, s * (3 + 7 * L) + self.rng.gauss(0, 0.08)) if s > 0 else 0.0
        pw = RATED_KW * s * L * 0.95
        torque = pw * 9550 / speed if speed > 50 else 0.0
        dt = max(0.0, t - self.last_t); self.last_t = t
        foul = 25 * self.fault_fraction(t) if self.profile == "fouling" else 0.0
        hs_t = min(95.0, 32 + 30 * (cur / RATED_I) ** 2 + foul)
        mt_t = min(140.0, 35 + 45 * (cur / RATED_I) ** 2)
        self.heatsink += (hs_t - self.heatsink) * min(1, dt / 40)       # first-order thermal lag
        self.motor_temp += (mt_t - self.motor_temp) * min(1, dt / 90)
        # one second of vibration at 1 kHz: slowly varying 1x/2x tones + noise (+ occasional shocks, + wear tone)
        n = t + np.arange(FS) / FS
        drift = 1 + 0.06 * math.sin(2 * math.pi * (t - self.t0) / 900 + self.ph2)
        x = 1.2 * drift * s * np.sin(2 * math.pi * freq * n + self.phase) + 0.4 * s * np.sin(4 * math.pi * freq * n)
        x += self.np.normal(0, 0.25, FS)
        if s > 0 and self.rng.random() < 0.01:                           # random shock: one noisy second
            i = self.rng.randrange(0, FS - 50); x[i:i + 50] += self.np.normal(0, 3.0, 50)
        if self.profile == "wear":
            x += 7.0 * self.fault_fraction(t) * s * np.sin(2 * math.pi * 180 * n)
        x = x - x.mean()
        return dict(ts=t, state=state, freq=freq, current=cur, power=pw, speed=speed, torque=torque,
                    dc_link=565 - 12 * L * s + self.rng.gauss(0, 1.5), heatsink=self.heatsink,
                    motor_temp=self.motor_temp + self.rng.gauss(0, 0.1),
                    vib_rms=float(np.sqrt(np.mean(x ** 2))), vib_peak=float(np.abs(x).max()))
