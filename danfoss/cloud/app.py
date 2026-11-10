"""Cloud API: ingest -> store -> detect anomalies -> score -> serve dashboard."""
import asyncio, hmac, os, statistics, time
from contextlib import asynccontextmanager
from typing import List, Optional
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field
import anomaly, comms, health, trend
from db import init_schema, pool

HERE = os.path.dirname(os.path.abspath(__file__))
API_KEY = os.getenv("API_KEY", "demo-key")
WARMUP_S = float(os.getenv("WARMUP_S", 300))      # commissioning: ignored warm-up ...
LEARN_S = float(os.getenv("LEARN_S", 900))        # ... then this many seconds are ASSUMED healthy = "normal"
STALE_S, RAW_KEEP_H = float(os.getenv("STALE_S", 20)), 24
SIGNALS = ["freq", "current", "power", "speed", "torque", "dc_link", "heatsink", "motor_temp", "vib_rms", "vib_peak"]
ROW_SQL = ("select drive_id, extract(epoch from ts)::float8 as ts, extract(epoch from received_at)::float8 as rec, "
           "extract(epoch from now())::float8 as now_ts, state, status, current, power, heatsink, motor_temp, vib_rms")

class Reading(BaseModel):
    ts: float; state: str; status: str
    freq: float; current: float; power: float; speed: float; torque: float
    dc_link: float; heatsink: float; motor_temp: float; vib_rms: float; vib_peak: float

class Event(BaseModel):
    ts: float; severity: str; signal: str; value: float; msg: str

class EdgeStatus(BaseModel):
    boot_id: str = ""; uptime_s: float = 0; buffer_depth: int = 0; failures: int = 0; last_upload_ms: Optional[float] = None

class Batch(BaseModel):
    drive_id: str = Field(min_length=1, max_length=40)
    name: Optional[str] = None; model: Optional[str] = None
    rated_a: Optional[float] = None; rated_kw: Optional[float] = None
    simulated: bool = True
    backfill: bool = False                      # history seeding: received_at = ts (keeps latency stats honest)
    edge: Optional[EdgeStatus] = None
    readings: List[Reading] = Field(default_factory=list, max_length=1000)
    events: List[Event] = Field(default_factory=list, max_length=300)

ROLLUP_SQL = """
insert into readings_1m (drive_id, ts, n, current, current_max, power, heatsink, motor_temp, vib_rms, vib_rms_max)
select drive_id, date_trunc('minute', ts), count(*), avg(current), max(current), avg(power),
       avg(heatsink), avg(motor_temp), avg(vib_rms), max(vib_rms)
from readings where ts < date_trunc('minute', now() - interval '5 minutes') group by 1, 2
on conflict (drive_id, ts) do update set n=excluded.n, current=excluded.current, current_max=excluded.current_max,
  power=excluded.power, heatsink=excluded.heatsink, motor_temp=excluded.motor_temp,
  vib_rms=excluded.vib_rms, vib_rms_max=excluded.vib_rms_max"""

def rollup():
    """Retention: roll complete minutes into readings_1m, then drop raw rows older than RAW_KEEP_H."""
    with pool().connection() as c:
        c.execute(ROLLUP_SQL)
        c.execute("delete from readings where ts < now() - make_interval(hours => %s)", (RAW_KEEP_H,))

async def rollup_loop():
    while True:
        try: await asyncio.to_thread(rollup)
        except Exception as e: print("rollup failed:", e)
        await asyncio.sleep(3600)

@asynccontextmanager
async def lifespan(app):
    init_schema()
    task = asyncio.create_task(rollup_loop())      # also runs once at startup
    yield
    task.cancel()

app = FastAPI(title="Drive Monitoring Cloud", lifespan=lifespan)

def check_key(k):
    if not hmac.compare_digest(k.encode(), API_KEY.encode()): raise HTTPException(401, "bad api key")

@app.get("/healthz")
def healthz():
    return {"ok": True}

