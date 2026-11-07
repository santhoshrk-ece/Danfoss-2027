"""Run: python test_health.py  (no database needed)"""
import health, analytics

ok = dict(current=6, power=2.5, heatsink=50, motor_temp=60, vib_rms=0.9)
assert health.compute(ok, "run")["score"] == 100
assert health.compute(ok, "run")["band"] == "Healthy"

warn_vib = dict(ok, vib_rms=2.6)                       # warning-level vibration
r = health.compute(warn_vib, "run")
print("warn vib:", r["score"], r["band"], "average", r["average"], "cap", r["cap"])
assert r["cap"] == 70 and r["score"] == 70 and r["band"] == "Watch"   # average alone would say ~84 (Healthy)

crit_cur = dict(ok, current=18.4)
r = health.compute(crit_cur, "run"); print("critical current:", r["score"], r["band"])
assert r["score"] <= 30 and r["band"] == "Critical"

idle = dict(current=0, power=0, heatsink=40, motor_temp=40, vib_rms=0.25)
r = health.compute(idle, "idle"); assert r["score"] == 100 and "vib_rms" not in r["signals"]   # run-only ignored

assert abs(health.signal_score(1.8, 1.8, 4.5) - 60) < 1e-6 and abs(health.signal_score(4.5, 1.8, 4.5) - 20) < 1e-9
assert health.signal_score(10, 1.8, 4.5) == 0

t = analytics.vib_trend([0.9] * 60 + [2.0] * 60)
print("trend:", t["flag"], t["penalty"]); assert t["flag"] and t["penalty"] > 0
assert not analytics.vib_trend([0.9] * 120)["flag"]
assert analytics.vib_trend([1] * 10)["penalty"] == 0
print("all health/analytics tests passed")
