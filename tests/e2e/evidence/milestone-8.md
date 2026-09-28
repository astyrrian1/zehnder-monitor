# Milestone 8 — manufacturer maintenance reminder

The isolated Home Assistant package supplies two native automations and persisted
maintenance episode helpers. It uses only `persistent_notification.create` and
`persistent_notification.dismiss`; no household messaging or ventilation control
service is called.

The browser-level acceptance test was red before implementation: the isolated
HA had no maintenance automation and the queried automation entity returned 404.
After implementation, `run_m8.py` passed the complete source → HA automation →
persistent notification journey. The simulated countdown comes from the test
source entity; the corrected calibration cycle comes through AppDaemon → MQTT →
HA. Its final run reported:

```json
{"result":"pass","initial_seconds":0.2,"single_notice_id":"zehnder_filter_maintenance","restart_reinstated":true,"escalated":true,"unknown_suppressed":true,"confirmed_cycle":"e2e-m8-confirmed-210725","later_episode_notified":true,"already_due_startup_seconds":6.9}
```

The test asserted one stable notice ID and unchanged creation time after repeated
automation triggers. Restart recreated the same notice ID once; severity escalated
from seven-day to due-now. Unknown values made no notice. A timer reset did not
dismiss an existing notice. Explicit clean-filter confirmation dismissed the old
notice and suppressed that cycle until the manufacturer countdown reset. A later
episode notified again. For the startup case, a retained *test-only* MQTT source
was present when HA started with a zero-day countdown; the due-now notice appeared
within 6.9 seconds. The test removed those retained MQTT topics afterward.

The rendered isolated HA Notifications panel showed one “Zehnder filter
maintenance due soon” card with the manufacturer countdown and a clean-filter
confirmation instruction. No capacity score or remaining-life prediction appeared.
The screenshot was captured in the task browser during the acceptance run.

Validation: 63 Python unit tests passed; the installed isolated HA package passed
`homeassistant --script check_config`; `git diff --check` passed. Production
configuration and all legacy entity/history records remain untouched.
