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
import math
import hashlib
from datetime import datetime, timedelta, timezone

try:
    from corrected import UNITS, _validate_dynamic, evaluate_sfp, evaluate_fan_effort, evaluate_recovery_inputs, evaluate_sampling_eligibility, select_reference, conditioned_comparison, hourly_sfp_trend, conditioned_recovery
except ImportError:
    from .corrected import UNITS, _validate_dynamic, evaluate_sfp, evaluate_fan_effort, evaluate_recovery_inputs, evaluate_sampling_eligibility, select_reference, conditioned_comparison, hourly_sfp_trend, conditioned_recovery

try:
    from capability import (
        CAPACITY_REMAINING_KEYS,
        compute_capability,
        floor_capacity_payload,
    )
except ImportError:
    try:
        from .capability import (
            CAPACITY_REMAINING_KEYS,
            compute_capability,
            floor_capacity_payload,
        )
    except ImportError:
        _capability_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "capability.py"
        )
        _capability_spec = importlib.util.spec_from_file_location(
            "zehnder_monitor_capability", _capability_path
        )
        _capability_module = importlib.util.module_from_spec(_capability_spec)
        _capability_spec.loader.exec_module(_capability_module)
        CAPACITY_REMAINING_KEYS = _capability_module.CAPACITY_REMAINING_KEYS
        compute_capability = _capability_module.compute_capability
        floor_capacity_payload = _capability_module.floor_capacity_payload


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
    TRUSTED_SAMPLE_QUALITIES = ("conditioned", "conditioned_stale")
    TEMP_MAX_AGE_SECONDS = 600
    BASELINE_VERSION = 2
    BASELINE_MIN_CONDITIONED_SAMPLES = 20
    BASELINE_CANDIDATE_HOURS = 24
    BASELINE_LEARNING_MIN_DAYS = FILTER_CYCLE - 14
    STATE_FILE     = "state.json"
    BASELINE_FILE  = "baselines.json"
    CORRECTED_FILE = "corrected_v2.json"
    DATA_DIR_ARG   = "data_dir"

    # =================================================================
    # INIT
    # =================================================================

    def initialize(self):
        self.log("=" * 60)
        if self.args.get("isolated_test_mode"):
            self.TICK_SECONDS = int(self.args.get("test_tick_seconds", self.TICK_SECONDS))
        self.log("ZEHNDER MONITOR v2 -- Corrected Readings and Legacy Compatibility")
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
        self.instant_health_score = 100.0
        self.health_floor = None
        self.sfp_trend_slope = 0.0
        self.health_sfp = 0.0
        self.health_duty_ratio = 1.0
        self.capability = {}
        self.capacity_floor = {}
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
        self.v2_discovery_published = False
        self.v2_last_fingerprint = None
        self.v2_accepted_reports = 0
        self.v2_recent_samples = []
        self.v2_comparison_samples = []
        self.v2_recovery_samples = []

        loaded_baselines = self._load_json(self.BASELINE_FILE, self._defaults())
        self.baselines = self._migrate_baselines(loaded_baselines)
        if self.baselines != loaded_baselines:
            self._save_json(self.BASELINE_FILE, self.baselines)
        saved = self._load_json(self.STATE_FILE, {})
        self._restore(saved)
        self.v2_state = self._load_corrected_state()

        self.log(
            f"Baselines: SFP={self.baselines['sfp']:.3f}, "
            f"Ratio={self.baselines['duty_ratio']:.2f}, "
            f"captured={self.baselines.get('captured_at', 'never')}"
        )
        self.log(f"Restored {len(self.sfp_buffer)} conditioned SFP samples.")

        handle = self.run_every(self._tick, "now", self.TICK_SECONDS)
        self.log(f"Evaluation scheduled every {self.TICK_SECONDS}s: {handle}")
        self.listen_event(self._on_clean_filters_confirmed, "zehnder_monitor_clean_filters_confirmed")
        self.listen_state(self._on_clean_filter_button, "input_button.zehnder_confirm_clean_filters")

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

    def _data_dir(self):
        args = getattr(self, "args", {}) or {}
        configured = args.get(self.DATA_DIR_ARG) or args.get("persistence_dir")
        app_dir = os.path.dirname(self._dir())
        config_dir = os.path.dirname(app_dir)
        if configured:
            configured = os.path.expanduser(configured)
            if not os.path.isabs(configured):
                configured = os.path.join(config_dir, configured)
            return os.path.abspath(configured)

        return os.path.join(config_dir, "zehnder-monitor")

    def _json_paths(self, name):
        primary = os.path.join(self._data_dir(), name)
        legacy = os.path.join(self._dir(), name)
        if os.path.abspath(primary) == os.path.abspath(legacy):
            return [primary]
        return [primary, legacy]

    def _load_json(self, name, defaults):
        paths = self._json_paths(name)
        primary = paths[0]

        for path in paths:
            try:
                with open(path, "r") as f:
                    data = {**defaults, **json.load(f)}
            except FileNotFoundError:
                continue
            except json.JSONDecodeError as e:
                self.log(f"Load {name} failed from {path}: {e}", level="ERROR")
                continue

            if path != primary:
                self.log(f"Migrating {name} from legacy app directory to {primary}.")
                self._save_json(name, data)
            return data

        return dict(defaults)

    def _save_json(self, name, data):
        paths = self._json_paths(name)
        errors = []
        for path in paths:
            directory = os.path.dirname(path)
            tmp_path = f"{path}.tmp"
            try:
                os.makedirs(directory, exist_ok=True)
                with open(tmp_path, "w") as f:
                    json.dump(data, f, indent=2, default=str)
                os.replace(tmp_path, path)
                return
            except Exception as e:
                errors.append(f"{path}: {e}")
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass

        try:
            joined = "; ".join(errors)
            self.log(f"Save {name} failed: {joined}", level="ERROR")
        except Exception as e:
            self.log(f"Save {name} failed: {e}", level="ERROR")

    @staticmethod
    def _corrected_defaults():
        return {
            "schema_version": 2,
            "calibration": {
                "state": "awaiting_confirmation",
                "cycle_id": None,
                "confirmed_at": None,
                "settling_until": None,
                "learning_until": None,
                "references": {},
                "candidates": {},
            },
            "archived_references": [],
            "last_filter_days": None,
            "timer_confirmation_requested": False,
            "trend_reports": [],
        }

    def _load_corrected_state(self):
        """Read only the v2 file; never infer a reference from legacy data."""
        path = os.path.join(self._data_dir(), self.CORRECTED_FILE)
        try:
            with open(path, "r") as stream:
                data = json.load(stream)
        except FileNotFoundError:
            defaults = self._corrected_defaults()
            self._save_json(self.CORRECTED_FILE, defaults)
            return defaults
        except (OSError, json.JSONDecodeError) as exc:
            self.log(f"Corrected state unavailable at {path}: {exc}", level="ERROR")
            return self._corrected_defaults()
        calibration = data.get("calibration") if isinstance(data, dict) else None
        if (not isinstance(data, dict) or data.get("schema_version") != 2 or not isinstance(calibration, dict)
                or not isinstance(calibration.get("references"), dict)
                or calibration.get("state") not in ("awaiting_confirmation", "settling", "learning", "partial", "qualified")):
            self.log(f"Corrected state invalid at {path}; awaiting confirmation", level="ERROR")
            return self._corrected_defaults()
        try:
            self._validate_corrected_storage(data)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            self.log(f"Corrected state incomplete at {path}: {exc}; awaiting confirmation", level="ERROR")
            return self._corrected_defaults()
        return data

    @staticmethod
    def _validate_corrected_storage(data):
        def stamp(raw):
            value = datetime.fromisoformat(raw)
            if value.tzinfo is None:
                raise ValueError("naive storage timestamp")
            return value

        def finite(value, minimum=0, maximum=None):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("invalid stored number")
            if not math.isfinite(value) or value < minimum or (maximum is not None and value > maximum):
                raise ValueError("out-of-range stored number")

        c = data["calibration"]
        for key in ("seen_confirmation_ids", "archived_references", "trend_reports"):
            if not isinstance(data.get(key, []), list):
                raise ValueError("invalid " + key)
        if data.get("last_filter_days") is not None:
            finite(data["last_filter_days"], float("-inf"))
        if not isinstance(data.get("timer_confirmation_requested", False), bool):
            raise ValueError("invalid timer confirmation flag")
        if any(not isinstance(event, str) or not event for event in data.get("seen_confirmation_ids", [])):
            raise ValueError("invalid confirmation identity")
        if not isinstance(c.get("candidates", {}), dict):
            raise ValueError("invalid candidates")
        if c["state"] != "awaiting_confirmation":
            if not isinstance(c.get("cycle_id"), str) or not c["cycle_id"]:
                raise ValueError("missing cycle identity")
            if not stamp(c["confirmed_at"]) <= stamp(c["settling_until"]) <= stamp(c["learning_until"]):
                raise ValueError("invalid deadlines")
        elif c["references"] or c.get("candidates"):
            raise ValueError("unconfirmed references")
        for point, metrics in c["references"].items():
            if not isinstance(point, str) or not isinstance(metrics, dict):
                raise ValueError("invalid reference")
            for metric, ref in metrics.items():
                keys = {"sfp": ("sfp",), "duty": ("supply_duty", "exhaust_duty"),
                        "rpm_flow": ("supply_rpm", "exhaust_rpm")}[metric]
                finite(ref["count"], 20)
                if stamp(ref["last_reported_at"]) < stamp(ref["first_reported_at"]):
                    raise ValueError("invalid reference times")
                for key in ("supply_flow_m3h", "exhaust_flow_m3h", *keys):
                    finite(ref[key], 0 if key.endswith("duty") else 1e-12,
                           100 if key.endswith("duty") else None)
        for samples in c.get("candidates", {}).values():
            if not isinstance(samples, list):
                raise ValueError("invalid candidate list")
            for sample in samples:
                stamp(sample["reported_at"])
                if not isinstance(sample["fingerprint"], str):
                    raise ValueError("invalid fingerprint")
                for key in ("sfp", "supply_flow_m3h", "exhaust_flow_m3h"):
                    finite(sample[key], 1e-12)
                for key in ("supply_duty", "exhaust_duty", "supply_rpm", "exhaust_rpm"):
                    if sample.get(key) is not None:
                        finite(sample[key], 0, 100 if key.endswith("duty") else None)
        for report in data.get("trend_reports", []):
            stamp(report["reported_at"])
            finite(report["sfp"])
            for key in ("cycle_id", "point", "fingerprint"):
                if not isinstance(report[key], str):
                    raise ValueError("invalid trend identity")

    def _save_corrected_state(self):
        self._save_json(self.CORRECTED_FILE, self.v2_state)

    def _confirm_clean_filters(self, event_id, confirmed_at):
        """Begin a corrected cycle only from an explicit, deduplicated confirmation."""
        if not isinstance(event_id, str) or not event_id.strip():
            return False
        try:
            confirmed = datetime.fromisoformat(confirmed_at)
            if confirmed.tzinfo is None:
                return False
        except (TypeError, ValueError):
            return False
        if event_id in self.v2_state.get("seen_confirmation_ids", []):
            return False
        previous = self.v2_state["calibration"]
        if previous.get("cycle_id"):
            self.v2_state.setdefault("archived_references", []).append({
                "cycle_id": previous["cycle_id"],
                "confirmed_at": previous.get("confirmed_at"),
                "references": previous.get("references", {}),
            })
        self.v2_state.setdefault("seen_confirmation_ids", []).append(event_id)
        self.v2_state["timer_confirmation_requested"] = False
        settling = int(self.args.get("test_settling_seconds", 7200)) if self.args.get("isolated_test_mode") else 7200
        learning = int(self.args.get("test_learning_seconds", 72 * 3600)) if self.args.get("isolated_test_mode") else 72 * 3600
        self.v2_state["calibration"] = {
            "state": "settling",
            "cycle_id": event_id,
            "confirmed_at": confirmed.isoformat(),
            "settling_until": (confirmed + timedelta(seconds=settling)).isoformat(),
            "learning_until": (confirmed + timedelta(seconds=settling + learning)).isoformat(),
            "references": {},
            "candidates": {},
        }
        self.v2_recent_samples = []
        self.v2_comparison_samples = []
        self.v2_last_sample = {}
        self._save_corrected_state()
        return True

    def _on_clean_filters_confirmed(self, event_name, data, kwargs):
        """Monitor-only HA event; never calls a ventilation service."""
        if not isinstance(data, dict) or data.get("both_filter_paths_clean") is not True:
            return
        event_id = data.get("event_id")
        now = datetime.now(timezone.utc)
        self._confirm_clean_filters(event_id, now.isoformat())

    def _on_clean_filter_button(self, entity, attribute, old, new, kwargs):
        """A confirmed dashboard press is an explicit maintenance assertion."""
        if isinstance(new, str) and new and new != old:
            self._confirm_clean_filters("button:" + new, datetime.now(timezone.utc).isoformat())

    def _collect_corrected_candidate(self, sample, now):
        """Accumulate distinct eligible reports and freeze each qualified metric."""
        calibration = getattr(self, "v2_state", self._corrected_defaults())["calibration"]
        if calibration["state"] not in ("settling", "learning", "partial", "qualified"):
            return False
        settling_until = datetime.fromisoformat(calibration["settling_until"])
        learning_until = datetime.fromisoformat(calibration["learning_until"])
        if now < settling_until:
            return False
        if now > learning_until:
            if calibration["state"] in ("settling", "learning"):
                calibration["state"] = "partial"
                self._save_corrected_state()
            return False
        if calibration["state"] == "settling":
            calibration["state"] = "learning"
        level = sample.get("fan_level")
        if level not in ("Low", "Medium"):
            return False
        try:
            supply = float(sample["supply_flow_m3h"])
            exhaust = float(sample["exhaust_flow_m3h"])
            reported = datetime.fromisoformat(sample["reported_at"])
            if reported.tzinfo is None or not all(math.isfinite(x) and x > 0 for x in (supply, exhaust)):
                return False
        except (KeyError, TypeError, ValueError):
            return False
        if reported < settling_until or reported > learning_until:
            return False
        band = round(((supply + exhaust) / 2) / 25) * 25
        point = f"{level}:{band}"
        samples = calibration.setdefault("candidates", {}).setdefault(point, [])
        fingerprint = sample.get("fingerprint")
        if not fingerprint or any(item["fingerprint"] == fingerprint for item in samples):
            return False
        samples.append(sample)
        references = calibration.setdefault("references", {}).get(point, {})
        changed = True
        for metric, required in (
            ("sfp", ("sfp",)),
            ("duty", ("supply_duty", "exhaust_duty")),
            ("rpm_flow", ("supply_rpm", "exhaust_rpm")),
        ):
            if metric in references:
                continue
            eligible = [item for item in samples if all(item.get(k) is not None for k in required)]
            if len(eligible) < 20:
                continue
            times = [datetime.fromisoformat(item["reported_at"]) for item in eligible]
            min_span = int(self.args.get("test_min_span_seconds", 1800)) if self.args.get("isolated_test_mode") else 1800
            if (max(times) - min(times)).total_seconds() < min_span:
                continue
            medians = {key: statistics.median(float(item[key]) for item in eligible)
                       for key in ("supply_flow_m3h", "exhaust_flow_m3h")}
            if any(medians[key] <= 0 or any(abs(float(item[key]) - medians[key]) / medians[key] > 0.05
                                             for item in eligible)
                   for key in medians):
                continue
            references[metric] = {
                "count": len(eligible), "first_reported_at": min(times).isoformat(),
                "last_reported_at": max(times).isoformat(),
                "supply_flow_m3h": medians["supply_flow_m3h"],
                "exhaust_flow_m3h": medians["exhaust_flow_m3h"],
                **{key: statistics.median(float(item[key]) for item in eligible) for key in required},
            }
        if references:
            calibration["references"][point] = references
            calibration["state"] = "qualified"
        self._save_corrected_state()
        return changed

    def _advance_corrected_calibration(self, now):
        if not hasattr(self, "v2_state"):
            return
        calibration = self.v2_state["calibration"]
        if calibration["state"] not in ("settling", "learning"):
            return
        if now >= datetime.fromisoformat(calibration["learning_until"]):
            calibration["state"] = "partial"
        elif now >= datetime.fromisoformat(calibration["settling_until"]):
            calibration["state"] = "learning"
        else:
            return
        self._save_corrected_state()

    def _observe_corrected_timer(self, source):
        """A timer increase requests human confirmation; it never proves replacement."""
        if not isinstance(source, dict):
            return
        try:
            days = float(source["value"])
        except (KeyError, TypeError, ValueError):
            return
        if not math.isfinite(days):
            return
        previous = self.v2_state.get("last_filter_days")
        if previous is not None and days - previous > 90:
            self.v2_state["timer_confirmation_requested"] = True
        if previous != days:
            self.v2_state["last_filter_days"] = days
            self._save_corrected_state()

    def _corrected_now(self):
        """Real UTC clock, with a source-driven override only in the isolated stack."""
        if getattr(self, "args", {}).get("isolated_test_mode"):
            raw = self.get_state("sensor.zehnder_monitor_test_clock")
            try:
                synthetic = datetime.fromisoformat(raw)
                if synthetic.tzinfo is not None:
                    return synthetic.astimezone(timezone.utc)
            except (TypeError, ValueError):
                pass
        return datetime.now(timezone.utc)

    def _prune_corrected_trend(self, now):
        if not hasattr(self, "v2_state"):
            return
        cutoff = now - timedelta(days=7)
        retained = []
        reports = self.v2_state.get("trend_reports", [])
        for report in reports if isinstance(reports, list) else []:
            try:
                timestamp = datetime.fromisoformat(report["reported_at"])
                if timestamp.tzinfo and cutoff <= timestamp <= now:
                    retained.append(report)
            except (KeyError, TypeError, ValueError):
                continue
        if retained != reports:
            self.v2_state["trend_reports"] = retained
            self._save_corrected_state()

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
            "health_floor": self.health_floor,
            "capacity_floor": self.capacity_floor,
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
        self.health_floor = self._pct_or_none(s.get("health_floor"))
        self.capacity_floor = self._restore_capacity_floor(s.get("capacity_floor"))
        self.last_filter_days = s.get("last_filter_days")
        self.last_conditioned_sample_at = s.get("last_conditioned_sample_at")

    def _pct_or_none(self, value):
        try:
            return round(max(0.0, min(100.0, float(value))), 1)
        except (TypeError, ValueError):
            return None

    def _restore_capacity_floor(self, saved):
        if not isinstance(saved, dict):
            return {}
        values = saved.get("values")
        if not isinstance(values, dict):
            values = {
                key: saved.get(key)
                for key in CAPACITY_REMAINING_KEYS
                if saved.get(key) is not None
            }
        values = {
            key: pct
            for key in CAPACITY_REMAINING_KEYS
            if (pct := self._pct_or_none(values.get(key))) is not None
        }
        return {
            "values": values,
            "updated_at": saved.get("updated_at"),
            "filter_days": saved.get("filter_days"),
        }

    def _reset_capacity_floor(self):
        self.capacity_floor = {}

    def _reset_filter_cycle_floors(self):
        self.health_floor = None
        self._reset_capacity_floor()

    def _trusted_sample_quality(self):
        return self.sample_quality in self.TRUSTED_SAMPLE_QUALITIES

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

        if not self._trusted_sample_quality():
            for key in CAPACITY_REMAINING_KEYS:
                capability[f"instant_{key}"] = capability.get(key)
                capability[key] = None
            capability["capacity_mode"] = f"untrusted_{self.sample_quality}"
            return capability

        floor_values = self.capacity_floor.get("values", {})
        capability, floor_values, changed = floor_capacity_payload(
            capability, floor_values
        )
        if changed:
            self.capacity_floor = {
                "values": floor_values,
                "updated_at": datetime.now().isoformat(),
                "filter_days": r.get("filter_days"),
            }
        else:
            self.capacity_floor.setdefault("values", floor_values)

        capability["capacity_mode"] = "cycle_minimum"
        capability["capacity_floor_updated_at"] = self.capacity_floor.get("updated_at")
        capability["capacity_floor_filter_days"] = self.capacity_floor.get("filter_days")
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
            if r["supply_rpm"] is not None and r["supply_flow"] and r["supply_flow"] > 10 else 0.0
        )
        self.exhaust_rpm_per_flow = (
            r["exhaust_rpm"] / r["exhaust_flow"]
            if r["exhaust_rpm"] is not None and r["exhaust_flow"] and r["exhaust_flow"] > 10 else 0.0
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
        self._reset_capacity_floor()
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
            self._reset_filter_cycle_floors()
            self.sample_quality = "warming_up"
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
        self._reset_capacity_floor()
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
        self.instant_health_score = round(
            sfp_s * 0.50 + rat_s * 0.30 + tim_s * 0.20, 1
        )
        if self._trusted_sample_quality():
            if (
                self.health_floor is None
                or self.instant_health_score < self.health_floor
            ):
                self.health_floor = self.instant_health_score
            self.health_score = self.health_floor
        else:
            self.health_score = self.instant_health_score
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

    def _publish_corrected_sfp(self):
        """Publish a separate SFP reading with its exact source measurements."""
        inputs = {}
        for key in (
            "power", "supply_flow", "exhaust_flow", "supply_duty", "exhaust_duty",
            "supply_rpm", "exhaust_rpm", "bypass", "supply_temp", "outdoor_temp",
            "extract_temp", "fan_level", "filter_days", "status",
        ):
            source = self.get_state(self.E[key], attribute="all")
            if not isinstance(source, dict):
                inputs[key] = None
                continue
            raw_value = source.get("state")
            try:
                numeric = float(raw_value)
                value = numeric if math.isfinite(numeric) else str(raw_value)
            except (TypeError, ValueError):
                value = raw_value
            attributes = source.get("attributes") or {}
            inputs[key] = {
                "value": value,
                "unit": attributes.get("unit_of_measurement"),
                "reported_at": (
                    attributes["source_reported_at"]
                    if "source_reported_at" in attributes
                    else source.get("last_reported") or source.get("last_updated")
                ),
            }
            if key == "status":
                inputs[key]["connected_at"] = (
                    attributes.get("source_reported_at") or source.get("last_changed")
                    or inputs[key]["reported_at"])
        if not getattr(self, "v2_discovery_published", False):
            self.call_service("mqtt/publish", topic="homeassistant/sensor/zehnder_monitor_v2/sfp/config", payload=json.dumps({
                "name": "Zehnder Corrected SFP",
                "unique_id": "zehnder_monitor_v2_sfp",
                "default_entity_id": "sensor.zehnder_corrected_sfp",
                "state_topic": "zehnder/monitor/v2/state",
                "value_template": "{{ value_json.sfp }}",
                "json_attributes_topic": "zehnder/monitor/v2/state",
                "json_attributes_template": "{{ {'inputs': value_json.inputs, 'calculated_at': value_json.calculated_at} | tojson }}",
                "unit_of_measurement": "kW/(m³/s)",
                "state_class": "measurement",
                "suggested_display_precision": 3,
                "icon": "mdi:speedometer",
                "availability_topic": "zehnder/monitor/v2/state",
                "availability_template": "{{ 'online' if value_json.quality == 'current' else 'offline' }}",
                "expire_after": 180,
                "device": {"identifiers": ["zehnder_monitor_v2"], "name": "Zehnder Monitor v2"},
            }), retain=True)
            self.call_service("mqtt/publish", topic="homeassistant/sensor/zehnder_monitor_v2/quality/config", payload=json.dumps({
                "name": "Zehnder Corrected SFP Quality",
                "unique_id": "zehnder_monitor_v2_sfp_quality",
                "default_entity_id": "sensor.zehnder_corrected_sfp_quality",
                "state_topic": "zehnder/monitor/v2/state",
                "value_template": "{{ value_json.quality }}",
                "json_attributes_topic": "zehnder/monitor/v2/state",
                "json_attributes_template": "{{ {'calculated_at': value_json.calculated_at, 'reason': value_json.reason, 'fan_effort_quality': value_json.fan_effort_quality, 'fan_effort_reason': value_json.fan_effort_reason, 'recovery_quality': value_json.recovery_quality, 'recovery_reason': value_json.recovery_reason, 'baseline_eligible': value_json.baseline_eligible, 'eligibility_reason': value_json.eligibility_reason, 'accepted_reports': value_json.accepted_reports} | tojson }}",
                "expire_after": 180,
                "icon": "mdi:check-decagram",
                "device": {"identifiers": ["zehnder_monitor_v2"], "name": "Zehnder Monitor v2"},
            }), retain=True)
            self.call_service("mqtt/publish", topic="homeassistant/sensor/zehnder_monitor_v2/calibration/config", payload=json.dumps({
                "name": "Zehnder Corrected Calibration",
                "unique_id": "zehnder_monitor_v2_calibration",
                "default_entity_id": "sensor.zehnder_corrected_calibration",
                "state_topic": "zehnder/monitor/v2/state",
                "value_template": "{{ value_json.calibration.state }}",
                "json_attributes_topic": "zehnder/monitor/v2/state",
                "json_attributes_template": "{{ {'calculated_at': value_json.calculated_at, 'cycle_id': value_json.calibration.cycle_id, 'confirmed_at': value_json.calibration.confirmed_at, 'settling_until': value_json.calibration.settling_until, 'learning_until': value_json.calibration.learning_until, 'candidate_counts': value_json.calibration.candidate_counts, 'references': value_json.calibration.references, 'timer_confirmation_requested': value_json.calibration.timer_confirmation_requested} | tojson }}",
                "expire_after": 180,
                "icon": "mdi:database-clock",
                "device": {"identifiers": ["zehnder_monitor_v2"], "name": "Zehnder Monitor v2"},
            }), retain=True)
            self.call_service("mqtt/publish", topic="homeassistant/sensor/zehnder_monitor_v2/sfp_change/config", payload=json.dumps({
                "name": "Zehnder Corrected SFP Change",
                "unique_id": "zehnder_monitor_v2_sfp_change",
                "default_entity_id": "sensor.zehnder_corrected_sfp_change",
                "state_topic": "zehnder/monitor/v2/state",
                "value_template": "{{ value_json.comparison.sfp_change_pct }}",
                "json_attributes_topic": "zehnder/monitor/v2/state",
                "json_attributes_template": "{{ {'quality': value_json.comparison.quality, 'selected_reference': value_json.comparison.selected_reference, 'window_count': value_json.comparison.window_count, 'window_minutes': 15} | tojson }}",
                "availability_topic": "zehnder/monitor/v2/state",
                "availability_template": "{{ 'online' if value_json.comparison.sfp_change_pct is not none else 'offline' }}",
                "unit_of_measurement": "%",
                "state_class": "measurement",
                "expire_after": 180,
                "icon": "mdi:chart-line",
                "device": {"identifiers": ["zehnder_monitor_v2"], "name": "Zehnder Monitor v2"},
            }), retain=True)
            self.call_service("mqtt/publish", topic="homeassistant/sensor/zehnder_monitor_v2/comparison_quality/config", payload=json.dumps({
                "name": "Zehnder Corrected Comparison Quality",
                "unique_id": "zehnder_monitor_v2_comparison_quality",
                "default_entity_id": "sensor.zehnder_corrected_comparison_quality",
                "state_topic": "zehnder/monitor/v2/state",
                "value_template": "{{ value_json.comparison.quality }}",
                "json_attributes_topic": "zehnder/monitor/v2/state",
                "json_attributes_template": "{{ {'selected_reference': value_json.comparison.selected_reference, 'window_count': value_json.comparison.window_count, 'window_minutes': 15} | tojson }}",
                "expire_after": 180,
                "device": {"identifiers": ["zehnder_monitor_v2"], "name": "Zehnder Monitor v2"},
            }), retain=True)
            for field, name, unit in (
                ("supply_duty_change_pp", "Supply Duty Change", "pp"),
                ("exhaust_duty_change_pp", "Exhaust Duty Change", "pp"),
                ("supply_rpm_flow_change_pct", "Supply RPM per Flow Change", "%"),
                ("exhaust_rpm_flow_change_pct", "Exhaust RPM per Flow Change", "%"),
            ):
                self.call_service("mqtt/publish", topic=f"homeassistant/sensor/zehnder_monitor_v2/{field}/config", payload=json.dumps({
                    "name": "Zehnder Corrected " + name,
                    "unique_id": "zehnder_monitor_v2_" + field,
                    "default_entity_id": "sensor.zehnder_corrected_" + field,
                    "state_topic": "zehnder/monitor/v2/state",
                    "value_template": "{{ value_json.comparison." + field + " }}",
                    "availability_topic": "zehnder/monitor/v2/state",
                    "availability_template": "{{ 'online' if value_json.comparison." + field + " is not none else 'offline' }}",
                    "unit_of_measurement": unit,
                    "state_class": "measurement", "expire_after": 180,
                    "device": {"identifiers": ["zehnder_monitor_v2"], "name": "Zehnder Monitor v2"},
                }), retain=True)
            self.call_service("mqtt/publish", topic="homeassistant/sensor/zehnder_monitor_v2/sfp_trend/config", payload=json.dumps({
                "name": "Zehnder Corrected SFP Trend",
                "unique_id": "zehnder_monitor_v2_sfp_trend",
                "default_entity_id": "sensor.zehnder_corrected_sfp_trend",
                "state_topic": "zehnder/monitor/v2/state",
                "value_template": "{{ value_json.trend.slope_w_per_m3s_day }}",
                "availability_topic": "zehnder/monitor/v2/state",
                "availability_template": "{{ 'online' if value_json.trend.quality == 'ready' else 'offline' }}",
                "json_attributes_topic": "zehnder/monitor/v2/state",
                "json_attributes_template": "{{ {'quality': value_json.trend.quality, 'bucket_count': value_json.trend.bucket_count, 'span_hours': value_json.trend.span_hours, 'first_hour': value_json.trend.first_hour, 'last_hour': value_json.trend.last_hour, 'cycle_id': value_json.trend.cycle_id, 'point': value_json.trend.point} | tojson }}",
                "unit_of_measurement": "W/(m³/s)/day", "state_class": "measurement",
                "expire_after": 180,
                "device": {"identifiers": ["zehnder_monitor_v2"], "name": "Zehnder Monitor v2"},
            }), retain=True)
            self.call_service("mqtt/publish", topic="homeassistant/sensor/zehnder_monitor_v2/trend_quality/config", payload=json.dumps({
                "name": "Zehnder Corrected Trend Quality",
                "unique_id": "zehnder_monitor_v2_trend_quality",
                "default_entity_id": "sensor.zehnder_corrected_trend_quality",
                "state_topic": "zehnder/monitor/v2/state",
                "value_template": "{{ value_json.trend.quality }}",
                "json_attributes_topic": "zehnder/monitor/v2/state",
                "json_attributes_template": "{{ {'bucket_count': value_json.trend.bucket_count, 'span_hours': value_json.trend.span_hours, 'first_hour': value_json.trend.first_hour, 'last_hour': value_json.trend.last_hour, 'cycle_id': value_json.trend.cycle_id, 'point': value_json.trend.point} | tojson }}",
                "expire_after": 180,
                "device": {"identifiers": ["zehnder_monitor_v2"], "name": "Zehnder Monitor v2"},
            }), retain=True)
            for key, title, template, availability in (
                ("recovery_raw", "Apparent Sensible Recovery", "{{ value_json.recovery.raw_pct }}",
                 "{{ 'online' if value_json.recovery.raw_pct is not none else 'offline' }}"),
                ("recovery_conditioned", "Conditioned Apparent Recovery", "{{ value_json.recovery.conditioned_pct }}",
                 "{{ 'online' if value_json.recovery.conditioned_pct is not none else 'offline' }}"),
            ):
                self.call_service("mqtt/publish", topic=f"homeassistant/sensor/zehnder_monitor_v2/{key}/config", payload=json.dumps({
                    "name": "Zehnder Corrected " + title,
                    "unique_id": "zehnder_monitor_v2_" + key,
                    "default_entity_id": "sensor.zehnder_corrected_" + key,
                    "state_topic": "zehnder/monitor/v2/state", "value_template": template,
                    "availability_topic": "zehnder/monitor/v2/state", "availability_template": availability,
                    "json_attributes_topic": "zehnder/monitor/v2/state",
                    "json_attributes_template": "{{ {'quality': value_json.recovery.quality, 'reason': value_json.recovery.reason, 'conditioned_state': value_json.recovery.conditioned_state, 'conditioned_count': value_json.recovery.conditioned_count, 'last_reported_at': value_json.recovery.last_reported_at, 'age_seconds': value_json.recovery.age_seconds, 'window_seconds': value_json.recovery.window_seconds, 'temperatures_c': value_json.recovery.temperatures_c} | tojson }}",
                    "unit_of_measurement": "%", "state_class": "measurement", "expire_after": 180,
                    "device": {"identifiers": ["zehnder_monitor_v2"], "name": "Zehnder Monitor v2"},
                }), retain=True)
            self.call_service("mqtt/publish", topic="homeassistant/sensor/zehnder_monitor_v2/recovery_quality/config", payload=json.dumps({
                "name": "Zehnder Corrected Recovery Quality",
                "unique_id": "zehnder_monitor_v2_recovery_quality",
                "default_entity_id": "sensor.zehnder_corrected_recovery_quality",
                "state_topic": "zehnder/monitor/v2/state", "value_template": "{{ value_json.recovery.quality }}",
                "json_attributes_topic": "zehnder/monitor/v2/state",
                "json_attributes_template": "{{ {'reason': value_json.recovery.reason, 'conditioned_state': value_json.recovery.conditioned_state, 'conditioned_count': value_json.recovery.conditioned_count, 'last_reported_at': value_json.recovery.last_reported_at, 'age_seconds': value_json.recovery.age_seconds, 'window_seconds': value_json.recovery.window_seconds, 'temperatures_c': value_json.recovery.temperatures_c} | tojson }}",
                "expire_after": 180,
                "device": {"identifiers": ["zehnder_monitor_v2"], "name": "Zehnder Monitor v2"},
            }), retain=True)
            self.v2_discovery_published = True
        result = evaluate_sfp(inputs, self._corrected_now().isoformat())
        if hasattr(self, "v2_state"):
            self._observe_corrected_timer(inputs.get("filter_days"))
        fan_effort = evaluate_fan_effort(inputs, result["calculated_at"])
        recovery = evaluate_recovery_inputs(inputs, result["calculated_at"])
        eligible, eligibility_reason, recent = evaluate_sampling_eligibility(
            inputs, result, getattr(self, "v2_recent_samples", []), result["calculated_at"]
        )
        self.v2_recent_samples = recent if eligibility_reason in ("warming_up", "stable_conditions", "unstable") else []
        sample = None
        if result["quality"] == "current" and result["fingerprint"] != getattr(self, "v2_last_fingerprint", None):
            self.v2_accepted_reports = getattr(self, "v2_accepted_reports", 0) + 1
            self.v2_last_fingerprint = result["fingerprint"]
            if eligible:
                def number(key):
                    value, quality, _ = _validate_dynamic(
                        inputs, key, datetime.fromisoformat(result["calculated_at"]))
                    if quality != "current" or (key.endswith("rpm") and value <= 0):
                        return None
                    return value
                sample = {
                    "fingerprint": result["fingerprint"],
                    "reported_at": max(inputs[key]["reported_at"] for key in ("power", "supply_flow", "exhaust_flow")),
                    "fan_level": inputs["fan_level"]["value"],
                    "supply_flow_m3h": number("supply_flow"),
                    "exhaust_flow_m3h": number("exhaust_flow"),
                    "sfp": result["sfp"],
                    "supply_duty": number("supply_duty"),
                    "exhaust_duty": number("exhaust_duty"),
                    "supply_rpm": number("supply_rpm"),
                    "exhaust_rpm": number("exhaust_rpm"),
                }
                self.v2_last_sample = sample
                self._collect_corrected_candidate(sample, datetime.fromisoformat(result["calculated_at"]))
        if eligible and sample is None and getattr(self, "v2_last_sample", {}).get("fingerprint") == result.get("fingerprint"):
            sample = self.v2_last_sample
        calibration = getattr(self, "v2_state", self._corrected_defaults())["calibration"]
        comparison = {"quality": "unavailable", "selected_reference": None, "window_count": 0,
                      "sfp_change_pct": None, "supply_duty_change_pp": None,
                      "exhaust_duty_change_pp": None, "supply_rpm_flow_change_pct": None,
                      "exhaust_rpm_flow_change_pct": None}
        trend = {"quality": "unavailable", "slope_w_per_m3s_day": None,
                 "bucket_count": 0, "span_hours": 0, "first_hour": None,
                 "last_hour": None, "cycle_id": calibration.get("cycle_id"), "point": None}
        if result["quality"] == "current":
            supply_flow = float(inputs["supply_flow"]["value"]) * UNITS["supply_flow"][inputs["supply_flow"]["unit"]]
            exhaust_flow = float(inputs["exhaust_flow"]["value"]) * UNITS["exhaust_flow"][inputs["exhaust_flow"]["unit"]]
            level = inputs.get("fan_level", {}).get("value") if isinstance(inputs.get("fan_level"), dict) else None
            selected = select_reference(calibration, level, supply_flow, exhaust_flow)
            if selected is None:
                comparison["quality"] = "no_matching_reference"
                trend["quality"] = "no_matching_reference"
            else:
                comparison["selected_reference"] = selected
                trend["point"] = selected["point"]
                if eligible and sample is not None:
                    values, self.v2_comparison_samples = conditioned_comparison(
                        sample, selected, getattr(self, "v2_comparison_samples", []),
                        datetime.fromisoformat(result["calculated_at"]),
                        int(self.args.get("test_comparison_window_seconds", 900)) if self.args.get("isolated_test_mode") else 900,
                    )
                else:
                    values = None
                comparison["window_count"] = len(getattr(self, "v2_comparison_samples", []))
                if values is None:
                    comparison["quality"] = "warming_up" if eligible else "ineligible"
                else:
                    comparison.update({key: round(value, 3) for key, value in values.items()})
                    comparison["quality"] = "ready"
                if hasattr(self, "v2_state"):
                    now = datetime.fromisoformat(result["calculated_at"])
                    cutoff = now - timedelta(days=7)
                    reports = [report for report in self.v2_state.get("trend_reports", [])
                               if datetime.fromisoformat(report["reported_at"]) >= cutoff]
                    if eligible and sample is not None and not any(
                        report["fingerprint"] == sample["fingerprint"]
                        and report["cycle_id"] == selected["cycle_id"]
                        and report["point"] == selected["point"] for report in reports
                    ):
                        reports.append({"fingerprint": sample["fingerprint"],
                                        "reported_at": sample["reported_at"], "sfp": sample["sfp"],
                                        "cycle_id": selected["cycle_id"], "point": selected["point"]})
                        self.v2_state["trend_reports"] = reports
                        self._save_corrected_state()
                    trend.update(hourly_sfp_trend(reports, selected["cycle_id"], selected["point"], now))
                    if trend["slope_w_per_m3s_day"] is not None:
                        trend["slope_w_per_m3s_day"] = round(trend["slope_w_per_m3s_day"], 1)
                    trend.update(cycle_id=selected["cycle_id"], point=selected["point"])
        visible_calibration = {key: value for key, value in calibration.items() if key != "candidates"}
        visible_calibration.setdefault("settling_until", None)
        visible_calibration.setdefault("learning_until", None)
        visible_calibration["candidate_counts"] = {
            point: len(samples) for point, samples in calibration.get("candidates", {}).items()
        }
        visible_calibration["timer_confirmation_requested"] = self.v2_state.get("timer_confirmation_requested", False) if hasattr(self, "v2_state") else False
        recovery_keys = ("supply_temp", "outdoor_temp", "extract_temp", "bypass", "supply_flow", "exhaust_flow")
        recovery_fingerprint = hashlib.sha256(json.dumps(
            {key: inputs.get(key) for key in recovery_keys}, sort_keys=True
        ).encode()).hexdigest()
        current_recovery = recovery["apparent_sensible_recovery_pct"] if recovery["quality"] == "current" else None
        now = datetime.fromisoformat(result["calculated_at"])
        source_reported = max(inputs[key]["reported_at"] for key in recovery_keys) if current_recovery is not None else None
        conditioned, self.v2_recovery_samples = conditioned_recovery(
            current_recovery, recovery_fingerprint if current_recovery is not None else None,
            now, getattr(self, "v2_recovery_samples", []),
            int(self.args.get("test_recovery_window_seconds", 900)) if getattr(self, "args", {}).get("isolated_test_mode") else 900,
            source_reported,
        )
        last_reported = self.v2_recovery_samples[-1]["reported_at"] if self.v2_recovery_samples else None
        recovery_detail = {
            "raw_pct": round(recovery["apparent_sensible_recovery_pct"], 1) if recovery["apparent_sensible_recovery_pct"] is not None else None,
            "quality": recovery["quality"], "reason": recovery["reason"],
            "temperatures_c": recovery["temperatures_c"],
            "conditioned_pct": round(conditioned, 1) if conditioned is not None else None,
            "conditioned_state": "historical" if conditioned is not None and current_recovery is None else ("ready" if conditioned is not None else "warming_up"),
            "conditioned_count": len(self.v2_recovery_samples),
            "last_reported_at": last_reported,
            "age_seconds": round((now - datetime.fromisoformat(last_reported)).total_seconds(), 1) if last_reported else None,
            "window_seconds": 900,
        }
        payload = {
            "sfp": round(result["sfp"], 4) if result["sfp"] is not None else None,
            "inputs": inputs,
            "calculated_at": result["calculated_at"],
            "quality": result["quality"],
            "reason": result["reason"],
            "fan_effort_quality": fan_effort["quality"],
            "fan_effort_reason": fan_effort["reason"],
            "recovery_quality": recovery["quality"],
            "recovery_reason": recovery["reason"],
            "baseline_eligible": eligible,
            "eligibility_reason": eligibility_reason,
            "accepted_reports": getattr(self, "v2_accepted_reports", 0),
            "calibration": visible_calibration,
            "comparison": comparison,
            "trend": trend,
            "recovery": recovery_detail,
        }
        self.call_service("mqtt/publish", topic="zehnder/monitor/v2/state", payload=json.dumps(payload, allow_nan=False), retain=False)
        return result["quality"]

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
                "availability_template": "{{ 'online' if value_json.health.sample_quality in ['conditioned', 'conditioned_stale'] else 'offline' }}",
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
                "availability_template": "{{ 'online' if value_json.health.sample_quality in ['conditioned', 'conditioned_stale'] else 'offline' }}",
            }),
            ("duty_ratio", {
                "name": "Zehnder Duty Ratio",
                "unique_id": "zehnder_monitor_duty_ratio",
                "default_entity_id": "sensor.zehnder_duty_ratio",
                "state_class": "measurement",
                "icon": "mdi:arrow-split-vertical",
                "value_template": "{{ value_json.metrics.duty_ratio }}",
                "availability_topic": "zehnder/monitor/state",
                "availability_template": "{{ 'online' if value_json.health.sample_quality in ['conditioned', 'conditioned_stale'] else 'offline' }}",
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
                "availability_template": "{{ 'online' if value_json.capability.baseline_quality in ['conditioned', 'single_sample'] and value_json.health.sample_quality in ['conditioned', 'conditioned_stale'] else 'offline' }}",
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
                "availability_template": "{{ 'online' if value_json.capability.baseline_quality in ['conditioned', 'single_sample'] and value_json.health.sample_quality in ['conditioned', 'conditioned_stale'] else 'offline' }}",
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
                "availability_template": "{{ 'online' if value_json.capability.baseline_quality in ['conditioned', 'single_sample'] and value_json.health.sample_quality in ['conditioned', 'conditioned_stale'] else 'offline' }}",
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
                "score": self.health_score,
                "instant_score": self.instant_health_score,
                "score_mode": (
                    "cycle_minimum"
                    if self._trusted_sample_quality()
                    else f"untrusted_{self.sample_quality}"
                ),
                "health_floor": self.health_floor,
                "status": self._health_l(),
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

    def _prune_history(self):
        now = time.time()
        cutoff = now - self.BUFFER_HOURS * 3600
        for name in ("sfp_buffer", "ratio_buffer", "rpm_ratio_buffer", "heat_recovery_buffer"):
            if hasattr(self, name):
                setattr(self, name, [(t, value) for t, value in getattr(self, name) if t > cutoff])
        if hasattr(self, "baseline_candidate_buffer"):
            baseline_cutoff = now - self.BASELINE_CANDIDATE_HOURS * 3600
            self.baseline_candidate_buffer = [
                sample for sample in self.baseline_candidate_buffer
                if sample.get("time", 0) > baseline_cutoff
            ]

    def _tick(self, kwargs):
        self.tick_count += 1
        self.log(f"Evaluation tick {self.tick_count}")
        try:
            self._prune_history()
            self._prune_corrected_trend(self._corrected_now())
            self._advance_corrected_calibration(datetime.now(timezone.utc))
            v2_quality = self._publish_corrected_sfp()
        except Exception as exc:
            self.log(f"Corrected SFP publisher failed: {exc}", level="ERROR")
            return
        if v2_quality != "current":
            return
        r = self._read()
        if r is None:
            if self.tick_count % 10 == 0:
                self.log("Unit offline.", level="WARNING")
            return
        if r.get("supply_rpm") is None or r.get("exhaust_rpm") is None:
            return

        try:
            self._compute(r)
            self._sample(r)
            self._detect_change(r)
            self._health(r)
            if not self.discovery_published:
                self.discovery_published = self._publish_mqtt_discovery()
            self._publish_mqtt(r)
        except Exception as exc:
            self.log(f"Legacy monitor evaluation failed: {exc}", level="ERROR")
            return

        if self.tick_count % 5 == 0:
            self._persist()
            self.log(
                f"[HB] Health:{self.health_score:.0f}% ({self._health_l()}) | "
                f"Instant:{self.instant_health_score:.0f}% | "
                f"SFP:{self.health_sfp:.3f} ({self._sfp_c(self.health_sfp)}) | "
                f"Raw:{self.sfp:.3f} | Ratio:{self.health_duty_ratio:.2f}x | "
                f"Capacity:{self.capability.get('filter_capacity_remaining')}% | "
                f"Fan:{r['fan_level']} | eta:{self.heat_recovery_eta:.0f}% | "
                f"Filter:{r.get('filter_days','?')}d | "
                f"Buf:{len(self.sfp_buffer)} | Quality:{self.sample_quality}"
            )
