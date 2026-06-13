# Zehnder Monitor

Physics-based filter health monitoring for the **Zehnder ComfoAir Q600** HRV.  
Runs as a standalone [AppDaemon](https://appdaemon.readthedocs.io/) app for Home Assistant.

## Why?

The ComfoAir's built-in filter timer is a dumb countdown — it has no idea whether your filters are actually degraded. This monitor uses the unit's own telemetry to detect **real** performance degradation:

| Metric | What It Tells You |
|---|---|
| **Specific Fan Power (SFP)** | Electrical energy required per unit of air moved. Rises as filters clog. |
| **Duty Ratio** | Supply duty / exhaust duty. Speed-independent measure of differential filter loading. |
| **RPM/Flow** | How hard each impeller works per unit of airflow. Direct resistance proxy. |
| **Heat Recovery η** | Seasonal check on heat exchanger condition. |

### Why Duty Ratio, Not Absolute Asymmetry?

Pressure drop through filter media scales with Q² (turbulent flow). At higher fan speeds, the absolute duty gap between supply and exhaust **naturally widens** even with identical filter condition. The **ratio** normalises for this, giving a speed-independent signal.

## Architecture

```text
Zehnder ComfoAir Q600
       │
       ▼
  Home Assistant (native integration)
       │
       ▼
  AppDaemon ──── zehnder_monitor.py
       │              │
       ▼              ▼
  MQTT Discovery  MQTT Telemetry
  (HA Sensors)    (zehnder/monitor/state)
       │
       ▼
  HA Threshold Helpers
  (State tracking & hysteresis)
       │
       ▼
  HA Automations
  (Rate-limited notifications)
```

## Sensors Created

| Sensor | Type | Description |
|---|---|---|
| `sensor.zehnder_sfp` | kW/(m³/s) | Specific Fan Power with EU class attribute |
| `sensor.zehnder_filter_health` | % | Composite cycle-minimum health score (0–100) |
| `sensor.zehnder_duty_ratio` | ratio | Supply/exhaust duty ratio |
| `sensor.zehnder_heat_recovery` | % | Heat recovery efficiency |
| `sensor.zehnder_sfp_trend` | mW/(m³/s)/day | SFP degradation rate from 7-day regression |
| `sensor.zehnder_sample_quality` | text | Whether headline metrics are conditioned, stale, or live fallback |
| `sensor.zehnder_conditioned_samples` | count | Number of 7-day conditioned samples feeding the trend |
| `sensor.zehnder_raw_sfp` | kW/(m³/s) | Instantaneous diagnostic SFP before conditioning |
| `sensor.zehnder_heat_recovery_quality` | text | Whether heat recovery is based on fresh conditioned temperature samples |
| `sensor.zehnder_raw_heat_recovery` | % | Instantaneous diagnostic recovery before conditioning/freshness checks |
| `sensor.zehnder_filter_capacity_remaining` | % | Clean-baseline-normalized filter capacity remaining |
| `sensor.zehnder_sfp_capacity_remaining` | % | SFP-only capacity remaining relative to the clean baseline |
| `sensor.zehnder_duty_capacity_remaining` | % | Duty-ratio-only capacity remaining relative to the clean baseline |
| `sensor.zehnder_baseline_system_resistance` | % | Inferred clean-filter system resistance on the generic SFP envelope |
| `sensor.zehnder_baseline_quality` | text | Baseline source quality: conditioned, single_sample, fallback, invalid, or learning |

## Alert Tiers

AppDaemon outputs raw scores. Alerting is handled by a **blueprint** — a reusable automation template you import once and configure per tier.

### Setup (one-time)

1. **Import the blueprint** — click this link in your HA instance:

   [![Import Blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fastyrrian1%2Fzehnder-monitor%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fzehnder_filter_alert.yaml)

   Or manually: **Settings → Automations → Blueprints → Import Blueprint** and paste:
   ```
   https://github.com/astyrrian1/zehnder-monitor/blob/main/blueprints/automation/zehnder_filter_alert.yaml
   ```

2. **Create three automations** from the blueprint (Settings → Automations → Create → Use Blueprint).
   The default alert sensor is `sensor.zehnder_filter_capacity_remaining`, the
   baseline-aware post-clean-filter capability metric. If you want the original
   absolute/generic behavior, select `sensor.zehnder_filter_health` for the alert
   sensor.

   | Tier | Threshold | Cooldown | What It Means |
   |---|---|---|---|
   | **Advisory** | 60% | 24 hours | Filter capacity declining — keep an eye on it |
   | **Warning** | 30% | 6 hours | Plan a replacement soon |
   | **Critical** | 10% | 1 hour | Replace filters or inspect intake/exhaust restriction |

   For each, select your preferred notification target (persistent notification, mobile app, etc.). Notifications include baseline quality, SFP capacity, duty capacity, inferred baseline system resistance, absolute filter health, trusted SFP, and trusted duty ratio.

## Installation

### HA OS with AppDaemon Addon

1. **One-time setup — point AppDaemon at the HACS apps directory:**

   On HA OS, the AppDaemon addon defaults to reading apps from `/addon_configs/a0d7b954_appdaemon/apps/`, but HACS installs AppDaemon apps to `/config/appdaemon/apps/`. To bridge this, add `app_dir` to your AppDaemon config:

   Open `/addon_configs/a0d7b954_appdaemon/appdaemon.yaml` (use File Editor or Studio Code Server — you may need to disable "Enforce base path" in the addon settings to access `/addon_configs/`):

   ```yaml
   appdaemon:
     app_dir: /homeassistant/appdaemon/apps/
     # ... keep your existing settings below
   ```

   > `/homeassistant/` is the internal mount point for `/config/` inside the addon container.

   Restart AppDaemon after this change. **This only needs to be done once** — after this, all HACS-managed AppDaemon apps will load automatically.

2. **Install via HACS:**
   - Open **HACS → Automation** (AppDaemon category)
   - Click **⋮ → Custom repositories**, add `astyrrian1/zehnder-monitor` as **AppDaemon**
   - Search for **Zehnder Monitor** and click **Install**

3. **Import the alert blueprint** (see [Alert Tiers](#alert-tiers) above)

4. **Restart AppDaemon** (Settings → Add-ons → AppDaemon → Restart)

Updates are handled through HACS — click **Update** when a new release is available, then restart AppDaemon.

### Manual (Docker / venv installs)

1. Clone this repo on the machine running AppDaemon:
   ```bash
   git clone https://github.com/astyrrian1/zehnder-monitor.git
   ```
2. Symlink into your AppDaemon apps directory:
   ```bash
   ln -s ~/zehnder-monitor/apps/zehnder_monitor /path/to/appdaemon/apps/zehnder_monitor
   ```
3. Restart AppDaemon

## Requirements

- Home Assistant with the Zehnder ComfoAir Q integration
- AppDaemon 4.x
- MQTT integration with discovery enabled (for HA sensor creation and telemetry publishing)
- Native Threshold Helpers and Automations configured in HA UI (for alerting)
- [HACS](https://hacs.xyz/) (recommended, for managed installation and updates)

## Conditioned Sampling

The monitor only records SFP samples when:
- Fan level is **Low** (steady-state, most time spent here)
- Bypass is **< 5%** (no economizer interference)
- Power is **> 20W** (unit actually running)
- Flow imbalance is **< 10%** (no defrost or anomaly)

Headline SFP, duty ratio, and health score use the median of recent conditioned samples when available. Samples are accepted only after multiple consecutive stable ticks at Low or Medium fan level with bypass closed and balanced airflow. If fewer than five conditioned samples exist after startup or immediately after a filter-cycle reset, the monitor reports an untrusted sample quality such as `warming_up` or `live_fallback`; headline MQTT sensors are marked unavailable so raw telemetry is not mistaken for a filter-health signal. Use `sensor.zehnder_raw_sfp` for live diagnostics while waiting for conditioned samples. This keeps trend comparisons apples-to-apples over weeks and months. *(Note: Because of this highly conditional filtering, AppDaemon handles the 7-day regression internally rather than relying on HA's native `derivative` helper).*

Heat recovery is stricter: it is only published as a trusted metric when the outdoor, supply, and extract temperature readings are fresh and collected during stable operation. If the Zehnder integration has stale temperature states, `sensor.zehnder_heat_recovery` is marked unavailable and `sensor.zehnder_heat_recovery_quality` reports `unavailable`.

## Baseline Management

Baselines are captured automatically when a filter change is detected (the countdown timer jumps by >90 days). After detection, the system waits 2 hours for stabilisation before recording.

Baselines and rolling trend state persist outside the HACS-managed app directory, under `<appdaemon config>/zehnder-monitor/` by default. Existing `baselines.json` and `state.json` files found alongside the app are migrated automatically on first startup, so clean-filter captures and the 7-day conditioned sample window survive future HACS updates. Set `data_dir` in the AppDaemon app config if you need a custom persistence location.

When enough stable clean-filter samples are available at a fan level, the monitor stores a conditioned per-fan-level baseline and uses it for capability metrics.

## Baseline-Aware Capacity

`sensor.zehnder_filter_health` is preserved as the existing absolute/generic performance health score for compatibility with dashboards and alert automations. Within a filter cycle it publishes the worst trusted score seen so far, not the most optimistic recent estimate, so it does not climb because a later conditioned sample happened to look cleaner.

`sensor.zehnder_filter_capacity_remaining` is additive. It answers a different question: how much filter capacity remains after accounting for this home's clean-filter baseline system resistance and normal external static pressure load?

```
SFP_capacity  = (SFP_REPLACE - current_SFP) / (SFP_REPLACE - baseline_SFP) * 100
Duty_capacity = (RATIO_REPLACE - current_ratio) / (RATIO_REPLACE - baseline_ratio) * 100

Filter_capacity = (SFP_capacity * 0.65) + (Duty_capacity * 0.35)
```

Capacity is clamped to 0-100%. Better-than-baseline readings cap at 100%, so a correct filter replacement should report near 100% after the clean baseline has been captured. Within a filter cycle, the public capacity sensors publish the cycle minimum: `sensor.zehnder_filter_capacity_remaining`, `sensor.zehnder_sfp_capacity_remaining`, and `sensor.zehnder_duty_capacity_remaining` can decrease, but they do not increase until the Zehnder filter timer reset path clears the cycle state. Instantaneous baseline comparisons are still included in the retained MQTT payload as `instant_filter_capacity_remaining`, `instant_sfp_capacity_remaining`, and `instant_duty_capacity_remaining` for diagnostics. If the monitor only has fallback or invalid baseline data, the new capacity sensors are marked unavailable rather than publishing misleading values.

`sensor.zehnder_baseline_system_resistance` expresses the captured clean-filter SFP baseline on the generic SFP health envelope. This is an inferred context metric from fan, power, and flow telemetry; the app does not directly measure duct static pressure.

## Health Score Formula

```
Health = (SFP_score × 0.50) + (Ratio_score × 0.30) + (Timer_score × 0.20)

SFP_score:   100 at 0.35 kW/(m³/s), 0 at 0.80 kW/(m³/s)
Ratio_score: 100 at 1.20x,          0 at 2.50x
Timer_score: 100 at 180 days,       0 at 0 days
```

## MQTT Telemetry

Published to `zehnder/monitor/state` (retained) every 60 seconds:

```json
{
  "timestamp": "2026-04-04T19:00:00",
  "metrics": {
    "sfp": 0.4521,
    "duty_ratio": 1.482,
    "heat_recovery_eta": 89.2
  },
  "health": {
    "score": 64.3,
    "instant_score": 65.1,
    "score_mode": "cycle_minimum",
    "status": "Good",
    "sfp_trend_per_day": 0.000312,
    "conditioned_samples": 847
  },
  "capability": {
    "filter_capacity_remaining": 99.5,
    "instant_filter_capacity_remaining": 100.0,
    "sfp_capacity_remaining": 100.0,
    "instant_sfp_capacity_remaining": 100.0,
    "duty_capacity_remaining": 98.6,
    "instant_duty_capacity_remaining": 99.1,
    "capacity_mode": "cycle_minimum",
    "baseline_system_resistance": 46.1,
    "baseline_quality": "single_sample"
  },
  "raw": { ... },
  "baselines": { ... }
}
```

## License

MIT
