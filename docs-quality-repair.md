# V2 quality repair

An unchanged bypass position can be used beyond ten minutes only when the
connectivity entity remains on, its connection timestamp precedes the position
report, and power plus both airflow reports are current. The original bypass
report timestamp remains visible. Offline, unknown, pre-reconnection and stale
dynamic evidence remain ineligible. This is a latched-state reporting contract,
not a fabricated new observation of the bypass position.

Duty and RPM values now pass the same unit, range and report-age checks before
entering calibration or comparisons. Invalid optional measurements do not block
SFP. Stability restarts on fan-level changes, observation gaps or clean-filter
confirmation. Corrected persistence is checked before restoration, including
cycle deadlines, candidate structure, reference values and trend identities;
invalid files remain on disk for diagnosis and the monitor awaits confirmation.

Observation summaries now require complete elapsed duration, adequate sample
count and bounded polling gaps. This does not establish full feature acceptance:
calibration, recovery and rendered dashboard journeys must also pass.

No legacy entity IDs, history values, physical controls or clean-filter
confirmations are changed by this repair. The scheduled observation remains
paused. Rollback: restore the backed-up app Python files and dashboard YAML;
AppDaemon reloads the app, and YAML dashboard reloads on navigation. Do not reset
manufacturer timers or corrected calibration to perform rollback.
