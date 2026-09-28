# Corrected Zehnder Monitor v2

The v2 publisher adds entities with new unique IDs on non-retained MQTT topic
`zehnder/monitor/v2/state`. It leaves legacy discovery, entity IDs, numeric
history, and `baselines.json` intact. Corrected state persists separately in
`corrected_v2.json`; it does not import legacy clean-filter baselines or samples.

## Current readings

Corrected SFP uses total reported electrical power divided by the mean of
supply and exhaust airflow, in kW/(m³/s). At 72 W and 350 m³/h on both paths,
the result is 0.740571 kW/(m³/s). The entity includes each source value, unit,
source report time, and calculation time. Unsupported, nonfinite, stopped, or
stale inputs withdraw the current value. Corrected state messages are not
retained and expire in HA after 180 seconds without publication. Reporting age
is limited to ten minutes for dynamic inputs; polling cached values does not
create new sampling evidence.

Apparent sensible recovery is `(supply − outdoor)/(extract − outdoor) × 100`.
It requires at least a 5°C outdoor/extract difference and suitable operating
evidence. Heating and cooling use the same signed ratio. An out-of-range ratio
is preserved as an anomaly, not clipped into a healthy reading. This is an
apparent temperature ratio, not certified heat-exchanger efficiency.

## Confirmed clean-filter reference

The dashboard action means **both supply and exhaust filter paths are clean**.
It creates an idempotent cycle with two hours of settling and 72 hours of
learning. Each metric and Low/Medium operating point needs at least 20 distinct
source-reported observations over at least 30 minutes, with both airflow
distributions within ±5% of their medians. Qualified references freeze. A
manufacturer timer increase merely requests confirmation; it never proves
replacement. Missing or unvisited references leave comparisons unavailable.

Current fan-effort comparison requires the same cycle and fan level, with both
flows within ±5% of the reference medians. The dashboard shows signed SFP,
duty, and RPM-per-flow changes. A 15-minute current window needs five accepted
observations. A seven-day trend needs 24 nonempty UTC hourly medians spanning
72 hours and reports W/(m³/s)/day. No corrected filter-capacity percentage or
remaining-life estimate is made.

## Notifications

Native HA automations provide internal persistent notifications for the
manufacturer's filter countdown at seven days/due now and for a persistent
monitor-reporting failure after ten minutes. Missing calibration, stopped fans,
bypass operation, and normal comparison ineligibility do not create monitor
faults. The fault clears after three valid reporting evaluations. The
notification package sends no physical ventilation commands or external
messages.

## Adoption and rollback

Back up the current AppDaemon app directory, `baselines.json`, `state.json`,
the MQTT/HA entity registry snapshot, the dashboard document, and HA config
before deployment. Deploy publisher code first, verify new IDs and units with
live source snapshots, then switch the dashboard. Leave corrected notifications
disabled for the 24-hour passive observation; enable them only after acceptance.
Do not simulate clean-filter confirmation on production. An initial
`awaiting_confirmation` state is expected.

To roll back, disable the two v2 notification automations, restore the prior
dashboard document, restore the AppDaemon code backup, and restart AppDaemon.
The v2 current MQTT entities expire within 180 seconds. Leave
`corrected_v2.json` archived for a later retry; legacy persistence and HA
history remain in place. Verify legacy entity IDs/history and the absence of
active corrected alerts after rollback.

The software tests validate formulas and data handling. Physical power,
airflow, and temperature sensor accuracy still requires independent field
measurement.
