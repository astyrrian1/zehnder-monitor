# Recovery evidence repair — 10 October 2026

Recovery arithmetic was correct, but the publisher counted airflow changes as
new temperature evidence and reported the newest input's age. In the production
review, a temperature set whose oldest report was 322.6 seconds old appeared as
0.1 seconds old. Five airflow-only updates could qualify the conditioned median.

The corrected publisher dates the raw temperature snapshot by its oldest
supply/outdoor/extract report. Each new conditioned observation requires all
three temperature report times to advance beyond the last accepted set. Sensor
values may remain unchanged; genuine report times, not value changes, establish
new evidence. Airflow and bypass remain eligibility gates but cannot manufacture
thermal observations. Asynchronous temperature reports are accepted once all
three have advanced. Samples age out using their oldest input timestamp.

`age_seconds` describes the current validated temperature snapshot, or null if
that snapshot is unavailable. `temperature_reported_at` exposes all three source
report times. `conditioned_age_seconds` and `conditioned_last_reported_at` describe
the newest accepted historical sample independently. For dashboard compatibility,
`last_reported_at` is the current snapshot time while current, and the latest
accepted sample time when historical. The existing historical dashboard wording
therefore remains accurate without a HA YAML change. Production window remains
900 seconds; isolated test metadata now correctly reports its 30-second window.

Recovery can legitimately remain warming up longer on infrequently reporting
temperature sensors. No timestamp is synthesized to make it appear ready. SFP,
filter references, maintenance confirmation, and legacy history are unaffected.
The in-memory recovery sample buffer starts empty after the app reload.

## Validation

- Test-first commits reproduce age 0 versus expected 300 seconds, five airflow
  samples versus one thermal set, asynchronous overcounting, and conflated ages.
- The isolated HA → AppDaemon → MQTT → HA regression failed before the fix with
  `Airflow-only reports inflated temperature evidence`.
- All 79 unit tests pass after the repair.
- `run_m7.py` covers heating/cooling/Fahrenheit 80%, historical medians, bypass,
  stale temperature, 120% anomaly, and independent SFP availability.
- `run_recovery_evidence.py` verifies rejected airflow-only reports, accurate age,
  genuine unchanged-value thermal reports reaching 80%, and unchanged SFP.
- Three successive live publications passed source-age and SFP arithmetic checks;
  recovery age advanced while thermal sample count remained one. All 153 captured
  legacy history records across four entities remained present. Calibration still
  awaits confirmation. Browser visual re-verification awaits test-account sign-in.
- This focused repair does not claim a new 24-hour production observation or
  independent calibration of physical temperature, airflow, or power sensors.

## Deployment and rollback

Only `corrected.py` and `zehnder_monitor.py` are deployed under
`/homeassistant/appdaemon/apps/zehnder-monitor/`. AppDaemon reloads the app;
Home Assistant does not restart. Backups of both files and corrected persistence
are in `/homeassistant/appdaemon_backups/recovery-20261010/` on the HA host.
Rollback restores only the two Python files from that directory. Preserve current
persistence and recorder data; neither requires migration. Scheduled observation
remains stopped. No production source simulation or clean-filter confirmation is
part of deployment.

## Remeasurement after maintenance

After both filter paths actually contain clean filters, use the dashboard's
clean-filter confirmation. Do not confirm beforehand. The existing calibration
then settles for two hours and collects for 72 hours. A reference needs at least
20 eligible distinct snapshots spanning 30 minutes at a stable Low/Medium
operating point; unvisited points remain unavailable. This repair does not require
or prove that filters need replacement.
