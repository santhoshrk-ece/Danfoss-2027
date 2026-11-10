"""Buffer + upload. Readings stay buffered until the cloud accepts them; the buffer is saved to disk on failure.
Every upload also carries edge health (buffer depth, failures, uptime) for the cloud's reliability panel."""
import collections, itertools, json, os, time, uuid, requests

class Uploader:
    def __init__(self, url, key, drive, simulated, every_s, buffer_file):
        self.url, self.key, self.drive, self.simulated = url.rstrip("/"), key, drive, simulated
        self.every, self.file = every_s, buffer_file
        self.readings, self.events = collections.deque(maxlen=50000), []
        self.last, self.urgent, self.failures, self.last_ms = 0.0, False, 0, None
        self.boot_id, self.started = uuid.uuid4().hex[:8], time.time()
        self._load()

    def add(self, reading, events):
        self.readings.append(reading); self.events += events
        if events: self.urgent = True            # alarms don't wait for the next batch

    def due(self, now):
        return self.urgent or now - self.last >= self.every

    def _body(self, chunk):
        d = self.drive
        return dict(drive_id=d["id"], name=d.get("name"), model=d.get("model", "FC-301 P4K0"),
                    rated_a=d.get("rated_a"), rated_kw=d.get("rated_kw"), simulated=self.simulated,
                    readings=chunk, events=self.events,
                    edge=dict(boot_id=self.boot_id, uptime_s=round(time.time() - self.started, 1),
                              buffer_depth=len(self.readings), failures=self.failures,
                              last_upload_ms=self.last_ms))

    def flush(self, now, offline=False):
        self.last, self.urgent = now, False
        if offline: self._save(); return False
        while self.readings or self.events:
            chunk = list(itertools.islice(self.readings, 500))
            t = time.time()
            try:
                requests.post(self.url + "/api/ingest", json=self._body(chunk), headers={"X-API-Key": self.key},
                              timeout=8).raise_for_status()
            except requests.RequestException:
                self.failures += 1; self._save(); return False
            self.last_ms = round((time.time() - t) * 1000)
            for _ in chunk: self.readings.popleft()
            self.events = []
        if os.path.exists(self.file): os.remove(self.file)
        return True

    def _save(self):
        tmp = self.file + ".tmp"
        with open(tmp, "w") as f:
            for r in self.readings: f.write(json.dumps({"k": "r", "v": r}) + "\n")
            for e in self.events: f.write(json.dumps({"k": "e", "v": e}) + "\n")
        os.replace(tmp, self.file)

    def _load(self):
        if not os.path.exists(self.file): return
        for line in open(self.file):
            o = json.loads(line)
            (self.readings if o["k"] == "r" else self.events).append(o["v"])
