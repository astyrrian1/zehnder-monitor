# Milestone 7 observed result (isolated stack, 2026-09-28 UTC)

Focused recovery tests failed before temperature normalization and the
conditioned window existed, then passed. `run_m7.py` passed through simulated
HA source states, AppDaemon, MQTT, HA entities, and the rendered dashboard.
Heating (0/20/16°C), equivalent Fahrenheit (32/68/60.8°F), and cooling
(30/20/22°C) each displayed **80.0%** apparent sensible recovery. At least
five distinct reports formed an 80.0% conditioned median. Small temperature
difference, open bypass, and a stale temperature withdrew current raw recovery
while corrected SFP remained current. A 120.0% raw ratio stayed 120.0%, was
marked `anomalous`, and was explicitly described as not a healthy recovery
reading. The prior median remained visible only as historical, with its last
source-report time. A browser screenshot in the associated Codex task captured
the anomaly explanation and separate current/historical values.

The ratio is an apparent sensible temperature calculation, not a certified
heat-exchanger efficiency. Original source values and units remain in the v2
input snapshot; converted Celsius temperatures are included for diagnostics.
The production conditioned window is 15 minutes. No physical ventilation
command or external household message was sent.
