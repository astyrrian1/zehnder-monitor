"""
Zehnder ComfoAir Q600 — Physics-Based Filter & Performance Monitor
==================================================================

Uses fan duty ratios, Specific Fan Power (SFP), and RPM-per-flow
analysis to detect filter degradation beyond the unit's countdown timer.

The ComfoAir Q is a VOLUME-FLOW-CONSTANT HRV. As filters clog:
    dP_filter up -> Fan duty up -> RPM up -> Power up -> SFP up

Filter grade asymmetry matters:
    - Supply side: F7 (fine) -> higher base resistance
    - Exhaust side: G4 (coarse) -> lower base resistance
    - dP scales with Q^2 (turbulent flow through media)
    - Therefore absolute duty gap WIDENS at higher fan speeds
    - We use DUTY RATIO (supply/exhaust) for speed-independent comparison

Completely standalone. No dependency on or awareness of HAPSIC.
"""

import appdaemon.plugins.hass.hassapi as hass
import json
import os
import statistics
import time
import importlib.util
from datetime import datetime, timezone

try:
    from capability import compute_capability
except ImportError:
    try:
        from .capability import compute_capability
    except ImportError:
        _capability_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "capability.py"
        )
        _capability_spec = importlib.util.spec_from_file_location(
            "zehnder_monitor_capability", _capability_path
        )
        _capability_module = importlib.util.module_from_spec(_capability_spec)
        _capability_spec.loader.exec_module(_capability_module)
        compute_capability = _capability_module.compute_capability


