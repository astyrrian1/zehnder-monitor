# Milestone 3 observed result (isolated stack, 2026-09-28 UTC)

The focused persistence tests failed before corrected-only loading existed and
passed afterward. The versioned `corrected_v2.json` file is written to the
isolated AppDaemon volume; no legacy file is read to populate its references.

`run_m3.py` passed both a fresh-install and a legacy-upgrade journey through
simulated HA source states, AppDaemon, MQTT discovery/state, and HA entities:

- Both showed corrected calibration `awaiting_confirmation`, empty references,
  absolute SFP `0.7406`, and corrected SFP change unavailable.
- The upgrade fixture retained the May 18 legacy capture while the corrected
  file remained at schema version 2 with zero references.
- No corrected capacity, remaining-life, or replacement entity was created.
- The rendered dashboard showed the pending-reference explanation, the action
  needed after maintenance, corrected SFP, unavailable comparison, and legacy
  baseline marked as context only. A browser screenshot was captured in the
  associated Codex task.

`assert_pending_quiet.py --seconds 601` completed 11 checks while simulated
source reports continued. Calibration stayed `awaiting_confirmation`, corrected
references stayed empty, comparison stayed unavailable, and no Zehnder notice
appeared. The rendered HA Notifications panel also showed “No notifications.”

The prior journeys remained green after this implementation: M1 republished
0.7406 at 72 W and 0.8146 at 79.2 W, and M2 withdrew SFP for NaN power
(`invalid_power`) then restored 0.7406 after valid reports resumed.
