"""Trend analysis - pure functions. Robust slope (Theil-Sen) + significance (Mann-Kendall) on load-matched
bucket medians, so a change in duty cycle is not mistaken for degradation."""
import math, statistics

def theil_sen(xs, ys):
    sl = [(ys[j] - ys[i]) / (xs[j] - xs[i]) for i in range(len(xs)) for j in range(i + 1, len(xs)) if xs[j] != xs[i]]
    return statistics.median(sl) if sl else 0.0

def mann_kendall_p(ys):
    """Two-sided p-value for a monotonic trend (normal approximation, ties ignored)."""
    n = len(ys)
    s = sum((ys[j] > ys[i]) - (ys[j] < ys[i]) for i in range(n) for j in range(i + 1, n))
    var = n * (n - 1) * (2 * n + 5) / 18
    z = 0.0 if s == 0 else (s - math.copysign(1, s)) / math.sqrt(var)
    return 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))

def analyze(points, warn=None, min_points=8, alpha=0.01, min_rise=0.10):
    """points: [(t_seconds, value)] bucket medians, ascending. Returns dict with a score penalty (0-15)."""
    if len(points) < min_points:
        return dict(ok=False, significant=False, penalty=0.0, note=f"need {min_points} buckets, have {len(points)}")
    t0 = points[0][0]
    xs, ys = [(t - t0) / 3600 for t, _ in points], [v for _, v in points]
    slope = theil_sen(xs, ys); p = mann_kendall_p(ys)
    base = statistics.median(ys[: max(3, len(ys) // 3)])
    rise = slope * xs[-1] / base if base > 0 else 0.0
    level = statistics.median(ys[-3:])
    sig = p < alpha and slope > 0 and rise >= min_rise
    eta = (warn - level) / slope if (sig and warn is not None and level < warn) else None
    return dict(ok=True, significant=sig, slope_per_h=round(slope, 4), p=round(p, 5), rise=round(rise, 3),
                level=round(level, 3), eta_h=round(eta, 1) if eta is not None else None,
                penalty=round(15 * min(1.0, rise / 0.5), 1) if sig else 0.0)
