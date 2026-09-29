"""Milestone 3 fresh-install and legacy-upgrade journeys in the test stack."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request


HERE = Path(__file__).resolve().parent
STACK = "/opt/stacks/zehnder-monitor-e2e"


def main():
    base = os.environ["ZMON_TEST_HA_URL"].rstrip("/")
    if not base.endswith(":18123"):
        raise SystemExit("Refusing to test outside the isolated port 18123")
    host = os.environ["ZMON_TEST_STACK_SSH"]
    token = os.environ["ZMON_TEST_HA_TOKEN"]

    def stack(command):
        subprocess.run(["ssh", host, f"cd {STACK} && docker compose {command}"], check=True)

    def remote_python(code):
        subprocess.run(["ssh", host, "python3 -"], input=code.encode(), check=True)

    def run(script, *args):
        subprocess.run([sys.executable, str(HERE / script), *args], check=True)

    def assert_files(expect_legacy):
        remote_python(f'''
import json,pathlib
base=pathlib.Path("{STACK}/appdaemon/zehnder-monitor")
corrected=json.loads((base/"corrected_v2.json").read_text())
assert corrected["schema_version"] == 2
assert corrected["calibration"]["state"] == "awaiting_confirmation"
assert corrected["calibration"]["references"] == {{}}
assert (base/"baselines.json").exists() is {expect_legacy}
print("persistence", "legacy-upgrade" if {expect_legacy} else "fresh", "corrected references=0")
''')

    for mode in ("fresh", "legacy-upgrade"):
        stack("stop appdaemon")
        legacy = mode == "legacy-upgrade"
        remote_python(f'''
import json,pathlib
base=pathlib.Path("{STACK}/appdaemon/zehnder-monitor")
base.mkdir(parents=True,exist_ok=True)
for name in ("corrected_v2.json","baselines.json","state.json"):
    (base/name).unlink(missing_ok=True)
if {legacy}:
    (base/"baselines.json").write_text(json.dumps({{"sfp":0.5575,"duty_ratio":1.231,"supply_duty":68.5,"exhaust_duty":55.7,"supply_rpm_per_flow":8.024,"fan_level_at_capture":"Medium","captured_at":"2026-05-18T12:36:53.461672","filter_days_at_capture":180.0}}))
''')
        before = datetime.now(timezone.utc).isoformat()
        run("seed.py", "72")
        stack("--profile publisher up -d appdaemon")
        run("assert_calibration.py", "--after", before)
        assert_files(legacy)

    request = urllib.request.Request(
        base + "/api/states",
        headers={"Authorization": "Bearer " + token},
    )
    with urllib.request.urlopen(request) as response:
        states = json.load(response)
    corrected_life = [item["entity_id"] for item in states if item["entity_id"].startswith("sensor.zehnder_corrected_") and any(term in item["entity_id"] for term in ("capacity", "remaining_life", "replacement"))]
    assert corrected_life == [], corrected_life
    print("No corrected capacity, remaining-life, or replacement entity before confirmation")
    print("Milestone 3 fresh-install and legacy-upgrade journeys passed")


if __name__ == "__main__":
    main()
