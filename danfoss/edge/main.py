"""Edge gateway: one thread per drive (like one gateway per drive). read -> validate -> rules -> buffer -> upload.
Also writes ground_truth.jsonl (what the simulator injected) for the evaluation tools."""
import argparse, json, random, threading, time, yaml
from sources import SimSource
from processing import validate, RuleEngine
from uploader import Uploader

ap = argparse.ArgumentParser()
ap.add_argument("--config", default="config.yaml")
ap.add_argument("--drives", help="comma-separated drive ids to run (default: all)")
ap.add_argument("--dry-run", action="store_true", help="no network; print only")
ap.add_argument("--seed", type=int, help="make the simulation repeatable")
a = ap.parse_args()
cfg = yaml.safe_load(open(a.config))
lock = threading.Lock()
open("ground_truth.jsonl", "w").close()

def truth(**kw):
    with lock, open("ground_truth.jsonl", "a") as f: f.write(json.dumps(kw) + "\n")

class Net:
    """Simulated network faults: fixed outages [[after_s, secs]] and/or random (flaky) outages."""
    def __init__(self, drive_id, cfg, start, rng):
        cfg = cfg or {}
        self.id, self.rng, self.flaky = drive_id, rng, cfg.get("flaky", False)
        self.fixed = sorted((start + x, start + x + y) for x, y in cfg.get("outages", []))
        self.nxt, self.win = start + rng.uniform(60, 180), None
    def is_down(self, now):
        if self.win and now >= self.win[1]:
            truth(kind="outage_end", drive=self.id, t=self.win[1]); self.win = None
        if not self.win:
            if self.fixed and now >= self.fixed[0][0]: self.win = self.fixed.pop(0)
            elif self.flaky and now >= self.nxt:
                self.win = (now, now + self.rng.uniform(15, 60)); self.nxt = self.win[1] + self.rng.uniform(180, 420)
            if self.win: truth(kind="outage_start", drive=self.id, t=self.win[0], end=self.win[1])
        return self.win is not None

def run(d, start):
    rng = random.Random(f"{a.seed}-{d['id']}" if a.seed is not None else None)
    src = SimSource(start, d["profile"], d.get("fault_after_s"), d.get("fault_growth_s", 1800), seed=rng.randrange(2**31))
    if src.truth(): truth(drive=d["id"], **src.truth())
    truth(kind="edge_start", drive=d["id"], t=start)
    rules = RuleEngine(cfg["limits"], cfg["debounce_samples"])
    up = Uploader(cfg["cloud_url"], cfg["api_key"], d, cfg["simulated"], cfg["upload_every_s"], f"buffer_{d['id']}.jsonl")
    net = Net(d["id"], d.get("network"), start, rng)
    while True:
        tick = time.time()
        r = src.read(tick)
        bad = validate(r, cfg["plausible"])
        if bad:
            print(f"[{d['id']}] dropped reading, bad signals: {bad}")
        else:
            r["status"], events = rules.update(r)
            r = {k: round(v, 3) if isinstance(v, float) else v for k, v in r.items()}
            up.add(r, events)
            for e in events: print(f"[{d['id']}] EVENT {e['severity']}: {e['msg']}")
        down = net.is_down(tick)
        if not a.dry_run and up.due(tick):
            if up.flush(tick, offline=down) is False:
                print(f"[{d['id']}] upload failed -> {len(up.readings)} readings buffered")
        time.sleep(max(0, 1 - (time.time() - tick)))

start = time.time()
chosen = a.drives.split(",") if a.drives else None
threads = [threading.Thread(target=run, args=(d, start), daemon=True)
           for d in cfg["drives"] if not chosen or d["id"] in chosen]
print(f"edge started: {len(threads)} simulated gateway(s) -> {'dry-run' if a.dry_run else cfg['cloud_url']}")
for t in threads: t.start()
try:
    while True: time.sleep(1)
except KeyboardInterrupt:
    for d in cfg["drives"]: truth(kind="edge_stop", drive=d["id"], t=time.time())
