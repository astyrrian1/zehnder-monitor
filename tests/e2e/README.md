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
