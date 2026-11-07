"""Data sources. The edge code only calls source.read(t) -> dict.
SimSource is used now; later a ModbusSource (drive) + SensorSource (temp/vibration) will expose the same call."""
import math, random
import numpy as np

FS = 1000                                   # vibration samples per second
RATED_RPM, RATED_KW, RATED_I = 1450.0, 4.0, 10.0

class SimSource:
    def __init__(self, t0, fault_after_s=None, overload=False):
        self.t0, self.fault_after_s, self.overload = t0, fault_after_s, overload
        self.heatsink, self.motor_temp, self.last_t = 30.0, 35.0, t0
        self.phase = random.uniform(0, 2 * math.pi)

    def cycle(self, t):
        """120 s duty cycle -> (speed fraction s, load factor L, state)."""
        c = (t - self.t0) % 120
        if c < 10: return 0.0, 0.0, "idle"
        if c < 25: return (c - 10) / 15, 0.4, "ramp"
        if c < 95:
            L = 0.75 + 0.1 * math.sin(c / 7)
            if self.overload and 60 <= c < 80: L = 2.2        # overload burst
            return 1.0, L, "run"
        return (120 - c) / 25, 0.4, "ramp"

    def read(self, t):
        s, L, state = self.cycle(t)
        freq, speed = 50 * s, RATED_RPM * s
        cur = max(0.0, s * (3 + 7 * L) + random.gauss(0, 0.08)) if s > 0 else 0.0
        pw = RATED_KW * s * L * 0.95
        torque = pw * 9550 / speed if speed > 50 else 0.0
        # first-order lag: temperature drifts toward a target set by the present current
        dt = max(0.0, t - self.last_t); self.last_t = t
        self.heatsink += (32 + 30 * (cur / RATED_I) ** 2 - self.heatsink) * min(1, dt / 40)
        self.motor_temp += (35 + 45 * (cur / RATED_I) ** 2 - self.motor_temp) * min(1, dt / 90)
        # one second of vibration at 1 kHz: 1x running-frequency tone + noise (+ growing fault tone)
        n = t + np.arange(FS) / FS
        x = 1.2 * s * np.sin(2 * np.pi * freq * n + self.phase) + np.random.normal(0, 0.25, FS)
        if self.fault_after_s is not None and t - self.t0 >= self.fault_after_s:
            amp = min(7.0, 7.0 * (t - self.t0 - self.fault_after_s) / 300)   # grows over 5 min
            x += amp * s * np.sin(2 * np.pi * 180 * n)
        x = x - x.mean()
        return dict(ts=t, state=state, freq=freq, current=cur, power=pw, speed=speed, torque=torque,
                    dc_link=565 - 12 * L * s + random.gauss(0, 1.5),
                    heatsink=self.heatsink, motor_temp=self.motor_temp + random.gauss(0, 0.1),
                    vib_rms=float(np.sqrt(np.mean(x ** 2))), vib_peak=float(np.abs(x).max()))
