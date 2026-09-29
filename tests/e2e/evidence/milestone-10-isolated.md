# Milestone 10 — isolated integrated acceptance and rollback

The adoption acceptance test was red before the production dashboard and
monitor-only confirmation helper were staged: three focused HA contract checks
failed for missing files/registration. They pass after the staged change.

The repeatable `run_m10_chain.py` ran from 2026-09-28 22:03:22 to 22:44:37 UTC
against the named isolated HA/MQTT/AppDaemon stack. All eleven sequential
journeys exited 0: milestones 1–9, the 601-second pending-calibration quiet
hold, and the failed-delivery/new-incident edge journey. The chain covered
current SFP, invalidation/recovery, absent calibration, confirmed maintenance,
reference learning, matched-flow comparison, positive/negative/flat/sparse
trend, screened recovery, maintenance reminders, a ten-minute monitor fault,
restart, retry, and recovery. The final fault notice appeared 15.2 seconds
after the full 600-second recognized-failure threshold.

During the chain, two test-harness timing assumptions were corrected: stop
AppDaemon before clearing corrected test storage, and wait for each virtual
hourly source report to be accepted through HA rather than sleeping a fixed
interval. The corrected trend replay then passed 29 buckets over 112 hours
with +10, -10, and zero W/(m³/s)/day results.

The rendered dashboard was retested after removing live source tiles that
could briefly outpace the corrected calculation. It displayed 0.815
kW/(m³/s) alongside the exact 79.2 W, 350 m³/h supply, and 350 m³/h exhaust
snapshot and calculation/report timestamps. A browser screenshot was captured
in the task. The refreshed milestone-1 journey passed at 1.23 seconds for each
source update.

`run_m10_rollback.py` backed up the isolated app, dashboard, packages, and
persistence; restored the exact pre-v2 publisher; verified corrected readings
became unavailable and 115 preexisting legacy recorder observations remained;
then restored v2. Rollback to legacy took 16.4 seconds, below the ten-minute
target. No physical ventilation command or external household message was
issued. The production rollout and 24-hour passive observation remain separate
gates and are not claimed by this isolated evidence.

App validation: 65 Python unit tests, Python compilation, and `git diff --check`
passed. The staged production dashboard/control config passed Home Assistant
2026.9.4 `check_config` in a disposable container with zero config errors or
warnings. Production source telemetry, four legacy entities, 173 recorder
observations, and 15 legacy registry identities were captured read-only before
rollout for comparison.
