"""Assert the isolated HA entity reflects the exact v2 SFP inputs."""

import argparse
import json
import math
import os
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("power", type=float)
    parser.add_argument("--timeout", type=float, default=65)
    args = parser.parse_args()
    base = os.environ["ZMON_TEST_HA_URL"].rstrip("/")
    if not base.endswith(":18123"):
        raise SystemExit("Refusing to test anything outside the isolated port 18123")
    token = os.environ["ZMON_TEST_HA_TOKEN"]
    expected = (args.power / 1000) / (350 / 3600)
    deadline = time.monotonic() + args.timeout
    last = None
    while time.monotonic() <= deadline:
        request = urllib.request.Request(
            base + "/api/states/sensor.zehnder_corrected_sfp",
            headers={"Authorization": "Bearer " + token},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                last = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
        if last and last["state"] not in ("unknown", "unavailable"):
            attributes = last["attributes"]
            inputs = attributes.get("inputs", {})
            if math.isclose(float(last["state"]), expected, abs_tol=0.00005) and inputs.get("power", {}).get("value") == args.power:
                assert inputs["power"]["unit"] == "W"
                for key in ("supply_flow", "exhaust_flow"):
                    assert inputs[key]["value"] == 350
                    assert inputs[key]["unit"] == "m³/h"
                    assert inputs[key]["reported_at"]
                assert attributes["unit_of_measurement"] == "kW/(m³/s)"
                assert attributes["calculated_at"]
                print(json.dumps({"result": "pass", "state": last["state"], "inputs": inputs, "calculated_at": attributes["calculated_at"]}))
                return
        time.sleep(1)
    raise AssertionError({"expected": expected, "last_state": last["state"] if last else None})


if __name__ == "__main__":
    main()
