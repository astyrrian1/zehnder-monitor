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
  HA Sensors     MQTT Telemetry
  (Raw metrics)  (zehnder/monitor/state)
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
| `sensor.zehnder_filter_health` | % | Composite health score (0–100) |
| `sensor.zehnder_duty_ratio` | ratio | Supply/exhaust duty ratio |
| `sensor.zehnder_heat_recovery` | % | Heat recovery efficiency |
| `sensor.zehnder_sfp_trend` | mW/(m³/s)/day | SFP degradation rate from 7-day regression |

## Alert Tiers

AppDaemon outputs raw scores. Alerting is handled by a **blueprint** — a reusable automation template you import once and configure per tier.

### Setup (one-time)

1. **Import the blueprint** — click this link in your HA instance:

   [![Import Blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fastyrrian1%2Fzehnder-monitor%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fzehnder_filter_alert.yaml)

   Or manually: **Settings → Automations → Blueprints → Import Blueprint** and paste:
   ```
   https://github.com/astyrrian1/zehnder-monitor/blob/main/blueprints/automation/zehnder_filter_alert.yaml
   ```

2. **Create three automations** from the blueprint (Settings → Automations → Create → Use Blueprint):

   | Tier | Threshold | Cooldown | What It Means |
   |---|---|---|---|
   | **Advisory** | 60% | 24 hours | Filters aging — keep an eye on it |
   | **Warning** | 30% | 6 hours | Plan a replacement soon |
   | **Critical** | 10% | 1 hour | Replace immediately — energy waste and reduced airflow |

   For each, select your preferred notification target (persistent notification, mobile app, etc.).

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
- MQTT broker (for telemetry publishing)
- Native Threshold Helpers and Automations configured in HA UI (for alerting)
- [HACS](https://hacs.xyz/) (recommended, for managed installation and updates)

## Conditioned Sampling

The monitor only records SFP samples for trend analysis when:
- Fan level is **Low** (steady-state, most time spent here)
- Bypass is **< 5%** (no economizer interference)
- Power is **> 20W** (unit actually running)
- Flow imbalance is **< 10%** (no defrost or anomaly)

This ensures trend comparisons are apples-to-apples over weeks and months. *(Note: Because of this highly conditional filtering, AppDaemon handles the 7-day regression internally rather than relying on HA's native `derivative` helper).*

## Baseline Management

Baselines are captured automatically when a filter change is detected (the countdown timer jumps by >90 days). After detection, the system waits 2 hours for stabilisation before recording.

Baselines persist in `baselines.json` alongside the app.

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
    "status": "Good",
    "sfp_trend_per_day": 0.000312,
    "conditioned_samples": 847
  },
  "raw": { ... },
  "baselines": { ... }
}
```

## License

MIT
