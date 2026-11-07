"""Buffer + upload. Readings stay buffered until the cloud accepts them; the buffer is saved to disk on failure."""
import collections, itertools, json, os, requests

class Uploader:
    def __init__(self, url, key, drive_id, simulated, every_s, buffer_file):
        self.url, self.key, self.drive_id, self.simulated = url.rstrip("/"), key, drive_id, simulated
        self.every, self.file = every_s, buffer_file
        self.readings, self.events = collections.deque(maxlen=50000), []
        self.last, self.urgent = 0.0, False
        self._load()

    def add(self, reading, events):
        self.readings.append(reading); self.events += events
        if events: self.urgent = True            # alarms don't wait for the next batch

    def due(self, now):
        return self.urgent or now - self.last >= self.every

    def flush(self, now, offline=False):
        self.last, self.urgent = now, False
        if offline: self._save(); return False
        while self.readings or self.events:
            chunk = list(itertools.islice(self.readings, 500))
            body = dict(drive_id=self.drive_id, simulated=self.simulated, readings=chunk, events=self.events)
            try:
                requests.post(self.url + "/api/ingest", json=body, headers={"X-API-Key": self.key},
                              timeout=5).raise_for_status()
            except requests.RequestException:
                self._save(); return False
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
