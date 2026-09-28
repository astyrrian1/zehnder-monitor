"""Assert a v2 quality transition and corresponding HA availability."""

import argparse
from datetime import datetime
import json
import os
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("quality")
    parser.add_argument("--timeout", type=float, default=65)
    parser.add_argument("--after", help="Require HA to report the quality after this ISO timestamp")
    parser.add_argument("--fan-effort")
    parser.add_argument("--recovery")
    parser.add_argument("--eligible", choices=("true", "false"))
    args = parser.parse_args()
    base = os.environ["ZMON_TEST_HA_URL"].rstrip("/")
    if not base.endswith(":18123"):
        raise SystemExit("Refusing to test outside the isolated port 18123")
    token = os.environ["ZMON_TEST_HA_TOKEN"]
    deadline = time.monotonic() + args.timeout
    last = None
    while time.monotonic() <= deadline:
        def get(entity):
            request = urllib.request.Request(
                base + "/api/states/" + entity,
                headers={"Authorization": "Bearer " + token},
            )
            with urllib.request.urlopen(request) as response:
                return json.load(response)
        try:
            quality = get("sensor.zehnder_corrected_sfp_quality")
            reading = get("sensor.zehnder_corrected_sfp")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            time.sleep(1)
            continue
        last = (quality["state"], reading["state"])
        evidence_time = quality["attributes"].get("calculated_at")
        reported_after = not args.after or (
            evidence_time is not None
            and datetime.fromisoformat(evidence_time) > datetime.fromisoformat(args.after)
        )
        optional_matches = (
            (args.fan_effort is None or quality["attributes"].get("fan_effort_quality") == args.fan_effort)
            and (args.recovery is None or quality["attributes"].get("recovery_quality") == args.recovery)
            and (args.eligible is None or quality["attributes"].get("baseline_eligible") is (args.eligible == "true"))
        )
        if quality["state"] == args.quality and reported_after and optional_matches:
            if args.quality == "current":
                assert reading["state"] not in ("unknown", "unavailable")
            else:
                assert reading["state"] in ("unknown", "unavailable")
            print(json.dumps({"quality": quality["state"], "reason": quality["attributes"].get("reason"), "sfp_state": reading["state"], "fan_effort_quality": quality["attributes"].get("fan_effort_quality"), "recovery_quality": quality["attributes"].get("recovery_quality"), "baseline_eligible": quality["attributes"].get("baseline_eligible"), "accepted_reports": quality["attributes"].get("accepted_reports")}))
            return
        time.sleep(1)
    raise AssertionError({"expected": args.quality, "last": last})


if __name__ == "__main__":
    main()
