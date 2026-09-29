# Milestone 5 observed result (isolated stack, 2026-09-28 UTC)

The comparison tests failed before reference selection and conditioned
comparison existed, then passed. The unit suite passed (55 tests). Reference
selection stays inside the current confirmed cycle and fan level, checks both
flows within ±5%, and searches neighboring 25 m³/h index bands. Selection is
deterministic and uses one reference with no blending or extrapolation.

`run_m5.py` passed through simulated source states, AppDaemon, MQTT, HA
entities, and the rendered dashboard. Its clean reference at Medium/350 m³/h
produced +10.0% SFP when power rose from 72 to 79.2 W at fixed airflow,
+2.0 supply duty points, +4.0 exhaust duty points, and +10.0% for both RPM per
airflow. Restoring the source values after the comparison window turned over
returned SFP and duty changes to 0.0. Low fan level and a flow just outside
±5% produced `no_matching_reference` and unavailable comparison. A flow that
crossed the 25 m³/h lookup boundary while still within ±5% retained the
eligible reference. Five distinct matched reports were required before the
conditioned result became ready. Browser screenshots in the associated Codex
task show both the ready +10.0% dashboard and the no-match explanation.

The isolated 20-second comparison window is a test override; production uses
15 minutes. All v2 comparison entities have new unique IDs. Legacy entities
and saved history were not rewritten. No physical ventilation command or
external household message was sent.
