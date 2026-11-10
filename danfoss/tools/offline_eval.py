"""Offline evaluation of the anomaly detector on simulated faults, over many random seeds (runs in seconds, no cloud).
Ground truth comes from the simulator and is used ONLY to score the detector, never by the detector itself.
  python offline_eval.py --seeds 10
CAUTION: results describe the detector on SIMULATED data with a known fault shape. They validate the pipeline and
logic, not performance on real drives (which needs labelled real fault data)."""
import argparse, csv, os, statistics, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "edge"), os.path.join(HERE, "..", "cloud")]
import anomaly
from sources import SimSource

WARMUP, LEARN, MIN, FA, GROWTH = 300, 900, 90, 45 * 60, 15 * 60      # seconds; 90-min run, fault from minute 45

def simulate(profile, seed, fault_after=None):
    s = SimSource(0, profile, fault_after, GROWTH, seed=seed)
    return [s.read(t) for t in range(MIN * 60)]

def stream(rows, model, persist=None, step=10):
    """Replays rows the way the cloud sees them: a detection call every `step` s on the last 900 s."""
    state, since, evs = {}, {}, []
    for end in range(int(model["trained_until"]) + 60, len(rows), step):
        for e in anomaly.detect(rows[max(0, end - 900):end], model, 10.0, state, since, persist):
            evs.append(e); since[e["signal"]] = e["ts"]
    return evs

def episodes(evs, sig, t_end):
    """[(start, end)] of open anomaly episodes for one signal."""
    out, start = [], None
    for e in sorted((e for e in evs if e["signal"] == sig), key=lambda e: e["ts"]):
        if e["severity"] == "ANOMALY" and start is None: start = e["ts"]
        elif e["severity"] == "ANOMALY_CLEAR" and start is not None: out.append((start, e["ts"])); start = None
    if start is not None: out.append((start, t_end))
    return out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--seeds", type=int, default=5); a = ap.parse_args()
    rec = []
    for seed in range(a.seeds):
        for profile, sig in (("wear", "vib_rms"), ("fouling", "heatsink")):
            rows = simulate(profile, seed, FA); m = anomaly.learn(rows, 10.0, 0, WARMUP, LEARN)
            evs = stream(rows, m); eps = [e for e in episodes(evs, sig, len(rows)) if e[1] > FA]
            first = eps[0][0] if eps else None
            alarm = sum(e[1] - max(e[0], first) for e in eps) / (len(rows) - first) if first else 0.0
            rec.append(dict(kind="fault", profile=profile, seed=seed, detected=first is not None,
                            delay_min=round((first - FA) / 60, 2) if first else "", episodes=len(eps), time_in_alarm=round(alarm, 3)))
        rows = simulate("healthy", seed); m = anomaly.learn(rows, 10.0, 0, WARMUP, LEARN)
        hrs = (len(rows) - m["trained_until"]) / 3600
        fa = sum(1 for e in stream(rows, m) if e["severity"] == "ANOMALY")
        fa_np = sum(1 for e in stream(rows, m, {k: (1, 1) for k in anomaly.PERSIST}) if e["severity"] == "ANOMALY")
        rec.append(dict(kind="healthy", profile="healthy", seed=seed, false_alarms=fa, false_alarms_no_persistence=fa_np, hours=round(hrs, 2)))
    path = os.path.join(HERE, "offline_eval_results.csv")
    keys = sorted({k for r in rec for k in r}, key=lambda k: (k not in ("kind", "profile", "seed"), k))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, keys); w.writeheader(); w.writerows(rec)
    print(f"\nOffline evaluation: {a.seeds} seeds, fault starts at minute {FA // 60}, grows over {GROWTH // 60} min\n")
    for profile in ("wear", "fouling"):
        r = [x for x in rec if x["profile"] == profile]; d = [x["delay_min"] for x in r if x["detected"]]
        print(f"{profile:8s} detection rate {sum(x['detected'] for x in r)}/{len(r)}"
              + (f" | delay {statistics.mean(d):.1f} min (min {min(d):.1f}, max {max(d):.1f}"
                 + (f", sd {statistics.stdev(d):.2f}" if len(d) > 1 else "") + ")" if d else "")
              + f" | episodes per fault {statistics.mean(x['episodes'] for x in r):.1f} (1 = steady)"
              + f" | time in alarm after first detection {100 * statistics.mean(x['time_in_alarm'] for x in r):.0f}%")
    h = [x for x in rec if x["kind"] == "healthy"]; hrs = sum(x["hours"] for x in h)
    print(f"healthy  false alarms {sum(x['false_alarms'] for x in h)} in {hrs:.1f} h "
          f"({sum(x['false_alarms'] for x in h) / hrs:.2f}/h) with persistence | "
          f"{sum(x['false_alarms_no_persistence'] for x in h)} ({sum(x['false_alarms_no_persistence'] for x in h) / hrs:.2f}/h) without")
    print("saved", path)

if __name__ == "__main__":
    main()
