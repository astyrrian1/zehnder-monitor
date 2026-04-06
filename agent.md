# Agent Context: Zehnder Monitor

## Project Overview
This repository contains a standalone, physics-based filter health monitoring system for the Zehnder ComfoAir Q600 HRV, built as an AppDaemon application for Home Assistant.

## Core Directives
1. **Separation of Concerns:** This project MUST remain completely decoupled from the main `hapsic` codebase. It is a read-only, passive monitoring system. **Do not** add active control code (like changing fan speeds or toggling bypass) to this repository.
2. **Physics-Based Metrics:** The core premise of this project is that pressure differential scales non-linearly with airflow. Filter degradation must be measured using **speed-independent metrics** like specific fan power (SFP) and duty ratio (Supply Duty / Exhaust Duty), rather than raw duty percentage gaps. Maintain this strict physical approach in any future updates.
3. **Conditioned Sampling:** Trend analysis (the 7-day linear regression) must only incorporate data sampled under steady-state conditions (e.g., fan level "Low", bypass disabled, negligible flow imbalance). Transient events (like bathroom boosts or defrost cycles) must be explicitly filtered out before updating ring buffers.
4. **AppDaemon Architecture:** 
  - Application logic: `apps/zehnder_monitor/zehnder_monitor.py`
  - State persistence (baselines, trends) is handled via local `*.json` files in the app directory.

## Deployment
- This repo is **HACS-managed** as an AppDaemon app (category: `appdaemon`).
- HACS repo registration: `astyrrian1/zehnder-monitor`
- HACS deploys the entire `apps/zehnder_monitor/` directory (Python module + `apps.yaml`) into AppDaemon's apps folder.
- AppDaemon auto-discovers `apps.yaml` files in subdirectories — no manual binding needed.
- The `apps.yaml` **must** be included in this repo so HACS deploys it. Without it, AppDaemon won't load the module.

## Release Management — MANDATORY
Every push to `main` **MUST** be accompanied by:
1. A **semver git tag** (e.g., `v1.2.0`) — HACS tracks releases via tags.
2. A **GitHub Release** with release notes summarizing what changed.
3. Use the `/release` workflow for the exact steps.

**Versioning rules:**
- Patch (`v1.1.x`): docs-only changes, minor fixes
- Minor (`v1.x.0`): new features, behavioural changes, config changes
- Major (`vX.0.0`): breaking changes requiring user migration

**Never push to `main` without a tagged release.** HACS users will not receive update notifications without one.

## Maintenance Guidelines
- Ensure that the syntax is compatible with AppDaemon 4.x.
- Remember to use `self.log()`, `self.get_state()`, `self.set_state()`, and `self.call_service()` provided by the `hass.Hass` base class.
- Retain all telemetry MQTT publishing to `zehnder/monitor/state` as external data sinks may rely on it.
- **Alerting logic belongs in native HA** (Threshold Helpers + Automations), not in this Python code. AppDaemon is the math/sensor engine only.
