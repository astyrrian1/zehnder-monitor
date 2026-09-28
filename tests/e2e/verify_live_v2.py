"""Read-only corrected publisher acceptance against live HA source values."""
from datetime import datetime, timezone
import json
import math
import os
import time
import urllib.error
import urllib.request


def main():
    base = os.environ['ZMON_OBSERVE_HA_URL'].rstrip('/')
    if base.endswith(':18123'):
        raise SystemExit('Use isolated milestone tests for the test stack')
    token = os.environ['ZMON_OBSERVE_HA_TOKEN']

    def state(entity):
        req = urllib.request.Request(base + '/api/states/' + entity,
            headers={'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)

    deadline = time.monotonic() + 130
    while True:
        try:
            quality = state('sensor.zehnder_corrected_sfp_quality')
            sfp = state('sensor.zehnder_corrected_sfp')
            calibration = state('sensor.zehnder_corrected_calibration')
            if quality['state'] == 'current' and sfp['state'] not in ('unknown', 'unavailable'):
                break
        except (OSError, urllib.error.HTTPError):
            pass
        if time.monotonic() >= deadline:
            raise AssertionError('Corrected live SFP did not become current within 130 seconds')
        time.sleep(2)
    assert sfp['attributes']['unit_of_measurement'] == 'kW/(m³/s)'
    inputs = sfp['attributes']['inputs']
    watts = float(inputs['power']['value'])
    supply = float(inputs['supply_flow']['value'])
    exhaust = float(inputs['exhaust_flow']['value'])
    expected = watts * 3.6 / ((supply + exhaust) / 2)
    actual = float(sfp['state'])
    assert all(math.isfinite(value) for value in (watts, supply, exhaust, expected, actual))
    error = abs(actual - expected)
    assert error <= .00005, (actual, expected)
    calculated = datetime.fromisoformat(sfp['attributes']['calculated_at'])
    assert (datetime.now(timezone.utc) - calculated).total_seconds() <= 125
    for key in ('power', 'supply_flow', 'exhaust_flow'):
        assert inputs[key]['reported_at']
    assert calibration['state'] == 'awaiting_confirmation', calibration['state']
    print(json.dumps({'result': 'pass', 'watts': watts, 'supply_m3h': supply,
                      'exhaust_m3h': exhaust, 'corrected_sfp': actual,
                      'expected_sfp': round(expected, 6),
                      'software_error': round(error, 7),
                      'calibration': calibration['state']}))


if __name__ == '__main__':
    main()
