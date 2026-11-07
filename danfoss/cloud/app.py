"""Cloud API: ingest -> store -> analyse -> serve dashboard."""
import asyncio, hmac, os, statistics, time
from contextlib import asynccontextmanager
from typing import List
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import analytics, health
from db import init_schema, pool

HERE = os.path.dirname(os.path.abspath(__file__))
API_KEY = os.getenv("API_KEY", "demo-key")
STALE_S = 20
RAW_KEEP_H = 24
SIGNALS = ["freq", "current", "power", "speed", "torque", "dc_link", "heatsink", "motor_temp", "vib_rms", "vib_peak"]

class Reading(BaseModel):
    ts: float; state: str; status: str
    freq: float; current: float; power: float; speed: float; torque: float
    dc_link: float; heatsink: float; motor_temp: float; vib_rms: float; vib_peak: float

class Event(BaseModel):
    ts: float; severity: str; signal: str; value: float; msg: str

class Batch(BaseModel):
    drive_id: str = Field(min_length=1, max_length=40)
    simulated: bool = True
    readings: List[Reading] = Field(default_factory=list, max_length=1000)
    events: List[Event] = Field(default_factory=list, max_length=200)

ROLLUP_SQL = """
insert into readings_1m (drive_id, ts, n, current, current_max, power, heatsink, motor_temp, vib_rms, vib_rms_max)
select drive_id, date_trunc('minute', ts), count(*), avg(current), max(current), avg(power),
       avg(heatsink), avg(motor_temp), avg(vib_rms), max(vib_rms)
from readings where ts < date_trunc('minute', now() - interval '5 minutes') group by 1, 2
on conflict (drive_id, ts) do update set n=excluded.n, current=excluded.current, current_max=excluded.current_max,
  power=excluded.power, heatsink=excluded.heatsink, motor_temp=excluded.motor_temp,
  vib_rms=excluded.vib_rms, vib_rms_max=excluded.vib_rms_max
"""

def rollup():
    """Retention: roll complete minutes into readings_1m, then drop raw rows older than RAW_KEEP_H."""
    with pool().connection() as c:
        c.execute(ROLLUP_SQL)
        c.execute("delete from readings where ts < now() - make_interval(hours => %s)", (RAW_KEEP_H,))

async def rollup_loop():
    while True:
        await asyncio.sleep(3600)
        try: await asyncio.to_thread(rollup)
        except Exception as e: print("rollup failed:", e)

@asynccontextmanager
async def lifespan(app):
    init_schema()
    task = asyncio.create_task(rollup_loop())
    yield
    task.cancel()

app = FastAPI(title="Drive Monitoring Cloud", lifespan=lifespan)

def check_key(k):
    if not hmac.compare_digest(k.encode(), API_KEY.encode()): raise HTTPException(401, "bad api key")

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.post("/api/ingest")
def ingest(b: Batch, x_api_key: str = Header(default="")):
    check_key(x_api_key)
    now = time.time()
    rows = [(b.drive_id, r.ts, b.simulated, r.state, r.status, *[getattr(r, s) for s in SIGNALS])
            for r in b.readings if abs(r.ts - now) < 7 * 86400]          # drop absurd timestamps
    evs = [(b.drive_id, e.ts, e.severity, e.signal, e.value, e.msg) for e in b.events if abs(e.ts - now) < 7 * 86400]
    with pool().connection() as c, c.cursor() as cur:
        if rows:
            cur.executemany(f"insert into readings (drive_id, ts, simulated, state, status, {', '.join(SIGNALS)}) "
                            f"values (%s, to_timestamp(%s), %s, %s, %s, {', '.join(['%s'] * len(SIGNALS))}) "
                            "on conflict do nothing", rows)
        if evs:
            cur.executemany("insert into events (drive_id, ts, severity, signal, value, msg) "
                            "values (%s, to_timestamp(%s), %s, %s, %s, %s) on conflict do nothing", evs)
    return {"ok": True, "readings": len(rows), "events": len(evs)}

