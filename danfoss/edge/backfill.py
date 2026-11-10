"""Seed the cloud with simulated HISTORY so the demo has charts, trends and a trained anomaly baseline immediately.
Posts with backfill=true (received_at = ts), so delivery-latency statistics are not polluted.
  python backfill.py --hours 2                 # healthy history for all drives (baseline learned, detectors armed)
  python backfill.py --hours 2 --with-fault    # also replay a bearing-wear story on the 'wear' drive"""
import argparse, time, yaml, requests
from sources import SimSource
from processing import RuleEngine

ap = argparse.ArgumentParser()
ap.add_argument("--config", default="config.yaml"); ap.add_argument("--hours", type=float, default=2)
ap.add_argument("--with-fault", action="store_true"); ap.add_argument("--seed", type=int, default=7)
a = ap.parse_args()
cfg = yaml.safe_load(open(a.config))
span = int(a.hours * 3600); end = time.time(); start = end - span

def post(d, readings, events):
    body = dict(drive_id=d["id"], name=d.get("name"), rated_a=d.get("rated_a"), rated_kw=d.get("rated_kw"),
                simulated=True, backfill=True, readings=readings, events=events)
    requests.post(cfg["cloud_url"].rstrip("/") + "/api/ingest", json=body, headers={"X-API-Key": cfg["api_key"]},
                  timeout=30).raise_for_status()

for i, d in enumerate(cfg["drives"]):
    fa, growth = (0.6 * span, 0.35 * span) if (a.with_fault and d["profile"] == "wear") else (None, 1800)
    src = SimSource(start, d["profile"], fa, growth, seed=a.seed + i)
    rules = RuleEngine(cfg["limits"], cfg["debounce_samples"])
    readings, events, n_ev = [], [], 0
    for k in range(span):
        r = src.read(start + k); r["status"], ev = rules.update(r)
        readings.append({x: round(v, 3) if isinstance(v, float) else v for x, v in r.items()}); events += ev; n_ev += len(ev)
        if len(readings) == 300 or k == span - 1:
            post(d, readings, events); readings, events = [], []
    print(f"{d['id']}: {span} readings, {n_ev} edge events posted")
