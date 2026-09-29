"""Milestone 1: simulated source -> AppDaemon -> MQTT -> HA entity."""

import os
from datetime import datetime, timezone
import subprocess
import sys
import time


def run(*args):
    script, *arguments = args
    return subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(__file__), script), *arguments],
        check=True,
    )


def main():
    for power, timeout in ((72, 130), (79.2, 65)):
        started = time.monotonic()
        before = datetime.now(timezone.utc).isoformat()
        run("seed.py", str(power))
        run("assert_sfp.py", str(power), "--timeout", str(timeout), "--after", before)
        elapsed = time.monotonic() - started
        print(f"input_to_ha_seconds={elapsed:.2f} power_w={power}")
        if power == 79.2 and elapsed > 65:
            raise AssertionError("Input-to-HA latency exceeded 65 seconds")


if __name__ == "__main__":
    main()