# ---------------------------------------------------------------- ingest
@app.post("/api/ingest")
def ingest(b: Batch, x_api_key: str = Header(default="")):
    check_key(x_api_key)
    now = time.time()
    rs = [r for r in b.readings if abs(r.ts - now) < 30 * 86400]
    es = [e for e in b.events if abs(e.ts - now) < 30 * 86400]
    ph = ", ".join(["%s"] * len(SIGNALS))
    with pool().connection() as c, c.cursor() as cur:
        cur.execute("""insert into drives (drive_id, name, model, rated_a, rated_kw, commissioned_at)
            values (%(d)s, %(n)s, %(m)s, coalesce(%(ra)s, 10.0), coalesce(%(rk)s, 4.0), to_timestamp(%(f)s))
            on conflict (drive_id) do update set name = coalesce(%(n)s, drives.name), model = coalesce(%(m)s, drives.model),
              rated_a = coalesce(%(ra)s, drives.rated_a), rated_kw = coalesce(%(rk)s, drives.rated_kw),
              commissioned_at = coalesce(drives.commissioned_at, to_timestamp(%(f)s))""",
            dict(d=b.drive_id, n=b.name, m=b.model, ra=b.rated_a, rk=b.rated_kw, f=min([r.ts for r in rs] or [now])))
        if rs:
            if b.backfill:
                cur.executemany(f"insert into readings (drive_id, ts, received_at, simulated, state, status, {', '.join(SIGNALS)}) "
                                f"values (%s, to_timestamp(%s), to_timestamp(%s), %s, %s, %s, {ph}) on conflict do nothing",
                                [(b.drive_id, r.ts, r.ts, b.simulated, r.state, r.status, *[getattr(r, s) for s in SIGNALS]) for r in rs])
            else:
                cur.executemany(f"insert into readings (drive_id, ts, simulated, state, status, {', '.join(SIGNALS)}) "
                                f"values (%s, to_timestamp(%s), %s, %s, %s, {ph}) on conflict do nothing",
                                [(b.drive_id, r.ts, b.simulated, r.state, r.status, *[getattr(r, s) for s in SIGNALS]) for r in rs])
        store_events(cur, b.drive_id, [e.model_dump() for e in es], b.backfill)
        if b.edge:
            cur.execute("""insert into drive_status (drive_id, updated_at, boot_id, uptime_s, buffer_depth, failures, last_upload_ms)
                values (%s, now(), %s, %s, %s, %s, %s) on conflict (drive_id) do update set updated_at=now(), boot_id=excluded.boot_id,
                uptime_s=excluded.uptime_s, buffer_depth=excluded.buffer_depth, failures=excluded.failures,
                last_upload_ms=excluded.last_upload_ms""",
                (b.drive_id, b.edge.boot_id, b.edge.uptime_s, b.edge.buffer_depth, b.edge.failures, b.edge.last_upload_ms))
    if rs:
        try:
            with pool().connection() as c: run_detection(c, b.drive_id, b.backfill)
        except Exception as e:                                  # detection problems must never block ingest
            print("detection error:", repr(e))
    return {"ok": True, "readings": len(rs), "events": len(es)}

def store_events(cur, d, evs, backfill=False):
    if not evs: return
    if backfill:
        cur.executemany("insert into events (drive_id, ts, received_at, severity, signal, value, msg) "
                        "values (%s, to_timestamp(%s), to_timestamp(%s), %s, %s, %s, %s) on conflict do nothing",
                        [(d, e["ts"], e["ts"], e["severity"], e["signal"], e["value"], e["msg"]) for e in evs])
    else:
        cur.executemany("insert into events (drive_id, ts, severity, signal, value, msg) "
                        "values (%s, to_timestamp(%s), %s, %s, %s, %s) on conflict do nothing",
                        [(d, e["ts"], e["severity"], e["signal"], e["value"], e["msg"]) for e in evs])

# ---------------------------------------------------------------- anomaly detection (runs after each ingest)
def fetch_rows(c, d, where, params, limit):
    return c.execute(f"{ROW_SQL} from readings where drive_id=%s and {where} order by ts limit %s", (d, *params, limit)).fetchall()

