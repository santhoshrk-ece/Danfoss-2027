"""Longer-window analysis on data the edge already summarised (no raw-sample reprocessing)."""
import statistics

def vib_trend(ys):
    """ys: time-ordered vibration RMS values from run state. Flags a sustained rise; returns a score penalty."""
    n = len(ys)
    if n < 60:
        return dict(n=n, flag=False, penalty=0.0, note="Collecting data for trend analysis.")
    k = n // 3
    base, recent = sum(ys[:k]) / k, sum(ys[-k:]) / k
    xm, ym = (n - 1) / 2, sum(ys) / n
    slope = sum((i - xm) * (y - ym) for i, y in enumerate(ys)) / sum((i - xm) ** 2 for i in range(n))
    ratio = recent / base if base > 0 else 1.0
    flag = slope > 0 and ratio > 1.25
    penalty = min(20.0, 20 * (ratio - 1.25) / 0.75) if flag else 0.0
    note = (f"Vibration RMS rose from {base:.2f} to {recent:.2f} mm/s over this window - sustained upward trend. "
            "An indicator only; inspect the machine to confirm the cause." if flag
            else "No sustained vibration trend detected.")
    return dict(n=n, flag=flag, penalty=round(penalty, 1), baseline=round(base, 3), recent=round(recent, 3), note=note)

def baseline_stats(vals):
    return statistics.fmean(vals), statistics.pstdev(vals)

def zscore(x, mean, std):
    return (x - mean) / max(std, 0.05 * abs(mean), 1e-6)    # floor on std avoids huge z for very steady signals
