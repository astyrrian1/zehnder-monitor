# Milestone 6 observed result (isolated stack, 2026-09-28 UTC)

The focused trend tests failed before UTC hourly aggregation existed, then
passed. A clean-start `run_m6.py` passed through simulated HA source reports,
AppDaemon, MQTT, HA trend entities, and the rendered dashboard chart. The
positive fixture reported **10.0 W/(m³/s)/day** from 26 nonempty UTC hourly
medians spanning 108 hours; negative and flat fixtures reported **−10.0** and
**0.0**. Sparse coverage remained unavailable. Confirming a new maintenance
cycle immediately withdrew the old trend. The browser screenshot in the
associated Codex task shows the numeric slope, coverage explanation, unit,
and chart.

The regression uses elapsed UTC time and weights hourly medians equally,
independent of the number of reports per hour. It requires at least 24
nonempty buckets in seven days spanning at least 72 hours, and selects only
reports from the current cycle and operating point. The isolated replay uses
an HA test-clock entity; the production publisher ignores it and runs at the
real 60-second cadence. No physical ventilation command or external household
message was sent.
