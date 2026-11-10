"""Communication reliability - pure functions over (ts, received_at) pairs."""
import statistics

LIVE_MAX_S = 10.0     # delivery delay above this = the reading was buffered at the edge and back-filled
GAP_MIN_S = 5.0       # no reading for this long = a gap

def pct(v, p):
    v = sorted(v); return v[min(len(v) - 1, int(p / 100 * len(v)))] if v else None

def link_state(age_s):
    return "Online" if age_s < 15 else "Delayed" if age_s < 60 else "Offline"

def link_metrics(rows, now, window_s, first_ts):
    """rows: [(ts, received_at)] ascending by ts, all in the window. Times are epoch seconds."""
    start = max(now - window_s, first_ts)
    expected = max(1.0, now - start)
    received = len(rows)
    out = dict(window_s=round(now - start), expected=round(expected), received=received,
               completeness=round(min(1.0, received / expected), 4))
    gaps, late = [], []
    if rows and rows[0][0] - start >= GAP_MIN_S * 2:             # drive known, but silent at the start of the window
        gaps.append(dict(start=start, end=rows[0][0], seconds=round(rows[0][0] - start, 1), ongoing=False))
    for i, (ts, rec) in enumerate(rows):
        if rec - ts >= LIVE_MAX_S: late.append((ts, rec - ts))
        if i and ts - rows[i - 1][0] >= GAP_MIN_S:
            gaps.append(dict(start=rows[i - 1][0], end=ts, seconds=round(ts - rows[i - 1][0] - 1, 1), ongoing=False))
    if rows and now - rows[-1][0] >= GAP_MIN_S * 2:
        gaps.append(dict(start=rows[-1][0], end=now, seconds=round(now - rows[-1][0], 1), ongoing=True))
    eps = []                                                    # back-fill episodes = outages whose data was recovered
    for ts, d in late:
        if eps and ts - eps[-1]["end"] <= GAP_MIN_S: eps[-1].update(end=ts, n=eps[-1]["n"] + 1, max_delay_s=max(eps[-1]["max_delay_s"], round(d, 1)))
        else: eps.append(dict(start=ts, end=ts, n=1, max_delay_s=round(d, 1)))
    live = [rec - ts for ts, rec in rows if rec - ts < LIVE_MAX_S]
    out.update(lost_s=round(sum(g["seconds"] for g in gaps), 1), gaps=gaps[-5:], backfilled=len(late), recovered=eps[-5:],
               latency_mean_s=round(statistics.fmean(live), 2) if live else None,
               latency_p95_s=round(pct(live, 95), 2) if live else None)
    return out
