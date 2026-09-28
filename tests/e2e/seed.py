"""Write test-only source states to the isolated Home Assistant API."""

import argparse
import json
import os
import time
import urllib.request


PREFIX = "sensor.zehnder_comfoair_q_a4cb9c_"
MEASUREMENTS = {
    "supply_fan_flow": (350, "m³/h"),
    "exhaust_fan_flow": (350, "m³/h"),
    "supply_fan_duty": (45, "%"),
    "exhaust_fan_duty": (42, "%"),
    "supply_fan_speed": (1250, "rpm"),
    "exhaust_fan_speed": (1250, "rpm"),
    "fan_level": ("Medium", None),
    "bypass_state": (0, "%"),
    "filter_replacement_remaining_days": (90, "d"),
    "supply_air_temperature": (16, "°C"),
    "outdoor_air_temperature": (0, "°C"),
    "extract_air_temperature": (20, "°C"),
    "exhaust_air_temperature": (4, "°C"),
    "wifi_signal": (-50, "dBm"),
    "energy_ytd": (120, "kWh"),
    "avoided_heating_actual": (0, "kWh"),
    "avoided_cooling_actual": (0, "kWh"),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("power", type=float)
    parser.add_argument("--supply-duty", type=float, default=45)
    parser.add_argument("--exhaust-duty", type=float, default=42)
    parser.add_argument("--fan-level", default="Medium")
    parser.add_argument("--supply-flow", type=float, default=350)
    parser.add_argument("--exhaust-flow", type=float, default=350)
    parser.add_argument("--supply-rpm", type=float, default=1250)
    parser.add_argument("--exhaust-rpm", type=float, default=1250)
    parser.add_argument("--reported-at", help="test-only source report time for virtual-clock replay")
    args = parser.parse_args()
    base = os.environ["ZMON_TEST_HA_URL"].rstrip("/")
    if not base.endswith(":18123"):
        raise SystemExit("Refusing to seed anything outside the isolated port 18123")
    token = os.environ["ZMON_TEST_HA_TOKEN"]

    def put(entity_id, value, unit=None):
        body = {"state": str(value), "attributes": {"test_report_id": time.time_ns()}}
        if args.reported_at:
            body["attributes"]["source_reported_at"] = args.reported_at
        if unit:
            body["attributes"]["unit_of_measurement"] = unit
        request = urllib.request.Request(
            base + "/api/states/" + entity_id,
            data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            if response.status not in (200, 201):
                raise RuntimeError((entity_id, response.status))

    put("binary_sensor.zehnder_comfoair_q_a4cb9c_status", "on")
    put(PREFIX + "power", args.power, "W")
    overrides = {
        'supply_fan_flow': args.supply_flow,
        'exhaust_fan_flow': args.exhaust_flow,
        'supply_fan_duty': args.supply_duty,
        'exhaust_fan_duty': args.exhaust_duty,
        'supply_fan_speed': args.supply_rpm,
        'exhaust_fan_speed': args.exhaust_rpm,
        'fan_level': args.fan_level,
    }
    for suffix, (default, unit) in MEASUREMENTS.items():
        value = overrides.get(suffix, default)
        put(PREFIX + suffix, value, unit)
    print("Seeded isolated HA source entities, power", args.power)


if __name__ == "__main__":
    main()