class ZehnderMonitor(hass.Hass):

    # === ENTITY MAP ===
    E = {
        "power":         "sensor.zehnder_comfoair_q_a4cb9c_power",
        "supply_flow":   "sensor.zehnder_comfoair_q_a4cb9c_supply_fan_flow",
        "exhaust_flow":  "sensor.zehnder_comfoair_q_a4cb9c_exhaust_fan_flow",
        "supply_duty":   "sensor.zehnder_comfoair_q_a4cb9c_supply_fan_duty",
        "exhaust_duty":  "sensor.zehnder_comfoair_q_a4cb9c_exhaust_fan_duty",
        "supply_rpm":    "sensor.zehnder_comfoair_q_a4cb9c_supply_fan_speed",
        "exhaust_rpm":   "sensor.zehnder_comfoair_q_a4cb9c_exhaust_fan_speed",
        "fan_level":     "sensor.zehnder_comfoair_q_a4cb9c_fan_level",
        "bypass":        "sensor.zehnder_comfoair_q_a4cb9c_bypass_state",
        "filter_days":   "sensor.zehnder_comfoair_q_a4cb9c_filter_replacement_remaining_days",
        "supply_temp":   "sensor.zehnder_comfoair_q_a4cb9c_supply_air_temperature",
        "outdoor_temp":  "sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_temperature",
        "extract_temp":  "sensor.zehnder_comfoair_q_a4cb9c_extract_air_temperature",
        "exhaust_temp":  "sensor.zehnder_comfoair_q_a4cb9c_exhaust_air_temperature",
        "status":        "binary_sensor.zehnder_comfoair_q_a4cb9c_status",
        "wifi":          "sensor.zehnder_comfoair_q_a4cb9c_wifi_signal",
        "energy_ytd":    "sensor.zehnder_comfoair_q_a4cb9c_energy_ytd",
        "avoided_heat":  "sensor.zehnder_comfoair_q_a4cb9c_avoided_heating_actual",
        "avoided_cool":  "sensor.zehnder_comfoair_q_a4cb9c_avoided_cooling_actual",
    }

    # === PHYSICS THRESHOLDS ===
    SFP_PRISTINE  = 0.35    # kW/(m3/s) best-case clean filters
    SFP_REPLACE   = 0.80    # kW/(m3/s) replace at this level

    # Duty ratio (supply/exhaust) -- speed-independent differential loading.
    # dP proportional to Q^2 means absolute duty gap widens at higher speeds,
    # but the ratio stays stable for a given filter state.
    RATIO_PRISTINE = 1.20   # Clean filters, minimal differential
    RATIO_REPLACE  = 2.50   # One side critically overloaded

    FILTER_CYCLE   = 180    # Nominal days between filter changes

    # === TIMING ===
    TICK_SECONDS   = 60
    BUFFER_HOURS   = 168    # 7-day conditioned sample window
    HEALTH_HOURS   = 24     # Recent conditioned window for headline metrics
    MIN_HEALTH_SAMPLES = 5
    STABLE_TICKS_REQUIRED = 3
    STABILITY_TOLERANCE = 0.08
    SAMPLE_FAN_LEVELS = ("Low", "Medium")
    TEMP_MAX_AGE_SECONDS = 600
    BASELINE_VERSION = 2
    BASELINE_MIN_CONDITIONED_SAMPLES = 20
    BASELINE_CANDIDATE_HOURS = 24
    BASELINE_LEARNING_MIN_DAYS = FILTER_CYCLE - 14
    STATE_FILE     = "state.json"
    BASELINE_FILE  = "baselines.json"

    # =================================================================
    # INIT
    # =================================================================

    def initialize(self):
        self.log("=" * 60)
        self.log("ZEHNDER MONITOR v1.8.0 -- Physics-Based Filter Health")
        self.log("=" * 60)

        self.sfp = 0.0
        self.duty_ratio = 1.0
        self.duty_asymmetry_abs = 0.0
        self.supply_rpm_per_flow = 0.0
        self.exhaust_rpm_per_flow = 0.0
        self.heat_recovery_eta = 0.0
        self.heat_recovery_raw = None
        self.heat_recovery_quality = "unavailable"
        self.health_score = 100.0
        self.sfp_trend_slope = 0.0
        self.health_sfp = 0.0
        self.health_duty_ratio = 1.0
        self.capability = {}
        self.sample_quality = "warming_up"
        self.last_conditioned_sample_at = None
        self.previous_sample_context = None
        self.stable_tick_count = 0

        self.sfp_buffer = []
        self.ratio_buffer = []
        self.rpm_ratio_buffer = []
        self.heat_recovery_buffer = []
        self.baseline_candidate_buffer = []

        self.last_filter_days = None
        self.baseline_timer = None
        self.tick_count = 0
        self.discovery_published = False

        loaded_baselines = self._load_json(self.BASELINE_FILE, self._defaults())
        self.baselines = self._migrate_baselines(loaded_baselines)
        if self.baselines != loaded_baselines:
            self._save_json(self.BASELINE_FILE, self.baselines)
        saved = self._load_json(self.STATE_FILE, {})
        self._restore(saved)

        self.log(
            f"Baselines: SFP={self.baselines['sfp']:.3f}, "
            f"Ratio={self.baselines['duty_ratio']:.2f}, "
            f"captured={self.baselines.get('captured_at', 'never')}"
        )
        self.log(f"Restored {len(self.sfp_buffer)} conditioned SFP samples.")

        self.run_every(self._tick, "now", self.TICK_SECONDS)

    # =================================================================
    # PERSISTENCE
    # =================================================================

    def _defaults(self):
        return {
            "version": self.BASELINE_VERSION,
            "sfp": 0.45, "duty_ratio": 1.50,
            "supply_duty": 40.0, "exhaust_duty": 27.0,
            "supply_rpm_per_flow": 8.0,
            "captured_at": None, "filter_days_at_capture": None,
            "per_fan_level": {},
        }

    def _dir(self):
        return os.path.dirname(os.path.abspath(__file__))

    def _load_json(self, name, defaults):
        path = os.path.join(self._dir(), name)
        try:
            with open(path, "r") as f:
                return {**defaults, **json.load(f)}
        except (FileNotFoundError, json.JSONDecodeError):
            return dict(defaults)

    def _save_json(self, name, data):
        path = os.path.join(self._dir(), name)
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            self.log(f"Save {name} failed: {e}", level="ERROR")

    def _persist(self):
        cutoff = time.time() - (self.BUFFER_HOURS * 3600)
        baseline_cutoff = time.time() - (self.BASELINE_CANDIDATE_HOURS * 3600)
        self._save_json(self.STATE_FILE, {
            "sfp_buffer": [(t, v) for t, v in self.sfp_buffer if t > cutoff],
            "ratio_buffer": [(t, v) for t, v in self.ratio_buffer if t > cutoff],
            "rpm_ratio_buffer": [(t, v) for t, v in self.rpm_ratio_buffer if t > cutoff],
            "heat_recovery_buffer": [(t, v) for t, v in self.heat_recovery_buffer if t > cutoff],
            "baseline_candidate_buffer": [
                sample for sample in self.baseline_candidate_buffer
                if sample.get("time", 0) > baseline_cutoff
            ],
            "last_filter_days": self.last_filter_days,
            "last_conditioned_sample_at": self.last_conditioned_sample_at,
            "saved_at": datetime.now().isoformat(),
        })

    def _restore(self, s):
        if not s:
            return
        self.sfp_buffer = s.get("sfp_buffer", [])
        self.ratio_buffer = s.get("ratio_buffer", [])
        self.rpm_ratio_buffer = s.get("rpm_ratio_buffer", [])
        self.heat_recovery_buffer = s.get("heat_recovery_buffer", [])
        self.baseline_candidate_buffer = s.get("baseline_candidate_buffer", [])
        self.last_filter_days = s.get("last_filter_days")
        self.last_conditioned_sample_at = s.get("last_conditioned_sample_at")

    # =================================================================
    # BASELINES
    # =================================================================

    def _migrate_baselines(self, baselines):
        data = {**self._defaults(), **(baselines or {})}
        data["version"] = self.BASELINE_VERSION
        per_fan_level = data.get("per_fan_level") or {}

        fan_level = data.get("fan_level_at_capture")
        if (
            fan_level
            and fan_level not in per_fan_level
            and data.get("captured_at")
        ):
            per_fan_level[fan_level] = self._baseline_snapshot(
                data,
                quality=data.get("baseline_quality") or data.get("quality") or "single_sample",
                sample_count=data.get("sample_count", 1),
            )

        data["per_fan_level"] = per_fan_level
        return data

    def _baseline_snapshot(self, source, quality, sample_count):
        return {
            "sfp": source.get("sfp"),
            "duty_ratio": source.get("duty_ratio"),
            "supply_duty": source.get("supply_duty"),
            "exhaust_duty": source.get("exhaust_duty"),
            "supply_rpm_per_flow": source.get("supply_rpm_per_flow"),
            "fan_level_at_capture": source.get("fan_level_at_capture"),
            "captured_at": source.get("captured_at"),
            "filter_days_at_capture": source.get("filter_days_at_capture"),
            "baseline_quality": quality,
            "sample_count": sample_count,
        }

    def _valid_baseline(self, baseline):
        try:
            return (
                baseline
                and 0 < float(baseline.get("sfp", 0)) < self.SFP_REPLACE
                and 0 < float(baseline.get("duty_ratio", 0)) < self.RATIO_REPLACE
            )
        except (TypeError, ValueError):
            return False

    def _baseline_for(self, r):
        fan_level = r.get("fan_level")
        per_fan_level = self.baselines.get("per_fan_level") or {}
        fan_baseline = per_fan_level.get(fan_level)

        if self._valid_baseline(fan_baseline):
            return fan_baseline, fan_baseline.get("baseline_quality", "single_sample")

        learning = any(
            sample.get("fan_level") == fan_level
            for sample in self.baseline_candidate_buffer
        )

        if self._valid_baseline(self.baselines):
            quality = (
                self.baselines.get("baseline_quality")
                or self.baselines.get("quality")
                or ("single_sample" if self.baselines.get("captured_at") else None)
            )
            if quality is None:
                quality = "learning" if learning else "fallback"
            return self.baselines, quality

        return self._defaults(), "learning" if learning else "invalid"

    def _baseline_capability(self, r):
        baseline, quality = self._baseline_for(r)
        capability = compute_capability(
            self.health_sfp,
            self.health_duty_ratio,
            baseline.get("sfp"),
            baseline.get("duty_ratio"),
            self.SFP_PRISTINE,
            self.SFP_REPLACE,
            self.RATIO_REPLACE,
            quality,
        )
        capability["baseline_fan_level"] = baseline.get("fan_level_at_capture")
        capability["baseline_sfp"] = baseline.get("sfp")
        capability["baseline_duty_ratio"] = baseline.get("duty_ratio")
        return capability

    def _capture_payload(self, r, quality, sample_count):
        return {
            "version": self.BASELINE_VERSION,
            "sfp": round(self.sfp, 4),
            "duty_ratio": round(self.duty_ratio, 3),
            "supply_duty": round(r["supply_duty"], 1),
            "exhaust_duty": round(r["exhaust_duty"], 1),
            "supply_rpm_per_flow": round(self.supply_rpm_per_flow, 3),
            "fan_level_at_capture": r.get("fan_level", "?"),
            "captured_at": datetime.now().isoformat(),
            "filter_days_at_capture": r.get("filter_days", 0),
            "baseline_quality": quality,
            "sample_count": sample_count,
        }

    # =================================================================
    # SENSOR I/O
    # =================================================================

    def _f(self, eid, default=None):
        try:
            v = self.get_state(eid)
            if v in (None, "unavailable", "unknown", ""):
                return default
            return float(v)
        except (ValueError, TypeError):
            return default

    def _age_seconds(self, eid):
        try:
            data = self.get_state(eid, attribute="all")
            stamp = (
                data.get("last_reported")
                or data.get("last_updated")
                or data.get("last_changed")
            )
            if not stamp:
                return None
            ts = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            now = datetime.now(ts.tzinfo or timezone.utc)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return max(0, (now - ts).total_seconds())
        except Exception:
            return None

    def _s(self, eid, default=""):
        v = self.get_state(eid)
        return default if v in (None, "unavailable", "unknown") else str(v)

    def _read(self):
        if self.get_state(self.E["status"]) != "on":
            return None
        r = {}
        r["power"]       = self._f(self.E["power"])
        r["supply_flow"] = self._f(self.E["supply_flow"])
        r["exhaust_flow"]= self._f(self.E["exhaust_flow"])
        r["supply_duty"] = self._f(self.E["supply_duty"])
        r["exhaust_duty"]= self._f(self.E["exhaust_duty"])
        r["supply_rpm"]  = self._f(self.E["supply_rpm"])
        r["exhaust_rpm"] = self._f(self.E["exhaust_rpm"])
        r["fan_level"]   = self._s(self.E["fan_level"])
        r["bypass"]      = self._f(self.E["bypass"], 0.0)
        r["filter_days"] = self._f(self.E["filter_days"])
        r["supply_temp"] = self._f(self.E["supply_temp"])
        r["outdoor_temp"]= self._f(self.E["outdoor_temp"])
        r["extract_temp"]= self._f(self.E["extract_temp"])
        r["exhaust_temp"]= self._f(self.E["exhaust_temp"])
        r["temp_ages"]   = {
            "supply_temp": self._age_seconds(self.E["supply_temp"]),
            "outdoor_temp": self._age_seconds(self.E["outdoor_temp"]),
            "extract_temp": self._age_seconds(self.E["extract_temp"]),
            "exhaust_temp": self._age_seconds(self.E["exhaust_temp"]),
        }
        r["wifi"]        = self._f(self.E["wifi"])
        r["energy_ytd"]  = self._f(self.E["energy_ytd"])
        r["avoided_heat"]= self._f(self.E["avoided_heat"])
        r["avoided_cool"]= self._f(self.E["avoided_cool"])
        for k in ("power", "supply_flow", "exhaust_flow", "supply_duty", "exhaust_duty"):
            if r[k] is None:
                self.log(f"Missing critical: {k}", level="WARNING")
                return None
        return r

    # =================================================================
    # PHYSICS
    # =================================================================

    def _compute(self, r):
        # SFP: kW per m3/s total air moved
        q = (r["supply_flow"] + r["exhaust_flow"]) / 3600.0
        self.sfp = (r["power"] / 1000.0) / q if q > 0.01 else 0.0

        # Duty ratio: speed-independent differential loading
        # dP proportional to Q^2 through filter media, so absolute duty gap
        # naturally widens at higher fan speeds. Ratio normalises this.
        if r["exhaust_duty"] > 5.0:
            self.duty_ratio = r["supply_duty"] / r["exhaust_duty"]
        else:
            self.duty_ratio = 1.0

        self.duty_asymmetry_abs = r["supply_duty"] - r["exhaust_duty"]

        # RPM per unit flow -- direct impeller resistance proxy
        self.supply_rpm_per_flow = (
            r["supply_rpm"] / r["supply_flow"]
            if r["supply_flow"] and r["supply_flow"] > 10 else 0.0
        )
        self.exhaust_rpm_per_flow = (
            r["exhaust_rpm"] / r["exhaust_flow"]
            if r["exhaust_flow"] and r["exhaust_flow"] > 10 else 0.0
        )

        # Heat recovery is only meaningful from fresh, synchronous temperatures.
        self.heat_recovery_raw = None
        if (r["supply_temp"] is not None and r["outdoor_temp"] is not None
                and r["extract_temp"] is not None):
            dt = r["extract_temp"] - r["outdoor_temp"]
            if (r["bypass"] < 5.0 and abs(dt) > 5.0
                    and self._temperatures_fresh(r)):
                eta = ((r["supply_temp"] - r["outdoor_temp"]) / dt) * 100.0
                self.heat_recovery_raw = max(0.0, min(120.0, eta))

    def _temperatures_fresh(self, r):
        ages = r.get("temp_ages", {})
        required = ("supply_temp", "outdoor_temp", "extract_temp")
        return all(
            ages.get(k) is not None and ages[k] <= self.TEMP_MAX_AGE_SECONDS
            for k in required
        )

    # =================================================================
    # CONDITIONED SAMPLING
    # =================================================================

    def _sample(self, r):
        """Record metrics only under steady, comparable conditions."""
        now = time.time()

        if r["supply_flow"] < 1:
            return

        imbal = abs(r["supply_flow"] - r["exhaust_flow"]) / r["supply_flow"]

        if self._steady_sample_ready(r, imbal):
            self.sfp_buffer.append((now, self.sfp))
            self.ratio_buffer.append((now, self.duty_ratio))
            self.rpm_ratio_buffer.append((now, self.supply_rpm_per_flow))
            if self.heat_recovery_raw is not None:
                self.heat_recovery_buffer.append((now, self.heat_recovery_raw))
            self.last_conditioned_sample_at = now
            self._record_baseline_candidate(r, now)

        # Prune to window
        cutoff = now - (self.BUFFER_HOURS * 3600)
        self.sfp_buffer = [(t, v) for t, v in self.sfp_buffer if t > cutoff]
        self.ratio_buffer = [(t, v) for t, v in self.ratio_buffer if t > cutoff]
        self.rpm_ratio_buffer = [(t, v) for t, v in self.rpm_ratio_buffer if t > cutoff]
        self.heat_recovery_buffer = [
            (t, v) for t, v in self.heat_recovery_buffer if t > cutoff
        ]

        self._conditioned_metrics(now)
        self.sfp_trend_slope = (
            self._slope(self.sfp_buffer) if len(self.sfp_buffer) >= 20 else 0.0
        )

    def _record_baseline_candidate(self, r, now):
        days = r.get("filter_days")
        fan_level = r.get("fan_level")
        if (
            days is None
            or days < self.BASELINE_LEARNING_MIN_DAYS
            or fan_level not in self.SAMPLE_FAN_LEVELS
        ):
            return

        self.baseline_candidate_buffer.append({
            "time": now,
            "fan_level": fan_level,
            "sfp": self.sfp,
            "duty_ratio": self.duty_ratio,
            "supply_duty": r.get("supply_duty"),
            "exhaust_duty": r.get("exhaust_duty"),
            "supply_rpm_per_flow": self.supply_rpm_per_flow,
            "filter_days": days,
        })

        cutoff = now - (self.BASELINE_CANDIDATE_HOURS * 3600)
        self.baseline_candidate_buffer = [
            sample for sample in self.baseline_candidate_buffer
            if sample.get("time", 0) > cutoff
        ]
        self._maybe_promote_conditioned_baseline(fan_level)

    def _maybe_promote_conditioned_baseline(self, fan_level):
        samples = [
            sample for sample in self.baseline_candidate_buffer
            if sample.get("fan_level") == fan_level
        ]
        if len(samples) < self.BASELINE_MIN_CONDITIONED_SAMPLES:
            return

        per_fan_level = self.baselines.setdefault("per_fan_level", {})
        existing = per_fan_level.get(fan_level)
        if existing and existing.get("baseline_quality") == "conditioned":
            return

        source = {
            "sfp": round(statistics.median(s["sfp"] for s in samples), 4),
            "duty_ratio": round(statistics.median(s["duty_ratio"] for s in samples), 3),
            "supply_duty": round(statistics.median(s["supply_duty"] for s in samples), 1),
            "exhaust_duty": round(statistics.median(s["exhaust_duty"] for s in samples), 1),
            "supply_rpm_per_flow": round(
                statistics.median(s["supply_rpm_per_flow"] for s in samples), 3
            ),
            "fan_level_at_capture": fan_level,
            "captured_at": datetime.now().isoformat(),
            "filter_days_at_capture": round(
                statistics.median(s["filter_days"] for s in samples), 1
            ),
        }
        snapshot = self._baseline_snapshot(
            source, quality="conditioned", sample_count=len(samples)
        )
        if not self._valid_baseline(snapshot):
            return

        per_fan_level[fan_level] = snapshot
        self.baselines.update(source)
        self.baselines["version"] = self.BASELINE_VERSION
        self.baselines["baseline_quality"] = "conditioned"
        self.baselines["sample_count"] = len(samples)
        self.baselines["per_fan_level"] = per_fan_level
        self._save_json(self.BASELINE_FILE, self.baselines)
        self.log(
            f"Conditioned clean-filter baseline promoted for {fan_level}: "
            f"SFP={source['sfp']:.3f}, Ratio={source['duty_ratio']:.2f}"
        )

    def _recent_values(self, buf, now, hours):
        cutoff = now - (hours * 3600)
        return [v for t, v in buf if t > cutoff]

    def _conditioned_metrics(self, now):
        sfp_recent = self._recent_values(self.sfp_buffer, now, self.HEALTH_HOURS)
        ratio_recent = self._recent_values(self.ratio_buffer, now, self.HEALTH_HOURS)
        heat_recent = self._recent_values(
            self.heat_recovery_buffer, now, self.HEALTH_HOURS
        )

        if len(sfp_recent) >= self.MIN_HEALTH_SAMPLES:
            self.health_sfp = statistics.median(sfp_recent)
            self.health_duty_ratio = statistics.median(ratio_recent)
            self.sample_quality = "conditioned"
            if len(heat_recent) >= self.MIN_HEALTH_SAMPLES:
                self.heat_recovery_eta = statistics.median(heat_recent)
                self.heat_recovery_quality = "conditioned"
            else:
                self.heat_recovery_quality = "unavailable"
            return

        if len(self.sfp_buffer) >= self.MIN_HEALTH_SAMPLES:
            self.health_sfp = statistics.median(v for _, v in self.sfp_buffer)
            self.health_duty_ratio = statistics.median(v for _, v in self.ratio_buffer)
            self.sample_quality = "conditioned_stale"
            if len(self.heat_recovery_buffer) >= self.MIN_HEALTH_SAMPLES:
                self.heat_recovery_eta = statistics.median(
                    v for _, v in self.heat_recovery_buffer
                )
                self.heat_recovery_quality = "conditioned_stale"
            else:
                self.heat_recovery_quality = "unavailable"
            return

        self.health_sfp = self.sfp
        self.health_duty_ratio = self.duty_ratio
        self.sample_quality = "live_fallback"
        self.heat_recovery_quality = "unavailable"

    def _steady_sample_ready(self, r, imbal):
        context = {
            "fan_level": r.get("fan_level", ""),
            "supply_flow": r.get("supply_flow"),
            "exhaust_flow": r.get("exhaust_flow"),
            "power": r.get("power"),
        }
        base_ok = (
            context["fan_level"] in self.SAMPLE_FAN_LEVELS
            and r.get("bypass", 100) < 5.0
            and r.get("power", 0) > 20.0
            and imbal < 0.10
            and self.sfp > 0.1
        )
        if not base_ok:
            self.previous_sample_context = context
            self.stable_tick_count = 0
            return False

        if self.previous_sample_context is None:
            self.previous_sample_context = context
            self.stable_tick_count = 1
            return False

        stable = (
            context["fan_level"] == self.previous_sample_context.get("fan_level")
            and self._within(context["supply_flow"], self.previous_sample_context.get("supply_flow"))
            and self._within(context["exhaust_flow"], self.previous_sample_context.get("exhaust_flow"))
            and self._within(context["power"], self.previous_sample_context.get("power"))
        )
        self.previous_sample_context = context
        self.stable_tick_count = self.stable_tick_count + 1 if stable else 1
        return self.stable_tick_count >= self.STABLE_TICKS_REQUIRED

    def _within(self, current, previous):
        if current is None or previous is None:
            return False
        if abs(previous) < 1e-6:
            return abs(current) < 1e-6
        return abs(current - previous) / abs(previous) <= self.STABILITY_TOLERANCE

    def _slope(self, buf):
        """Least-squares slope in units-per-day."""
        if len(buf) < 2:
            return 0.0
        n = len(buf)
        t0 = buf[0][0]
        xs = [(t - t0) / 86400.0 for t, _ in buf]
        ys = [v for _, v in buf]
        sx = sum(xs); sy = sum(ys)
        sxy = sum(x * y for x, y in zip(xs, ys))
        sx2 = sum(x * x for x in xs)
        d = n * sx2 - sx * sx
        return (n * sxy - sx * sy) / d if abs(d) > 1e-10 else 0.0

    # =================================================================
    # FILTER CHANGE DETECTION
    # =================================================================

    def _detect_change(self, r):
        days = r.get("filter_days")
        if days is None:
            return
        if self.last_filter_days is not None and days - self.last_filter_days > 90:
            self.log(
                f"FILTER CHANGE: {self.last_filter_days:.0f} -> {days:.0f} days",
                level="WARNING"
            )
            if self.baseline_timer:
                try:
                    self.cancel_timer(self.baseline_timer)
                except Exception:
                    pass
            self.baseline_timer = self.run_in(self._capture_baseline, 7200)
            self.sfp_buffer.clear()
            self.ratio_buffer.clear()
            self.rpm_ratio_buffer.clear()
            self.baseline_candidate_buffer.clear()
            self._notify(
                "Zehnder Filter Change Detected",
                "Timer reset detected. Baselines auto-capture in 2 hours.",
                "zehnder_filter_change"
            )
        self.last_filter_days = days

    def _capture_baseline(self, kwargs):
        self.log("Capturing clean-filter baselines...")
        r = self._read()
        if r is None:
            self.log("Sensors unavailable, retry in 30 min.", level="WARNING")
            self.baseline_timer = self.run_in(self._capture_baseline, 1800)
            return
        self._compute(r)
        captured = self._capture_payload(r, quality="single_sample", sample_count=1)
        self.baselines = self._migrate_baselines({
            **self.baselines,
            **captured,
            "per_fan_level": {
                **(self.baselines.get("per_fan_level") or {}),
                captured["fan_level_at_capture"]: self._baseline_snapshot(
                    captured, quality="single_sample", sample_count=1
                ),
            },
        })
        self._save_json(self.BASELINE_FILE, self.baselines)
        self._notify(
            "Zehnder Baselines Captured",
            f"SFP: {self.baselines['sfp']:.3f} kW/(m3/s)\n"
            f"Ratio: {self.baselines['duty_ratio']:.2f}x "
            f"({self.baselines['supply_duty']:.0f}%/{self.baselines['exhaust_duty']:.0f}%)\n"
            f"RPM/flow: {self.baselines['supply_rpm_per_flow']:.2f}",
            "zehnder_baseline"
        )

    # =================================================================
    # HEALTH SCORE
    # =================================================================

    def _health(self, r):
        """
        Composite 0-100 score.
          SFP        50% -- electrical cost per unit air
          Duty Ratio 30% -- speed-normalised differential loading
          Timer      20% -- sanity floor
        """
        sfp_s = max(0, min(100,
            (self.SFP_REPLACE - self.health_sfp) /
            (self.SFP_REPLACE - self.SFP_PRISTINE) * 100))
        rat_s = max(0, min(100,
            (self.RATIO_REPLACE - self.health_duty_ratio) /
            (self.RATIO_REPLACE - self.RATIO_PRISTINE) * 100))
        days = r.get("filter_days") or 0
        tim_s = max(0, min(100, days / self.FILTER_CYCLE * 100))
        self.health_score = round(sfp_s * 0.50 + rat_s * 0.30 + tim_s * 0.20, 1)
        self.capability = self._baseline_capability(r)

    # =================================================================
    # NOTIFICATIONS
    # =================================================================

    def _notify(self, title, msg, nid):
        try:
            self.call_service(
                "notify/notify", title=title, message=msg,
                data={"tag": nid}
            )
        except Exception as e:
            self.log(f"Push failed: {e}", level="WARNING")
        try:
            self.call_service(
                "persistent_notification/create",
                title=title, message=msg, notification_id=nid
            )
        except Exception as e:
            self.log(f"Persistent notif failed: {e}", level="WARNING")
        self.log(f"NOTIFY [{nid}] {title}")

    # =================================================================
    # HA SENSORS
    # =================================================================

    def _publish_sensors(self):
        sensors = [
            ("sensor.zehnder_sfp", round(self.sfp, 3), {
                "unit_of_measurement": "kW/(m³/s)",
                "state_class": "measurement", "icon": "mdi:speedometer",
                "friendly_name": "Zehnder SFP", "sfp_class": self._sfp_c(),
            }),
            ("sensor.zehnder_filter_health", round(self.health_score, 1), {
                "unit_of_measurement": "%",
                "state_class": "measurement", "icon": "mdi:air-filter",
                "friendly_name": "Zehnder Filter Health",
                "status": self._health_l(),
            }),
            ("sensor.zehnder_duty_ratio", round(self.duty_ratio, 3), {
                "state_class": "measurement",
                "icon": "mdi:arrow-split-vertical",
                "friendly_name": "Zehnder Duty Ratio",
                "absolute_asymmetry_pct": round(self.duty_asymmetry_abs, 1),
            }),
            ("sensor.zehnder_heat_recovery", round(self.heat_recovery_eta, 1), {
                "unit_of_measurement": "%",
                "state_class": "measurement", "icon": "mdi:heat-wave",
                "friendly_name": "Zehnder Heat Recovery",
            }),
            ("sensor.zehnder_sfp_trend", round(self.sfp_trend_slope * 1000, 2), {
                "unit_of_measurement": "mW/(m³/s)/day",
                "state_class": "measurement", "icon": "mdi:trending-up",
                "friendly_name": "Zehnder SFP Trend",
                "conditioned_samples_7d": len(self.sfp_buffer),
            }),
        ]
        for eid, val, attrs in sensors:
            try:
                self.set_state(eid, state=str(val), attributes=attrs)
            except Exception as e:
                self.log(f"set_state({eid}) failed: {e}", level="WARNING")

    # =================================================================
    # MQTT
    # =================================================================

    def _publish_mqtt_discovery(self):
        device = {
            "identifiers": ["zehnder_monitor"],
            "name": "Zehnder Monitor",
            "manufacturer": "Zehnder",
            "model": "ComfoAir Q600",
        }
        base = {
            "state_topic": "zehnder/monitor/state",
            "device": device,
        }
        sensors = [
            ("sfp", {
                "name": "Zehnder SFP",
                "unique_id": "zehnder_monitor_sfp",
                "default_entity_id": "sensor.zehnder_sfp",
                "unit_of_measurement": "kW/(m³/s)",
                "state_class": "measurement",
                "icon": "mdi:speedometer",
                "value_template": "{{ value_json.metrics.sfp }}",
                "availability_topic": "zehnder/monitor/state",
                "availability_template": "{{ 'online' if value_json.health.sample_quality != 'live_fallback' else 'offline' }}",
            }),
            ("filter_health", {
                "name": "Zehnder Filter Health",
                "unique_id": "zehnder_monitor_filter_health",
                "default_entity_id": "sensor.zehnder_filter_health",
                "unit_of_measurement": "%",
                "state_class": "measurement",
                "icon": "mdi:air-filter",
                "value_template": "{{ value_json.health.score }}",
                "availability_topic": "zehnder/monitor/state",
                "availability_template": "{{ 'online' if value_json.health.sample_quality != 'live_fallback' else 'offline' }}",
            }),
            ("duty_ratio", {
                "name": "Zehnder Duty Ratio",
                "unique_id": "zehnder_monitor_duty_ratio",
                "default_entity_id": "sensor.zehnder_duty_ratio",
                "state_class": "measurement",
                "icon": "mdi:arrow-split-vertical",
                "value_template": "{{ value_json.metrics.duty_ratio }}",
                "availability_topic": "zehnder/monitor/state",
                "availability_template": "{{ 'online' if value_json.health.sample_quality != 'live_fallback' else 'offline' }}",
            }),
            ("heat_recovery", {
                "name": "Zehnder Heat Recovery",
                "unique_id": "zehnder_monitor_heat_recovery",
                "default_entity_id": "sensor.zehnder_heat_recovery",
                "unit_of_measurement": "%",
                "state_class": "measurement",
                "icon": "mdi:heat-wave",
                "value_template": "{{ value_json.metrics.heat_recovery_eta }}",
                "availability_topic": "zehnder/monitor/state",
                "availability_template": "{{ 'online' if value_json.metrics.heat_recovery_quality == 'conditioned' else 'offline' }}",
            }),
            ("sfp_trend", {
                "name": "Zehnder SFP Trend",
                "unique_id": "zehnder_monitor_sfp_trend",
                "default_entity_id": "sensor.zehnder_sfp_trend",
                "unit_of_measurement": "mW/(m³/s)/day",
                "state_class": "measurement",
                "icon": "mdi:trending-up",
                "value_template": "{{ (value_json.health.sfp_trend_per_day | float(0) * 1000) | round(2) }}",
                "availability_topic": "zehnder/monitor/state",
                "availability_template": "{{ 'online' if value_json.health.conditioned_samples | int(0) >= 20 else 'offline' }}",
            }),
            ("sample_quality", {
                "name": "Zehnder Sample Quality",
                "unique_id": "zehnder_monitor_sample_quality",
                "default_entity_id": "sensor.zehnder_sample_quality",
                "icon": "mdi:check-decagram",
                "value_template": "{{ value_json.health.sample_quality }}",
            }),
            ("conditioned_samples", {
                "name": "Zehnder Conditioned Samples",
                "unique_id": "zehnder_monitor_conditioned_samples",
                "default_entity_id": "sensor.zehnder_conditioned_samples",
                "state_class": "measurement",
                "icon": "mdi:counter",
                "value_template": "{{ value_json.health.conditioned_samples }}",
            }),
            ("raw_sfp", {
                "name": "Zehnder Raw SFP",
                "unique_id": "zehnder_monitor_raw_sfp",
                "default_entity_id": "sensor.zehnder_raw_sfp",
                "unit_of_measurement": "kW/(m³/s)",
                "state_class": "measurement",
                "icon": "mdi:pulse",
                "value_template": "{{ value_json.raw.sfp }}",
            }),
            ("raw_heat_recovery", {
                "name": "Zehnder Raw Heat Recovery",
                "unique_id": "zehnder_monitor_raw_heat_recovery",
                "default_entity_id": "sensor.zehnder_raw_heat_recovery",
                "unit_of_measurement": "%",
                "state_class": "measurement",
                "icon": "mdi:heat-wave",
                "value_template": "{{ value_json.raw.heat_recovery_eta }}",
            }),
            ("heat_recovery_quality", {
                "name": "Zehnder Heat Recovery Quality",
                "unique_id": "zehnder_monitor_heat_recovery_quality",
                "default_entity_id": "sensor.zehnder_heat_recovery_quality",
                "icon": "mdi:thermometer-check",
                "value_template": "{{ value_json.metrics.heat_recovery_quality }}",
            }),
            ("filter_capacity_remaining", {
                "name": "Zehnder Filter Capacity Remaining",
                "unique_id": "zehnder_monitor_filter_capacity_remaining",
                "default_entity_id": "sensor.zehnder_filter_capacity_remaining",
                "unit_of_measurement": "%",
                "state_class": "measurement",
                "icon": "mdi:air-filter-check",
                "value_template": "{{ value_json.capability.filter_capacity_remaining }}",
                "availability_topic": "zehnder/monitor/state",
                "availability_template": "{{ 'online' if value_json.capability.baseline_quality in ['conditioned', 'single_sample'] and value_json.health.sample_quality != 'live_fallback' else 'offline' }}",
            }),
            ("sfp_capacity_remaining", {
                "name": "Zehnder SFP Capacity Remaining",
                "unique_id": "zehnder_monitor_sfp_capacity_remaining",
                "default_entity_id": "sensor.zehnder_sfp_capacity_remaining",
                "unit_of_measurement": "%",
                "state_class": "measurement",
                "icon": "mdi:speedometer",
                "value_template": "{{ value_json.capability.sfp_capacity_remaining }}",
                "availability_topic": "zehnder/monitor/state",
                "availability_template": "{{ 'online' if value_json.capability.baseline_quality in ['conditioned', 'single_sample'] and value_json.health.sample_quality != 'live_fallback' else 'offline' }}",
            }),
            ("duty_capacity_remaining", {
                "name": "Zehnder Duty Capacity Remaining",
                "unique_id": "zehnder_monitor_duty_capacity_remaining",
                "default_entity_id": "sensor.zehnder_duty_capacity_remaining",
                "unit_of_measurement": "%",
                "state_class": "measurement",
                "icon": "mdi:arrow-split-vertical",
                "value_template": "{{ value_json.capability.duty_capacity_remaining }}",
                "availability_topic": "zehnder/monitor/state",
                "availability_template": "{{ 'online' if value_json.capability.baseline_quality in ['conditioned', 'single_sample'] and value_json.health.sample_quality != 'live_fallback' else 'offline' }}",
            }),
            ("baseline_system_resistance", {
                "name": "Zehnder Baseline System Resistance",
                "unique_id": "zehnder_monitor_baseline_system_resistance",
                "default_entity_id": "sensor.zehnder_baseline_system_resistance",
                "unit_of_measurement": "%",
                "state_class": "measurement",
                "icon": "mdi:gauge",
                "value_template": "{{ value_json.capability.baseline_system_resistance }}",
                "availability_topic": "zehnder/monitor/state",
                "availability_template": "{{ 'online' if value_json.capability.baseline_quality in ['conditioned', 'single_sample'] else 'offline' }}",
            }),
            ("baseline_quality", {
                "name": "Zehnder Baseline Quality",
                "unique_id": "zehnder_monitor_baseline_quality",
                "default_entity_id": "sensor.zehnder_baseline_quality",
                "icon": "mdi:database-check",
                "value_template": "{{ value_json.capability.baseline_quality }}",
            }),
        ]

        for key, config in sensors:
            topic = f"homeassistant/sensor/zehnder_monitor/{key}/config"
            try:
                self.call_service(
                    "mqtt/publish", topic=topic,
                    payload=json.dumps({**base, **config}), retain=True
                )
            except Exception as e:
                self.log(f"MQTT discovery failed for {key}: {e}", level="WARNING")
                return False
        return True

    def _publish_mqtt(self, r):
        payload = {
            "timestamp": datetime.now().isoformat(),
            "unit_online": True,
            "metrics": {
                "sfp": round(self.health_sfp, 4),
                "sfp_class": self._sfp_c(self.health_sfp),
                "duty_ratio": round(self.health_duty_ratio, 3),
                "duty_asymmetry_pct": round(self.duty_asymmetry_abs, 1),
                "supply_rpm_per_flow": round(self.supply_rpm_per_flow, 3),
                "exhaust_rpm_per_flow": round(self.exhaust_rpm_per_flow, 3),
                "heat_recovery_eta": round(self.heat_recovery_eta, 1),
                "heat_recovery_quality": self.heat_recovery_quality,
            },
            "health": {
                "score": self.health_score, "status": self._health_l(),
                "sfp_trend_per_day": round(self.sfp_trend_slope, 6),
                "conditioned_samples": len(self.sfp_buffer),
                "sample_quality": self.sample_quality,
                "last_conditioned_sample_at": self.last_conditioned_sample_at,
            },
            "raw": {
                "sfp": round(self.sfp, 4),
                "duty_ratio": round(self.duty_ratio, 3),
                "heat_recovery_eta": (
                    round(self.heat_recovery_raw, 1)
                    if self.heat_recovery_raw is not None else None
                ),
                "power_w": r.get("power"),
                "supply_flow": r.get("supply_flow"),
                "exhaust_flow": r.get("exhaust_flow"),
                "supply_duty": r.get("supply_duty"),
                "exhaust_duty": r.get("exhaust_duty"),
                "supply_rpm": r.get("supply_rpm"),
                "exhaust_rpm": r.get("exhaust_rpm"),
                "fan_level": r.get("fan_level"),
                "bypass_pct": r.get("bypass"),
                "filter_days": r.get("filter_days"),
                "wifi_dbm": r.get("wifi"),
                "energy_ytd_kwh": r.get("energy_ytd"),
                "temp_age_seconds": r.get("temp_ages", {}),
            },
            "capability": self.capability,
            "baselines": self.baselines,
        }
        try:
            self.call_service(
                "mqtt/publish", topic="zehnder/monitor/state",
                payload=json.dumps(payload), retain=True
            )
        except Exception as e:
            self.log(f"MQTT failed: {e}", level="WARNING")

    # =================================================================
    # LABELS
    # =================================================================

    def _sfp_c(self, sfp=None):
        sfp = self.sfp if sfp is None else sfp
        if sfp < 0.50: return "SFP 1 (Excellent)"
        if sfp < 0.75: return "SFP 2 (Good)"
        if sfp < 1.25: return "SFP 3 (Fair)"
        return "SFP 4 (Poor)"

    def _health_l(self):
        if self.health_score >= 80: return "Healthy"
        if self.health_score >= 60: return "Good"
        if self.health_score >= 30: return "Degraded"
        if self.health_score >= 10: return "Poor"
        return "Critical"

    # =================================================================
    # MASTER TICK
    # =================================================================

    def _tick(self, kwargs):
        self.tick_count += 1
        r = self._read()
        if r is None:
            if self.tick_count % 10 == 0:
                self.log("Unit offline.", level="WARNING")
            return

        self._compute(r)
        self._sample(r)
        self._detect_change(r)
        self._health(r)
        if not self.discovery_published:
            self.discovery_published = self._publish_mqtt_discovery()
        self._publish_mqtt(r)

        if self.tick_count % 5 == 0:
            self._persist()
            self.log(
                f"[HB] Health:{self.health_score:.0f}% ({self._health_l()}) | "
                f"SFP:{self.health_sfp:.3f} ({self._sfp_c(self.health_sfp)}) | "
                f"Raw:{self.sfp:.3f} | Ratio:{self.health_duty_ratio:.2f}x | "
                f"Capacity:{self.capability.get('filter_capacity_remaining')}% | "
                f"Fan:{r['fan_level']} | eta:{self.heat_recovery_eta:.0f}% | "
                f"Filter:{r.get('filter_days','?')}d | "
                f"Buf:{len(self.sfp_buffer)} | Quality:{self.sample_quality}"
            )