def run_detection(c, d, backfill=False):
    reg = c.execute("select rated_a, extract(epoch from commissioned_at)::float8 as t0 from drives where drive_id=%s", (d,)).fetchone()
    latest = c.execute("select extract(epoch from max(ts))::float8 as t from readings where drive_id=%s", (d,)).fetchone()["t"]
    if not reg or latest is None: return
    m = c.execute("select model from anomaly_models where drive_id=%s", (d,)).fetchone()
    if not m:
        if latest < reg["t0"] + WARMUP_S + LEARN_S: return
        rows = fetch_rows(c, d, "ts >= to_timestamp(%s)", (reg["t0"],), int(WARMUP_S + LEARN_S) + 300)
        model = anomaly.learn(rows, reg["rated_a"], reg["t0"], WARMUP_S, LEARN_S)
        if model:
            c.execute("insert into anomaly_models (drive_id, model) values (%s, %s) on conflict (drive_id) "
                      "do update set model=excluded.model, trained_at=now()", (d, Jsonb(model)))
        return
    rows = fetch_rows(c, d, "ts > to_timestamp(%s)", (latest - 900,), 1200)
    last = c.execute("select distinct on (signal) signal, severity, extract(epoch from ts)::float8 as ts from events "
                     "where drive_id=%s and severity in ('ANOMALY','ANOMALY_CLEAR') order by signal, ts desc", (d,)).fetchall()
    state = {r["signal"]: r["severity"] == "ANOMALY" for r in last}
    since = {r["signal"]: r["ts"] for r in last}
    evs = anomaly.detect(rows, m["model"], reg["rated_a"], state, since)
    if evs:
        with c.cursor() as cur: store_events(cur, d, evs, backfill)

# ---------------------------------------------------------------- trend (cached: queries are heavier)
_trend_cache = {}
WINDOWS = [("1 h", 3600, 300, 60), ("24 h", 86400, 3600, 300)]     # (name, span s, bucket s, min rows per bucket)

def trend_for(c, d, reg, lim):
    hit = _trend_cache.get(d)
    if hit and time.time() - hit[0] < 60: return hit[1]
    res, best = {"vib_rms": {}, "motor_temp": {}}, {"penalty": 0.0, "text": ""}
    for wname, span, bucket, minn in WINDOWS:
        rows = c.execute(
            "select floor(extract(epoch from ts) / %s)::float8 * %s as b, "
            "percentile_cont(0.5) within group (order by vib_rms::float8) as vib, "
            "percentile_cont(0.5) within group (order by motor_temp::float8) as mt "
            "from readings where drive_id=%s and state='run' and current between %s and %s "
            "and ts > now() - make_interval(secs => %s) group by 1 having count(*) >= %s order by 1",
            (bucket, bucket, d, 0.75 * reg["rated_a"], 1.0 * reg["rated_a"], span, minn)).fetchall()
        for sig, col in (("vib_rms", "vib"), ("motor_temp", "mt")):
            a = trend.analyze([(r["b"], r[col]) for r in rows], warn=lim[sig]["warn"])
            res[sig][wname] = a
            if a["penalty"] > best["penalty"]:
                eta = f"; about {a['eta_h']} h to the warning limit if the rate holds (extrapolation, not a prediction)" if a.get("eta_h") else ""
                best = {"penalty": a["penalty"], "text": f"{health.LABEL[sig]} rising {a['slope_per_h']:+.3f} {health.UNITS[sig]}/h over {wname} at matched load{eta}"}
    out = dict(best, detail=res); _trend_cache[d] = (time.time(), out); return out

