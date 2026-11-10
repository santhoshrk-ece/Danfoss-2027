# Danfoss Drive Monitoring - simulated end-to-end demo (v3)

edge/ (one simulated gateway per drive; laptop now, Raspberry Pi later) -> HTTPS -> cloud/ (FastAPI on Render)
-> Supabase Postgres -> dashboard at "/". All data is SIMULATED. Limits are illustrative, not validated.

## What is where
| Part | File | Idea |
|---|---|---|
| Health score | cloud/health.py | 100 - limit penalty (60) - anomaly penalty (25) - trend penalty (15), with reasons |
| Anomaly detection | cloud/anomaly.py | "unusual for THIS drive at THIS load": per-load-bin robust z (vibration) + thermal model residual (temperatures); must persist |
| Trend analysis | cloud/trend.py | Theil-Sen slope + Mann-Kendall test on load-matched medians |
| Communication reliability | cloud/comms.py | completeness, latency, gaps (data lost) vs back-filled batches (outage recovered) |
| API / storage / retention | cloud/app.py, schema.sql | ingest, drive registry, 24 h raw + 1-min roll-ups |
| Edge | edge/ | sources (simulator), processing (validate + rules), uploader (buffer + edge health), main (multi-drive, network faults, ground truth) |
| Evaluation | tools/offline_eval.py, tools/evaluate.py | offline (many seeds, seconds) and live (against ground truth) |

## Test the logic (no accounts needed)
    cd cloud && pip install -r requirements.txt && python test_logic.py
    cd ../tools && python offline_eval.py --seeds 10

## Deploy (Render + Supabase, as before)
Push to GitHub; Render redeploys. The schema upgrades itself on startup (new tables, one new column).
Optional Render env vars: WARMUP_S (default 300) and LEARN_S (default 900) = commissioning periods for the anomaly baseline.

## Demo script
1. Seed history so charts, trends and the baseline exist immediately:
       cd edge && python backfill.py --hours 2          # set cloud_url / api_key in config.yaml first
2. Run the live gateways (4 drives: healthy, bearing wear, overload-prone, flaky network):
       python main.py
3. Open the Render URL. The wear drive starts degrading after 30 min (config: fault_after_s); the flaky drive
   loses its network now and then and back-fills. Use `python backfill.py --hours 2 --with-fault` for an instant fault story.
4. Evaluate: python tools/evaluate.py --api https://<app>.onrender.com --truth edge/ground_truth.jsonl

## Honest limitations
- Baseline is learned once (warm-up + learning period assumed healthy) and frozen; POST /api/admin/drives/<id>/relearn resets it. Slow adaptation is not implemented.
- Thermal time constants (90 s motor, 40 s heatsink) are assumptions; fit them from real data.
- Evaluation on simulated faults shows the pipeline works, not real-world detection performance.
- GET endpoints are public (demo); writing requires the API key.