def ensure_baseline(c, d):
    """Learn 'normal' from the first 300 run-state readings of a drive (freeze it; don't keep re-learning)."""
    if c.execute("select 1 from baselines where drive_id=%s limit 1", (d,)).fetchone(): return
    rows = c.execute("select current, vib_rms from readings where drive_id=%s and state='run' "
                     "order by ts limit 300", (d,)).fetchall()
    if len(rows) < 300: return
    for s in ("current", "vib_rms"):
        m, sd = analytics.baseline_stats([r[s] for r in rows])
        c.execute("insert into baselines values (%s,%s,%s,%s,%s) on conflict do nothing", (d, s, m, sd, len(rows)))

def summarize(c, d):
    rows = c.execute("select *, extract(epoch from ts)::float8 as t from readings where drive_id=%s "
                     "order by ts desc limit 30", (d,)).fetchall()
    age = c.execute("select extract(epoch from now() - max(received_at))::float8 as a from readings "
                    "where drive_id=%s", (d,)).fetchone()["a"]
    last = rows[0]; stale = age > STALE_S
    # smooth: median of the last ~30 s (run-only signals use run-state samples only)
    med = {}
    for s in health.WEIGHTS:
        vals = [r[s] for r in rows if r[s] is not None and (s not in health.RUN_ONLY or r["state"] == "run")]
        if vals: med[s] = statistics.median(vals)
    ys = [r["vib_rms"] for r in c.execute("select vib_rms from readings where drive_id=%s and state='run' "
          "and ts > now() - interval '30 minutes' order by ts", (d,)).fetchall()]
    trend = analytics.vib_trend(ys)
    h = None if stale else health.compute(med, last["state"], trend["penalty"])
    ensure_baseline(c, d)
    z = {r["signal"]: round(analytics.zscore(med[r["signal"]], r["mean"], r["std"]), 1)
         for r in c.execute("select * from baselines where drive_id=%s", (d,)).fetchall()
         if r["signal"] in med and last["state"] == "run"}
    avg = c.execute("select avg(current) a, avg(vib_rms) v from readings where drive_id=%s and state='run' "
                    "and ts > now() - interval '10 minutes'", (d,)).fetchone()
    last = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in last.items()
            if k not in ("received_at", "ts")}
    return dict(drive_id=d, last=last, age_s=round(age, 1), stale=stale, health=h, trend=trend,
                anomaly=z, avg_current=avg["a"], avg_vib=avg["v"])

@app.get("/api/drives")
def drives():
    with pool().connection() as c:
        ids = [r["drive_id"] for r in c.execute("select distinct drive_id from readings order by 1").fetchall()]
        return [summarize(c, d) for d in ids]

@app.get("/api/history")
def history(drive_id: str, minutes: int = 10):
    minutes = max(1, min(minutes, 10080))
    table = "readings" if minutes <= RAW_KEEP_H * 60 else "readings_1m"
    bucket = max(1, minutes * 60 // 300)                      # ~300 points per chart
    with pool().connection() as c:
        return c.execute(
            f"select floor(extract(epoch from ts) / %s)::float8 * %s as ts, avg(current) current, avg(power) power, "
            f"avg(heatsink) heatsink, avg(motor_temp) motor_temp, avg(vib_rms) vib_rms from {table} "
            "where drive_id=%s and ts > now() - make_interval(mins => %s) group by 1 order by 1",
            (bucket, bucket, drive_id, minutes)).fetchall()

@app.get("/api/events")
def events(drive_id: str = "", limit: int = 30):
    with pool().connection() as c:
        return c.execute("select drive_id, extract(epoch from ts)::float8 as ts, severity, signal, value, msg "
                         "from events where (%s = '' or drive_id = %s) order by ts desc limit %s",
                         (drive_id, drive_id, min(limit, 200))).fetchall()

@app.get("/api/config")
def config():
    return {"limits": health.LIMITS, "weights": health.WEIGHTS}

@app.post("/api/admin/rollup")
def admin_rollup(x_api_key: str = Header(default="")):
    check_key(x_api_key); rollup(); return {"ok": True}

app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")

@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))