# ---------------------------------------------------------------- summaries
def summarize_all(c, only=None):
    regs = {r["drive_id"]: r for r in c.execute("select *, extract(epoch from commissioned_at)::float8 as t0 from drives order by drive_id").fetchall()}
    rows = {}
    for r in c.execute(f"select * from ({ROW_SQL}, row_number() over (partition by drive_id order by ts desc) rn from readings "
                       "where ts > now() - interval '10 minutes' and (%s::text is null or drive_id = %s)) x "
                       "where rn <= 300 order by drive_id, ts", (only, only)).fetchall():
        rows.setdefault(r["drive_id"], []).append(r)
    events = {}
    for e in c.execute("select drive_id, extract(epoch from ts)::float8 as ts, severity, signal from events "
                       "where ts > now() - interval '10 minutes' and severity in ('WARNING','CRITICAL')").fetchall():
        events.setdefault(e["drive_id"], []).append(e)
    opened = {}
    for e in c.execute("select distinct on (drive_id, signal) drive_id, signal, severity from events "
                       "where severity in ('ANOMALY','ANOMALY_CLEAR') order by drive_id, signal, ts desc").fetchall():
        if e["severity"] == "ANOMALY": opened.setdefault(e["drive_id"], []).append(e["signal"])
    models = {m["drive_id"]: m["model"] for m in c.execute("select drive_id, model from anomaly_models").fetchall()}
    out = []
    for d, reg in regs.items():
        if only and d != only: continue
        rs = rows.get(d)
        if not rs:                                           # silent for > 10 min: fall back to the last known row
            rs = c.execute(f"{ROW_SQL} from readings where drive_id=%s order by ts desc limit 1", (d,)).fetchall()
        if not rs: continue
        lim = health.limits_for(reg["rated_a"], reg["rated_kw"], reg["limits"])
        now_ts, last = rs[-1]["now_ts"], rs[-1]
        age = now_ts - max(r["rec"] for r in rs); stale = age > STALE_S
        z_open = {}
        if opened.get(d) and d in models:
            z = anomaly.snapshot(fetch_rows(c, d, "ts > now() - interval '15 minutes'", (), 1000), models[d], reg["rated_a"])
            z_open = {s: z[s] for s in opened[d] if s in z}
        tr = trend_for(c, d, reg, lim)
        h = None if stale else health.compute(rs, lim, reg["rated_a"], reg["rated_kw"], events.get(d, []),
                                              z_open or {s: 4.0 for s in opened.get(d, [])}, tr, now_ts)
        n5 = sum(1 for r in rs if r["ts"] >= now_ts - 300)
        comp5 = min(1.0, n5 / max(1.0, min(300.0, now_ts - reg["t0"])))
        learn_pct = None if d in models else round(min(1.0, max(0.0, (rs[-1]["ts"] - reg["t0"]) / (WARMUP_S + LEARN_S))), 2)
        runv = [r["vib_rms"] for r in rs if r["state"] == "run" and r["ts"] >= now_ts - 300]
        out.append(dict(drive_id=d, name=reg["name"] or d, model=reg["model"], rated_a=reg["rated_a"], rated_kw=reg["rated_kw"],
                        state=last["state"], edge_status=last["status"],
                        last={k: round(last[k], 2) for k in ("current", "power", "heatsink", "motor_temp", "vib_rms")},
                        age_s=round(age, 1), link=comms.link_state(age), health=h,
                        confidence="low" if comp5 < 0.8 and not stale else "ok", completeness_5m=round(comp5, 3),
                        anomaly=dict(status="active" if d in models else "learning", progress=learn_pct,
                                     open=opened.get(d, []), z=z_open),
                        trend=dict(penalty=tr["penalty"], text=tr["text"]),
                        vib_5m=round(statistics.median(runv), 3) if runv else None, limits=lim))
    vs = [x["vib_5m"] for x in out if x["vib_5m"] is not None]
    if len(vs) >= 3:                                         # peer comparison: only meaningful with >= 3 drives
        med = statistics.median(vs)
        for x in out: x["peer_ratio"] = round(x["vib_5m"] / med, 2) if x["vib_5m"] and med else None
    return out

@app.get("/api/fleet")
def fleet():
    with pool().connection() as c:
        ds = summarize_all(c)
        bands = {"Healthy": 0, "Watch": 0, "Action needed": 0, "Unknown": 0}
        for x in ds: bands[x["health"]["band"] if x["health"] else "Unknown"] += 1
        return dict(drives=ds, kpi=dict(total=len(ds), bands=bands, offline=sum(1 for x in ds if x["link"] == "Offline"),
                                         anomalies=sum(len(x["anomaly"]["open"]) for x in ds)))

@app.get("/api/drive/{drive_id}")
def drive_detail(drive_id: str):
    with pool().connection() as c:
        ds = summarize_all(c, only=drive_id)
        if not ds: raise HTTPException(404, "unknown drive")
        d = ds[0]
        d["trend"]["detail"] = _trend_cache.get(drive_id, (0, {"detail": {}}))[1]["detail"]
        d["comms"] = comms_for(c, drive_id, 60)
        d["edge"] = c.execute("select boot_id, uptime_s, buffer_depth, failures, last_upload_ms, "
                              "extract(epoch from now() - updated_at)::float8 as age_s from drive_status where drive_id=%s", (drive_id,)).fetchone()
        d["learning"] = dict(warmup_s=WARMUP_S, learn_s=LEARN_S)
        return d

