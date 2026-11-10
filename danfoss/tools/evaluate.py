"""Evaluate a LIVE run against the simulator's ground-truth log (edge/ground_truth.jsonl).
Metrics: data completeness, delivery latency, alert latency, anomaly detection delay, false alarms per hour,
data recovery after network outages. Ground truth is read from the file only - the cloud never sees it.
  python evaluate.py --api https://<your-app>.onrender.com --truth ../edge/ground_truth.jsonl"""
import argparse, csv, json, time, requests

ap = argparse.ArgumentParser()
ap.add_argument("--api", default="http://localhost:8000"); ap.add_argument("--truth", default="../edge/ground_truth.jsonl")
ap.add_argument("--out", default="live_eval_results.csv"); ap.add_argument("--learn-min", type=float, default=20,
                help="minutes of commissioning (warm-up + learning) to exclude from false-alarm hours")
a = ap.parse_args()
G = lambda p, **q: requests.get(a.api.rstrip("/") + p, params=q, timeout=30).json()
truth = [json.loads(l) for l in open(a.truth) if l.strip()]
stops = [t["t"] for t in truth if t["kind"] == "edge_stop"]
end = max(stops) if stops else time.time()
rows = []
for drive in sorted({t["drive"] for t in truth}):
    mine = [t for t in truth if t["drive"] == drive]
    t0 = min(t["t"] for t in mine if t["kind"] == "edge_start")
    fault = next((t for t in mine if t["kind"] == "fault_planned"), None)
    evs = G("/api/events", drive_id=drive, limit=2000, since=t0)
    an = [e for e in evs if e["severity"] == "ANOMALY"]
    sig = {"wear": "vib_rms", "fouling": "heatsink"}.get(fault["fault"]) if fault else None
    hit = min((e["ts"] for e in an if fault and e["signal"] == sig and e["ts"] >= fault["start"]), default=None)
    false = [e for e in an if not fault or e["ts"] < fault["start"]]
    hours = max(0.01, (min(end, fault["start"] if fault else end) - t0 - a.learn_min * 60) / 3600)
    c = G("/api/comms", drive_id=drive, minutes=int(min(360, (end - t0) / 60 + 5)))
    lat = [e["rec"] - e["ts"] for e in evs if e["severity"] in ("WARNING", "CRITICAL")]
    outs = []
    for o in (t for t in mine if t["kind"] == "outage_start"):
        e2 = next((t["t"] for t in mine if t["kind"] == "outage_end" and t["t"] >= o["t"]), o["end"])
        cov = G("/api/coverage", drive_id=drive, start=o["t"], end=e2)
        outs.append((cov["received"] / max(1, cov["expected"]), (cov["max_received_at"] or e2) - e2))
    rows.append(dict(drive=drive, completeness=c["completeness"], latency_mean_s=c["latency_mean_s"], latency_p95_s=c["latency_p95_s"],
                     alert_latency_s=round(sum(lat) / len(lat), 2) if lat else "",
                     anomaly_delay_min=round((hit - fault["start"]) / 60, 2) if hit else ("not detected" if fault else ""),
                     false_alarms=len(false), false_alarms_per_h=round(len(false) / hours, 2),
                     outages=len(outs), outage_recovery=f"{100 * sum(o[0] for o in outs) / len(outs):.0f}%" if outs else "",
                     recovery_delay_s=round(max(o[1] for o in outs), 1) if outs else ""))
keys = list(rows[0])
with open(a.out, "w", newline="") as f:
    w = csv.DictWriter(f, keys); w.writeheader(); w.writerows(rows)
print(" | ".join(keys))
for r in rows: print(" | ".join(str(r[k]) for k in keys))
print("saved", a.out)
