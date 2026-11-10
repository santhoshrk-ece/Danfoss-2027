"""Run: python test_logic.py  (no database needed)"""
import sys, random, statistics
sys.path.insert(0, "../edge")
import health, anomaly, trend, comms
from sources import SimSource

lim = health.limits_for(10.0, 4.0)
def rows_const(n=300, state="run", cur=8.0, pw=3.0, hs=50, mt=65, vib=0.9, t0=1000):
    return [dict(ts=t0 + i, state=state, current=cur, power=pw, heatsink=hs, motor_temp=mt, vib_rms=vib) for i in range(n)]

# ---- health ----
h = health.compute(rows_const(), lim, 10, 4, now_ts=1300); assert h["score"] == 100 and h["band"] == "Healthy", h
h = health.compute(rows_const(cur=17), lim, 10, 4, now_ts=1300)             # 170% for 5 min
print("overload:", h["score"], h["band"], [r["text"][:40] for r in h["reasons"]]); assert h["band"] == "Action needed"
h = health.compute(rows_const(mt=100), lim, 10, 4, now_ts=1300)             # motor temp between warn and crit
print("hot motor:", h["score"], h["band"]); assert h["band"] in ("Watch", "Action needed") and h["p_limit"] > 15
h = health.compute(rows_const(n=300, state="ramp", cur=3, vib=9), lim, 10, 4, now_ts=1300)   # vib ignored when not running
assert h["score"] == 100
ev = [dict(ts=1250, severity="CRITICAL", signal="current")]
h = health.compute(rows_const(), lim, 10, 4, events=ev, now_ts=1300); print("recent critical event:", h["score"], h["band"])
assert 55 < h["score"] < 70 and h["reasons"]
h = health.compute(rows_const(), lim, 10, 4, anomaly_open={"vib_rms": 8.0}, trend={"penalty": 10, "text": "trend"}, now_ts=1300)
print("anomaly+trend:", h["score"], h["p_anomaly"], h["p_trend"]); assert h["p_anomaly"] > 10 and h["score"] < 85

# ---- trend ----
assert not trend.analyze([(i * 300, 0.9 + random.Random(i).gauss(0, .02)) for i in range(12)])["significant"]
t = trend.analyze([(i * 300, 0.9 + 0.05 * i) for i in range(12)], warn=1.8)
print("trend:", t); assert t["significant"] and t["penalty"] > 0 and t["eta_h"] is not None
assert not trend.analyze([(i * 300, 1.5 - 0.05 * i) for i in range(12)])["significant"]    # falling
assert not trend.analyze([(0, 1), (1, 2)])["ok"]

# ---- comms ----
rows = [(t, t + 1.0) for t in range(0, 100)] + [(t, 160.0) for t in range(130, 160)] + [(t, t + 1.0) for t in range(160, 200)]
m = comms.link_metrics(rows, 200.0, 3600, 0.0)
print("comms:", {k: m[k] for k in ("completeness", "lost_s", "backfilled", "latency_mean_s")}, m["gaps"], m["recovered"])
assert m["backfilled"] == 21 and len(m["recovered"]) == 1 and m["gaps"][0]["seconds"] == 30.0 and 0.8 <= m["completeness"] < 0.9   # rows delayed < 10 s count as live
assert comms.link_state(5) == "Online" and comms.link_state(30) == "Delayed" and comms.link_state(300) == "Offline"
m2 = comms.link_metrics(rows[:100], 400.0, 3600, 0.0); assert m2["gaps"][-1]["ongoing"] and m2["completeness"] < 0.3

# ---- anomaly: learn on healthy, detect wear fault & thermal fouling, no false alarm on healthy ----
def sim(profile, seed, minutes=75, fa=2700, gr=900):
    s = SimSource(0, profile, fa, gr, seed=seed); return [s.read(t) for t in range(minutes * 60)]
def run_detect(rows, model, step=10, persist=None):
    state, since, evs = {}, {}, []
    for end in range(int(model["trained_until"]) + 60, len(rows), step):
        for e in anomaly.detect(rows[max(0, end - 900):end], model, 10.0, state, since, persist):
            evs.append(e); since[e["signal"]] = e["ts"]
    return evs
wear = sim("wear", 1); model = anomaly.learn(wear, 10.0, 0, 300, 900); assert model and "75-100" in model["vib"], model
evs = run_detect(wear, model); first = next(e for e in evs if e["severity"] == "ANOMALY" and e["signal"] == "vib_rms")
print("wear: first vib anomaly at", round(first["ts"] / 60, 1), "min; fault starts 45 min;", len(evs), "events")
assert 2700 <= first["ts"] <= 2700 + 900
fouling = sim("fouling", 2); mf = anomaly.learn(fouling, 10.0, 0, 300, 900)
ef = [e for e in run_detect(fouling, mf) if e["severity"] == "ANOMALY"]
print("fouling:", [(e["signal"], round(e["ts"] / 60, 1)) for e in ef]); assert any(e["signal"] == "heatsink" for e in ef)
ok = sim("healthy", 3); mo = anomaly.learn(ok, 10.0, 0, 300, 900)
fa_persist = [e for e in run_detect(ok, mo) if e["severity"] == "ANOMALY"]
fa_nopersist = [e for e in run_detect(ok, mo, persist={k: (1, 1) for k in anomaly.PERSIST}) if e["severity"] == "ANOMALY"]
print("healthy false alarms with persistence:", len(fa_persist), "| without persistence:", len(fa_nopersist))
assert len(fa_persist) == 0 and len(fa_nopersist) > 0
assert anomaly.learn(wear[:600], 10.0, 0, 300, 900) is None                  # not enough data -> no model
print("all tests passed")
