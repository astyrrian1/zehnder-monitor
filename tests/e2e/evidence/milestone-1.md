# Milestone 1 observed result (isolated stack, 2026-09-28 UTC)

- Simulated source at 72 W, 350/350 m³/h produced MQTT discovery unique ID
  `zehnder_monitor_v2_sfp` and HA state `0.7406 kW/(m³/s)`.
- Changed simulated power to 79.2 W. The HA entity became `0.8146` with its
  attributes recording 79.2 W, both 350 m³/h flows, each source report time,
  and calculation time `2026-09-28T18:00:13.510391+00:00`.
- The source power report time was `2026-09-28T17:59:36.486217+00:00`, giving
  a 37.0-second source-to-calculation interval, within the 65-second target.
- The browser-rendered card displayed `0.8146 kW/(m³/s)`, `79.2 W`, both
  `350 m³/h` flows, and the calculation/source timestamps. The dashboard
  screenshot was captured in the associated Codex task.
- A complete rerun of `run_m1.py` passed both fixtures through the HA entity.
  The second change took 60.01 seconds from test seed to HA assertion.
- `python3 -m unittest discover -s tests`: 28 passed.

The legacy MQTT topic and unique IDs remained untouched by the v2 code path.
No production HA config or production monitor was changed.
