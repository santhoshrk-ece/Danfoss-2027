"""Edge gateway main loop: read -> validate -> rules -> buffer -> upload."""
import argparse, time, yaml
from sources import SimSource
from processing import validate, RuleEngine
from uploader import Uploader

ap = argparse.ArgumentParser()
ap.add_argument("--config", default="config.yaml")
ap.add_argument("--dry-run", action="store_true", help="no network; print only")
ap.add_argument("--fault-after", type=float, help="start growing vibration fault after N seconds")
ap.add_argument("--overload", action="store_true", help="add overload bursts to the duty cycle")
ap.add_argument("--outage-after", type=float, default=0, help="cut the network N s after start")
ap.add_argument("--outage-secs", type=float, default=30)
a = ap.parse_args()
cfg = yaml.safe_load(open(a.config))

start = time.time()
src = SimSource(start, a.fault_after, a.overload)
rules = RuleEngine(cfg["limits"], cfg["debounce_samples"])
up = Uploader(cfg["cloud_url"], cfg["api_key"], cfg["drive_id"], cfg["simulated"],
              cfg["upload_every_s"], cfg["buffer_file"])
print(f"edge started: {cfg['drive_id']} (SIMULATED) -> {'dry-run' if a.dry_run else cfg['cloud_url']}")

while True:
    tick = time.time()
    r = src.read(tick)
    bad = validate(r, cfg["plausible"])
    if bad:
        print(f"dropped reading, bad signals: {bad}")
    else:
        r["status"], events = rules.update(r)
        r = {k: round(v, 3) if isinstance(v, float) else v for k, v in r.items()}
        up.add(r, events)
        for e in events: print(f"  EVENT {e['severity']}: {e['msg']}")
        print(f"{time.strftime('%H:%M:%S')} {r['state']:<5} I={r['current']:5.1f}A P={r['power']:4.1f}kW "
              f"Tm={r['motor_temp']:5.1f} vib={r['vib_rms']:4.2f} {r['status']:<8} buffered={len(up.readings)}")
    outage = a.outage_after and a.outage_after < tick - start < a.outage_after + a.outage_secs
    if not a.dry_run and up.due(tick):
        if up.flush(tick, offline=bool(outage)) is False:
            print("  upload failed -> data kept in buffer")
    time.sleep(max(0, 1 - (time.time() - tick)))
