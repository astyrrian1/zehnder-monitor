# Milestone 2 observed result (isolated stack, 2026-09-28 UTC)

The initial end-to-end invalid-quality assertion failed before the quality
entity existed. After implementation, the complete `run_m2.py` chain passed
through real AppDaemon, MQTT, and HA entities. The rendered dashboard was
inspected and captured in the associated Codex task in both invalid and current
states.

- NaN, infinity, and negative power produced `invalid` and withdrew SFP.
- An unsupported unit produced `unsupported`; an 11-minute-old source report
  produced `stale`; missing report evidence produced `unknown_freshness`.
- Missing power produced `unavailable`; zero airflow produced `stopped`.
- Valid reporting restored `0.7406` without clearing persistence.
- Missing RPM made fan-effort quality unavailable while SFP stayed current.
  Unknown bypass made recovery quality unsupported while SFP stayed current.
- Polling an unchanged source fingerprint left the distinct-report count at 1.
  A new same-value HA report increased it from 1 to 2. Three distinct stable
  eligible reports made baseline sampling eligible; no report was counted twice.
- Stopping AppDaemon made corrected SFP and quality unavailable through
  180-second MQTT expiry. Restarting HA while the publisher was stopped did not
  restore a current corrected value. Reseeding simulated sources before the
  publisher restarted restored a current reading.
- The affected optional-RPM path and the milestone 1 two-power journey passed
  again after the final legacy guard. The fresh 72 W and 79.2 W updates took
  43.8 and 59.96 seconds from seed to HA assertion.
- `python3 -m unittest discover -s tests`: 40 passed.

No physical ventilation service, production HA configuration, or external
notification target was used.
