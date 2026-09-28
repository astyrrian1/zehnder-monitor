"""Read-only legacy Zehnder entity/history snapshot for upgrade comparison."""
from datetime import datetime, timedelta, timezone
import argparse
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request

LEGACY_ENTITIES = (
    'sensor.zehnder_monitor_zehnder_sfp',
    'sensor.zehnder_monitor_zehnder_filter_health',
    'sensor.zehnder_monitor_zehnder_duty_ratio',
    'sensor.zehnder_monitor_zehnder_heat_recovery',
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compare', type=Path)
    args = parser.parse_args()
    base = os.environ['ZMON_OBSERVE_HA_URL'].rstrip('/')
    token = os.environ['ZMON_OBSERVE_HA_TOKEN']

    def get(route):
        req = urllib.request.Request(base + route,
            headers={'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)

    states = {}
    for entity in LEGACY_ENTITIES:
        record = get('/api/states/' + entity)
        states[entity] = {'state': record['state'],
                          'unit': (record.get('attributes') or {}).get('unit_of_measurement')}
    if args.compare:
        previous = json.loads(args.compare.read_text())
        start = previous['history_start']
    else:
        start = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    end = datetime.now(timezone.utc).isoformat()
    history = {}
    for entity in LEGACY_ENTITIES:
        query = urllib.parse.urlencode({'filter_entity_id': entity, 'end_time': end})
        series = get('/api/history/period/' + urllib.parse.quote(start) + '?' + query)
        history[entity] = [{'last_changed': item['last_changed'], 'state': item['state']}
                           for group in series for item in group]
    result = {'captured_at': end, 'history_start': start, 'states': states,
              'history': history}
    args.output.write_text(json.dumps(result, indent=2))
    if args.compare:
        for entity in LEGACY_ENTITIES:
            before = {(item['last_changed'], item['state'])
                      for item in previous['history'][entity]}
            after = {(item['last_changed'], item['state'])
                     for item in history[entity]}
            if not before <= after:
                raise AssertionError(f'Legacy history missing for {entity}: {len(before - after)}')
        print(json.dumps({'result': 'pass', 'legacy_entities': len(states),
                          'historical_records_preserved': sum(len(x) for x in previous['history'].values())}))
    else:
        print(json.dumps({'result': 'captured', 'legacy_entities': len(states),
                          'historical_records': sum(len(x) for x in history.values())}))


if __name__ == '__main__':
    main()
