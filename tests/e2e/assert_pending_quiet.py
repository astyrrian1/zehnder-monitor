"""Hold pending calibration through the monitor-fault delay without an alert."""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=601)
    args = parser.parse_args()
    base = os.environ["ZMON_TEST_HA_URL"].rstrip("/")
    if not base.endswith(":18123"):
        raise SystemExit("Refusing to inspect outside the isolated port 18123")
    token = os.environ["ZMON_TEST_HA_TOKEN"]

    def get(path):
        request = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    deadline = time.monotonic() + args.seconds
    checks = 0
    while True:
        subprocess.run([sys.executable, str(HERE / "seed.py"), "72"], check=True)
        calibration = get("/api/states/sensor.zehnder_corrected_calibration")
        comparison = get("/api/states/sensor.zehnder_corrected_sfp_change")
        states = get("/api/states")
        zehnder_notices = [
            item["entity_id"] for item in states
            if item["entity_id"].startswith("persistent_notification.zehnder")
        ]
        assert calibration["state"] == "awaiting_confirmation", calibration["state"]
        assert calibration["attributes"].get("references") == {}
        assert comparison["state"] in ("unavailable", "unknown")
        assert not zehnder_notices, zehnder_notices
        checks += 1
        if time.monotonic() >= deadline:
            print(json.dumps({"seconds": args.seconds, "checks": checks, "calibration": calibration["state"], "notices": zehnder_notices}))
            return
        time.sleep(min(60, max(0, deadline - time.monotonic())))


if __name__ == "__main__":
    main()
