# Isolated Zehnder end-to-end stack

This Compose project uses a fresh Home Assistant, Mosquitto, and AppDaemon. It
contains no production credentials, Zehnder connection, physical fan control,
or external notification integration. It only writes simulated state to HA's
test API on port 18123. Keep the Compose project on a separate Docker host or
an isolated local Docker daemon.

1. Copy this directory to the test host and run `docker compose up -d mqtt ha`.
2. Finish first-run HA onboarding with a test-only account and add the MQTT
   integration with broker `mqtt`, port 1883, and certificate verification off.
3. Copy the three Python files from `apps/zehnder_monitor/` into
   `appdaemon/apps/`. Create `appdaemon/secrets.yaml` with a test-only HA access
   token as `ha_token: <token>`, then run
   `docker compose --profile publisher up -d appdaemon`.
4. Set `ZMON_TEST_HA_URL=http://<test-host>:18123` and
   `ZMON_TEST_HA_TOKEN=<test-token>` locally. Run `python3 run_m1.py`.
5. Open `http://<test-host>:18123/lovelace/zehnder` in a browser. Confirm the
   card shows the corrected SFP, source inputs, units, report timestamps, and
   calculation time. The legacy SFP is on a separate labeled card.

Both scripts refuse HA URLs without the isolated `:18123` port. The MQTT
broker's host port is bound to loopback only. The v2 publisher issues no
ventilation commands; its state message is non-retained.

The first newly discovered HA entity can remain `unknown` until the second
60-second publication because discovery and state are separate MQTT messages.
The 65-second input-to-display target applies after discovery is established.

For milestone 2, keep the test account's OAuth token JSON outside this repo,
then set `ZMON_TEST_TOKEN_FILE=<absolute test-token JSON path>` and
`ZMON_TEST_STACK_SSH=<test Docker host>` alongside `ZMON_TEST_HA_URL`.
Run `python3 -u run_m2.py`. The script refreshes the test token, updates only
the isolated AppDaemon secret, and exercises invalid inputs, expiry, HA restart,
and recovery. It takes roughly 15 minutes at the fixed 60-second cadence.
REST-created simulated entities should be seeded before AppDaemon starts after
an HA restart, so its initial snapshot includes them.

For milestone 3, set `ZMON_TEST_STACK_SSH`, `ZMON_TEST_HA_URL`, and a current
`ZMON_TEST_HA_TOKEN`, then run `python3 -u run_m3.py`. It resets only this
isolated project's persistence directory to demonstrate fresh installation
and a legacy May-baseline upgrade. The corrected reference stays empty in both.
Run `python3 -u assert_pending_quiet.py --seconds 601` to hold that state
through the ten-minute fault-notification delay while renewing simulated
source reports. These scripts never confirm maintenance or issue fan commands.

For milestone 4, run `python3 -u run_m4.py` with the same isolated URL,
`ZMON_TEST_TOKEN_FILE`, and `ZMON_TEST_STACK_SSH` as milestone 2. It copies the
current publisher only into the named test stack, uses test-only 2-second ticks,
6-second settling, 120-second learning, and 32-second minimum span. Production
defaults remain 60 seconds, two hours, 72 hours, and 30 minutes. The script
resets only `corrected_v2.json` in the isolated volume, confirms simulated
maintenance, checks duplicate delivery and restart, qualifies SFP/duty/RPM,
freezes references, repeats without RPM for partial qualification, and checks
corrupt-file recovery plus timer-only confirmation prompting. Open the isolated
HA dashboard to inspect the confirmation dialog and rendered progress. It
never sends fan, bypass, or timer-control commands.

For milestone 5, run `python3 -u run_m5.py` with the same isolated URL,
`ZMON_TEST_TOKEN_FILE`, and `ZMON_TEST_STACK_SSH`. It creates a reference via
`run_m4.py --reference-only`, then verifies conditioned deterioration, recovery,
fan-level isolation, an airflow-band boundary, and an out-of-tolerance flow.
The isolated publisher uses a 20-second comparison window so a 15-minute
production window can be exercised quickly. Open the dashboard during the
ready step to inspect the signed SFP, duty, and RPM-per-flow changes.

For milestone 6, run `python3 -u run_m6.py` with the same isolated URL,
`ZMON_TEST_TOKEN_FILE`, and `ZMON_TEST_STACK_SSH`. The script builds a fresh
reference, then uses the test-only HA clock entity and simulated source report
timestamps to replay UTC hourly observations through AppDaemon and MQTT. It
checks positive, negative, flat, sparse, and new-cycle cases. `--reuse-reference`
is an accelerated development option when the isolated stack already has a
qualified test reference. Production never reads the test clock entity.

For milestone 7, run `python3 -u run_m7.py` with the same isolated URL,
`ZMON_TEST_TOKEN_FILE`, and `ZMON_TEST_STACK_SSH`. It refreshes the test token,
restarts only the isolated stack, and feeds heating, Fahrenheit, cooling, small
Delta-T, bypass, stale-temperature, and anomalous-ratio source states. The
30-second conditioned recovery window is test-only; production uses 15 minutes.
The dashboard distinguishes current raw recovery from historical median.

For milestone 8, run `python3 -u run_m8.py` with the same isolated URL,
`ZMON_TEST_TOKEN_FILE`, and `ZMON_TEST_STACK_SSH`. It installs the native HA
maintenance package only into the isolated stack and exercises unknown, seven-day,
due-now, timer-reset, confirmed-maintenance, later-episode, restart, and
already-due-at-startup paths. The notification capture uses Home Assistant's
WebSocket persistent-notification command. The test sends no external message.

For milestone 9, run `python3 -u run_m9.py` with the same isolated settings.
It uses the production ten-minute fault threshold and takes roughly 15 minutes,
including the publisher's 180-second expiry. Then run
`python3 -u run_m9_edge.py` for brief-fault suppression, a deliberately failed
test-only notification service, successful retry, and new-incident recovery.
The temporary missing service is restored by the edge test. Both scripts operate
only on the named isolated Compose stack and use internal HA notifications.

For the integrated pre-production gate, run `python3 -u run_m10_chain.py` with
the same isolated environment. It runs all previous journeys sequentially and
writes a JSON report to `/private/tmp/zehnder-m10-chain.json` by default. Run
`python3 -u run_m10_rollback.py` for the isolated rollback rehearsal; the next
chain invocation includes it automatically. After installing the publisher on
production, use `verify_live_v2.py` for read-only live calculation acceptance,
`snapshot_legacy.py` before and after rollout to compare recorder history, and
`observe_v2.py --hours 24 --interval-seconds 60` for passive observation.
These production readers use `ZMON_OBSERVE_HA_URL` and
`ZMON_OBSERVE_HA_TOKEN`; they do not write HA state.

After the integrated chain, run `run_quality_regressions.py` with the same
isolated environment. It holds bypass and connection timestamps unchanged while
power and airflow remain fresh, verifies recovery and sampling, rejects an
out-of-range duty independently, and tests disconnect/reconnect withholding.
It never confirms maintenance or controls ventilation. Unlike `seed.py`, this
journey deliberately does not refresh every source on every observation.
