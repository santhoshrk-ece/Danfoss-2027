"""Learning checks: RMS formula, duty cycle plot, and the rule engine on a simulated fault."""
import numpy as np, yaml, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
from sources import SimSource
from processing import RuleEngine

x = 1.2 * np.sin(2 * np.pi * 50 * np.arange(1000) / 1000)
print("RMS of 1.2-amplitude sine:", round(float(np.sqrt(np.mean(x ** 2))), 3), "(expect 1.2/sqrt2 = 0.849)")

cfg = yaml.safe_load(open("config.yaml"))
src, eng = SimSource(0, fault_after_s=120, overload=True), RuleEngine(cfg["limits"], cfg["debounce_samples"])
rows = []
for t in range(600):
    r = src.read(t); r["status"], ev = eng.update(r); rows.append(r)
    for e in ev: print(f"t={t:3d}s {e['severity']:<8} {e['msg']}")
fig, ax = plt.subplots(3, 1, figsize=(9, 7), sharex=True)
ax[0].plot([r["current"] for r in rows]); ax[0].set_ylabel("current (A)")
ax[1].plot([r["heatsink"] for r in rows], label="heatsink"); ax[1].plot([r["motor_temp"] for r in rows], label="motor")
ax[1].set_ylabel("degC"); ax[1].legend()
ax[2].plot([r["vib_rms"] for r in rows]); ax[2].axhline(1.8, ls="--"); ax[2].axhline(4.5, ls="--")
ax[2].set_ylabel("vib RMS (mm/s)"); ax[2].set_xlabel("time (s)")
plt.tight_layout(); plt.savefig("check_sim.png", dpi=110); print("saved check_sim.png")
