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

## Maintenance Guidelines
- Ensure that the syntax is compatible with AppDaemon 4.x.
- Remember to use `self.log()`, `self.get_state()`, `self.set_state()`, and `self.call_service()` provided by the `hass.Hass` base class.
- Retain all telemetry MQTT publishing to `zehnder/monitor/state` as external data sinks may rely on it.
