# Danfoss Drive Monitoring - simulated end-to-end demo

edge/ (laptop now, Raspberry Pi later) -> HTTPS -> cloud/ (FastAPI on Render) -> Supabase Postgres -> dashboard at "/"
All data is SIMULATED. Limits are illustrative, not validated.

## Test the logic (no accounts needed)
    cd cloud && pip install -r requirements.txt && python test_health.py
    cd ../edge && pip install -r requirements.txt && python checks.py

## Deploy
1. Push this folder to a GitHub repo.
2. Supabase: create a project -> Connect -> copy the *pooler* connection string (IPv4-compatible), insert your DB password.
3. Render: New -> Blueprint (uses render.yaml) -> set DATABASE_URL to the Supabase string. API_KEY is generated; copy it.
4. Open https://<your-service>.onrender.com/healthz (first load after idle can take up to a minute).
5. Edge: in edge/config.yaml set cloud_url (Render URL) and api_key, then run `python main.py --overload` or `--fault-after 60`.
6. Open the Render URL to see the dashboard.

## Before a demo
Start the edge 5-10 min earlier (keeps Render awake, fills the charts) and check the Supabase project is not paused.