def comms_for(c, d, minutes):
    minutes = max(1, min(minutes, 360))
    rows = c.execute("select extract(epoch from ts)::float8 as ts, extract(epoch from received_at)::float8 as rec from readings "
                     "where drive_id=%s and ts > now() - make_interval(mins => %s) order by ts", (d, minutes)).fetchall()
    ref = c.execute("select extract(epoch from now())::float8 as now, extract(epoch from commissioned_at)::float8 as t0 "
                    "from drives where drive_id=%s", (d,)).fetchone()
    m = comms.link_metrics([(r["ts"], r["rec"]) for r in rows], ref["now"], minutes * 60, ref["t0"] or 0)
    al = c.execute("select avg(extract(epoch from received_at - ts))::float8 as mean, max(extract(epoch from received_at - ts))::float8 as mx, "
                   "count(*) as n from events where drive_id=%s and severity in ('WARNING','CRITICAL') "
                   "and ts > now() - make_interval(mins => %s)", (d, minutes)).fetchone()
    m["alert_latency"] = dict(mean_s=round(al["mean"], 2) if al["mean"] is not None else None,
                              max_s=round(al["mx"], 2) if al["mx"] is not None else None, n=al["n"])
    return m

@app.get("/api/comms")
def comms_api(drive_id: str, minutes: int = 60):
    with pool().connection() as c: return comms_for(c, drive_id, minutes)

@app.get("/api/coverage")
def coverage(drive_id: str, start: float, end: float):
    """How many readings exist for a time range and when the last of them arrived (outage-recovery evaluation)."""
    with pool().connection() as c:
        r = c.execute("select count(*) as n, extract(epoch from max(received_at))::float8 as max_rec from readings "
                      "where drive_id=%s and ts between to_timestamp(%s) and to_timestamp(%s)", (drive_id, start, end)).fetchone()
        return dict(received=r["n"], expected=int(end - start), max_received_at=r["max_rec"])

@app.get("/api/history")
def history(drive_id: str, minutes: int = 10):
    minutes = max(1, min(minutes, 10080))
    table = "readings" if minutes <= RAW_KEEP_H * 60 else "readings_1m"
    bucket = max(1, minutes * 60 // 300)
    with pool().connection() as c:
        return c.execute(
            f"select floor(extract(epoch from ts) / %s)::float8 * %s as ts, avg(current) current, avg(power) power, "
            f"avg(heatsink) heatsink, avg(motor_temp) motor_temp, avg(vib_rms) vib_rms from {table} "
            "where drive_id=%s and ts > now() - make_interval(mins => %s) group by 1 order by 1",
            (bucket, bucket, drive_id, minutes)).fetchall()

@app.get("/api/events")
def events(drive_id: str = "", limit: int = 40, since: float = 0):
    with pool().connection() as c:
        return c.execute("select drive_id, extract(epoch from ts)::float8 as ts, extract(epoch from received_at)::float8 as rec, "
                         "severity, signal, value, msg from events where (%s = '' or drive_id = %s) and ts > to_timestamp(%s) "
                         "order by ts desc limit %s", (drive_id, drive_id, since, min(limit, 2000))).fetchall()

@app.get("/api/config")
def config():
    return {"defaults": health.DEFAULTS, "overload_ratio": health.OVERLOAD, "warmup_s": WARMUP_S, "learn_s": LEARN_S}

# ---------------------------------------------------------------- admin
class DriveEdit(BaseModel):
    name: Optional[str] = None; rated_a: Optional[float] = None; rated_kw: Optional[float] = None; limits: Optional[dict] = None

@app.put("/api/admin/drives/{drive_id}")
def edit_drive(drive_id: str, b: DriveEdit, x_api_key: str = Header(default="")):
    check_key(x_api_key)
    with pool().connection() as c:
        c.execute("update drives set name=coalesce(%s,name), rated_a=coalesce(%s,rated_a), rated_kw=coalesce(%s,rated_kw), "
                  "limits=coalesce(%s,limits) where drive_id=%s", (b.name, b.rated_a, b.rated_kw, Jsonb(b.limits) if b.limits else None, drive_id))
    return {"ok": True}

@app.post("/api/admin/drives/{drive_id}/relearn")
def relearn(drive_id: str, x_api_key: str = Header(default="")):
    """Technician action: 'treat the next warm-up + learning period as normal' (after repair or a change in duty)."""
    check_key(x_api_key)
    with pool().connection() as c:
        c.execute("delete from anomaly_models where drive_id=%s", (drive_id,))
        c.execute("update drives set commissioned_at=now() where drive_id=%s", (drive_id,))
    _trend_cache.pop(drive_id, None)
    return {"ok": True}

@app.post("/api/admin/rollup")
def admin_rollup(x_api_key: str = Header(default="")):
    check_key(x_api_key); rollup(); return {"ok": True}

app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")

@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))
