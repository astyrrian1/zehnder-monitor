"""Inject one simulated observation into the isolated HA instance."""

import argparse
import json
import os
import time
import urllib.request


SUFFIXES = {
    "power": "power",
    "supply_flow": "supply_fan_flow",
    "exhaust_flow": "exhaust_fan_flow",
    "supply_rpm": "supply_fan_speed",
    "bypass": "bypass_state",
    "filter_days": "filter_replacement_remaining_days",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("key", choices=SUFFIXES)
    parser.add_argument("value")
    parser.add_argument("unit")
    parser.add_argument("--reported-at")
    args = parser.parse_args()
    base = os.environ["ZMON_TEST_HA_URL"].rstrip("/")
    if not base.endswith(":18123"):
        raise SystemExit("Refusing to inject outside the isolated port 18123")
    token = os.environ["ZMON_TEST_HA_TOKEN"]
    attributes = {"unit_of_measurement": args.unit, "test_report_id": time.time_ns()}
    if args.reported_at is not None:
        attributes["source_reported_at"] = None if args.reported_at == "unknown" else args.reported_at
    body = {"state": args.value, "attributes": attributes}
    eid = "sensor.zehnder_comfoair_q_a4cb9c_" + SUFFIXES[args.key]
    request = urllib.request.Request(
        base + "/api/states/" + eid,
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        assert response.status in (200, 201)
    print(f"Injected {args.key}={args.value} into isolated HA")


if __name__ == "__main__":
    main()
