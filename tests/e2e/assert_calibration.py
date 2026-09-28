"""Assert corrected calibration is pending while absolute SFP still works."""

import json
import os
import time
from datetime import datetime
import argparse
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--after", help="Require a new monitor publication after this timestamp")
    args = parser.parse_args()
    base = os.environ["ZMON_TEST_HA_URL"].rstrip("/")
    if not base.endswith(":18123"):
        raise SystemExit("Refusing to inspect outside the isolated port 18123")
    token = os.environ["ZMON_TEST_HA_TOKEN"]

    def state(entity_id):
        request = urllib.request.Request(
            base + "/api/states/" + entity_id,
            headers={"Authorization": "Bearer " + token},
        )
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    deadline = time.monotonic() + 130
    last = None
    while time.monotonic() < deadline:
        try:
            calibration = state("sensor.zehnder_corrected_calibration")
            comparison = state("sensor.zehnder_corrected_sfp_change")
            sfp = state("sensor.zehnder_corrected_sfp")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            time.sleep(1)
            continue
        last = (calibration["state"], comparison["state"], sfp["state"])
        calculated_at = calibration["attributes"].get("calculated_at")
        is_new = not args.after or (calculated_at and datetime.fromisoformat(calculated_at) > datetime.fromisoformat(args.after))
        if (calibration["state"] == "awaiting_confirmation"
                and is_new
                and calibration["attributes"].get("references") == {}
                and calibration["attributes"].get("cycle_id") is None
                and comparison["state"] in ("unavailable", "unknown")
                and sfp["state"] not in ("unavailable", "unknown")):
            print(json.dumps({"calibration": calibration["state"], "references": calibration["attributes"]["references"], "comparison": comparison["state"], "sfp": sfp["state"]}))
            return
        time.sleep(1)
    raise AssertionError({"expected": "awaiting_confirmation/no_reference", "last": last})


if __name__ == "__main__":
    main()
