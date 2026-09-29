# Milestone 9 — persistent monitor-reporting fault

Before installing the watchdog, the browser/API acceptance lookup for
`automation.zehnder_monitor_reporting_watchdog` returned 404. The installed
isolated HA package then passed the real-time publisher-loss journey. The
corrected quality entity expired to unavailable after AppDaemon stopped; the
watchdog persisted incident `2026-09-28T21:14:55.822483+00:00` and retained its
first-observed time across an HA restart. There was no early notice. A single
`zehnder_monitor_reporting_fault` notification appeared 5.9 seconds after the
full 600-second threshold. Repeated triggers did not change its creation time.
Restart recreated that same ID without a second simultaneous notice. Three
valid evaluations cleared the incident and notification.

The edge journey passed: stopped fan reporting made no fault; a brief invalid
source interval recovered with no notice; a second incident was permitted. The
test then substituted an intentionally missing *isolated HA service* for
notification delivery. The service failed and `fault_delivered` stayed off.
Restoring the native service and triggering the automation delivered the notice
once; repeated triggers made no duplicate. Recovery resolved the new incident.
The test restored the package and sent no external household message.

The rendered dashboard showed “No persistent monitor-reporting incident is
active” and “Last recovery confirmed: 2026-09-28 21:28:23.” A browser screenshot
was captured in the task. The notice wording identifies a reporting-path issue,
not a mechanical ventilation diagnosis.

Validation: `homeassistant --script check_config` passed on the isolated stack;
63 Python unit tests and `git diff --check` passed. Production HA configuration
is unchanged.
