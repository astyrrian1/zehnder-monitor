# Milestone 4 observed result (isolated stack, 2026-09-28 UTC)

The new calibration tests failed before confirmation and collection existed,
then passed. The complete unit suite passed (51 tests). The production timing
rules are exact: confirmation T, settling T+2h, learning close T+74h. Only the
isolated Compose app uses accelerated values for the repeatable journey.

`run_m4.py` passed through simulated HA source states, AppDaemon, MQTT, HA
entities, and the rendered dashboard. It observed:

- Explicit event confirmation created one cycle with a 6-second settling
  deadline and a 126-second total learning deadline in test mode; duplicate
  event delivery and AppDaemon restart left both deadlines unchanged.
- 20 distinct source reports spanning at least 32 seconds qualified the SFP,
  duty, and RPM/flow references for Medium at 350 m³/h. New data did not change
  the frozen reference.
- A later confirmed cycle archived the first reference. With RPM unavailable,
  SFP and duty still qualified; RPM/flow did not.
- Corrupt corrected persistence restarted in `awaiting_confirmation` with no
  comparison. A timer-only jump from 7 to 180 days raised a confirmation
  request and created no corrected reference.
- The browser showed the clean-filter confirmation dialog, rendered learning
  progress and candidate counts, then the qualified reference with capture
  conditions. Screenshots were captured in the associated Codex task.

The earlier M1 calculation journey republished 0.7406 and 0.8146 at 72 W and
79.2 W; M2 invalid NaN power withdrew SFP and valid reporting restored it;
M3 fresh and legacy-upgrade journeys remained pending without silent baseline
promotion. No test issued a ventilation control command or external message.
