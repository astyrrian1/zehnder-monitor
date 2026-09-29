"""Milestone 2 isolated quality, expiry, restart, and recovery journey.

Requires ZMON_TEST_HA_URL, ZMON_TEST_TOKEN_FILE and ZMON_TEST_STACK_SSH.
The SSH target is only used with the named isolated Compose project.
"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse
import urllib.request


HERE = Path(__file__).resolve().parent
STACK = "/opt/stacks/zehnder-monitor-e2e"


def main():
    base = os.environ["ZMON_TEST_HA_URL"].rstrip("/")
    if not base.endswith(":18123"):
        raise SystemExit("Refusing to test outside the isolated port 18123")
    ssh_host = os.environ["ZMON_TEST_STACK_SSH"]
    token_file = Path(os.environ["ZMON_TEST_TOKEN_FILE"])
    env = dict(os.environ)

    def refresh():
        tokens = json.loads(token_file.read_text())
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": base + "/",
        }).encode()
        with urllib.request.urlopen(urllib.request.Request(base + "/auth/token", data=body)) as response:
            tokens.update(json.load(response))
        token_file.write_text(json.dumps(tokens))
        env["ZMON_TEST_HA_TOKEN"] = tokens["access_token"]
        return tokens["access_token"]

    def run(script, *arguments):
        subprocess.run([sys.executable, str(HERE / script), *map(str, arguments)], env=env, check=True)

    def state(entity):
        request = urllib.request.Request(
            base + "/api/states/" + entity,
            headers={"Authorization": "Bearer " + env["ZMON_TEST_HA_TOKEN"]},
        )
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def quality():
        return state("sensor.zehnder_corrected_sfp_quality")

    def inject_and_assert(key, value, unit, expected, *extra):
        after = datetime.now(timezone.utc).isoformat()
        run("set_source.py", key, value, unit, *extra)
        run("assert_quality.py", expected, "--after", after, "--timeout", "65")

    def stack_command(command):
        # The command operates on the test-only Compose project, never the HA host.
        subprocess.run(["ssh", ssh_host, f"cd {STACK} && docker compose {command}"], check=True)

    def update_publisher_token(token):
        subprocess.run(
            ["ssh", ssh_host, "sh -c 'cat > /opt/stacks/zehnder-monitor-e2e/appdaemon/secrets.yaml'"],
            input=("ha_token: " + token + "\n").encode(), check=True,
        )

    token = refresh()
    run("seed.py", 72)
    update_publisher_token(token)
    stack_command("restart appdaemon")
    run("assert_quality.py", "current", "--timeout", "130")
    inject_and_assert("power", "NaN", "W", "invalid")
    inject_and_assert("power", "inf", "W", "invalid")
    inject_and_assert("power", "-1", "W", "invalid")
    inject_and_assert("power", "72", "horsepower", "unsupported")
    old = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    inject_and_assert("power", "72", "W", "stale", "--reported-at", old)
    inject_and_assert("power", "72", "W", "unknown_freshness", "--reported-at", "unknown")
    inject_and_assert("power", "unavailable", "W", "unavailable")
    after = datetime.now(timezone.utc).isoformat()
    run("set_source.py", "power", "72", "W")
    run("set_source.py", "supply_flow", "0", "m³/h")
    run("assert_quality.py", "stopped", "--after", after, "--timeout", "65")
    run("seed.py", 72)
    run("assert_quality.py", "current", "--timeout", "65")

    before = quality()["attributes"].get("accepted_reports")
    after = datetime.now(timezone.utc).isoformat()
    run("set_source.py", "supply_rpm", "unavailable", "rpm")
    run("assert_quality.py", "current", "--fan-effort", "unavailable", "--after", after)
    assert quality()["attributes"]["accepted_reports"] == before
    after = datetime.now(timezone.utc).isoformat()
    run("set_source.py", "bypass", "unknown", "%")
    run("assert_quality.py", "current", "--recovery", "unsupported", "--eligible", "false", "--after", after)
    before = quality()["attributes"]["accepted_reports"]
    after = datetime.now(timezone.utc).isoformat()
    run("seed.py", 72)
    run("assert_quality.py", "current", "--fan-effort", "current", "--recovery", "current", "--after", after)
    assert quality()["attributes"]["accepted_reports"] == before + 1

    stack_command("stop appdaemon")
    started = time.monotonic()
    run("assert_quality.py", "unavailable", "--timeout", "185")
    assert time.monotonic() - started <= 185
    stack_command("restart ha")
    for _ in range(45):
        try:
            current = state("sensor.zehnder_corrected_sfp")["state"]
            assert current in ("unknown", "unavailable"), current
            break
        except (OSError, TimeoutError):
            time.sleep(2)
    else:
        raise AssertionError("HA did not restart")
    # REST-created test entities must exist before AppDaemon takes its snapshot.
    run("seed.py", 72)
    token = refresh()
    update_publisher_token(token)
    stack_command("--profile publisher up -d appdaemon")
    run("assert_quality.py", "current", "--timeout", "130")
    print("Milestone 2 isolated HA/MQTT/AppDaemon API journey passed")


if __name__ == "__main__":
    main()
