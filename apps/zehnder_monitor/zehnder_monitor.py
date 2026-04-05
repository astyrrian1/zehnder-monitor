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
import time
from datetime import datetime


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

    # === ALERT THRESHOLDS ===
    HEALTH_ADVISORY = 60
    HEALTH_WARNING  = 30
    HEALTH_CRITICAL = 10
    SFP_ABS_CRIT    = 0.75  # Absolute SFP critical regardless of score

    # === TIMING ===
    TICK_SECONDS   = 60
    BUFFER_HOURS   = 168    # 7-day conditioned sample window
    STATE_FILE     = "state.json"
    BASELINE_FILE  = "baselines.json"

    # =================================================================
    # INIT
    # =================================================================

    def initialize(self):
        self.log("=" * 60)
        self.log("ZEHNDER MONITOR v1.0.0 -- Physics-Based Filter Health")
        self.log("=" * 60)

        self.sfp = 0.0
        self.duty_ratio = 1.0
        self.duty_asymmetry_abs = 0.0
        self.supply_rpm_per_flow = 0.0
        self.exhaust_rpm_per_flow = 0.0
        self.heat_recovery_eta = 0.0
        self.health_score = 100.0
        self.sfp_trend_slope = 0.0

        self.sfp_buffer = []
        self.ratio_buffer = []
        self.rpm_ratio_buffer = []

        self.last_filter_days = None
        self.baseline_timer = None
        self.last_alert = {"advisory": 0, "warning": 0, "critical": 0}
        self.tick_count = 0

        self.baselines = self._load_json(self.BASELINE_FILE, self._defaults())
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
            "sfp": 0.45, "duty_ratio": 1.50,
            "supply_duty": 40.0, "exhaust_duty": 27.0,
            "supply_rpm_per_flow": 8.0,
            "captured_at": None, "filter_days_at_capture": None,
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
        cutoff = time.time() - 86400
        self._save_json(self.STATE_FILE, {
            "sfp_buffer": [(t, v) for t, v in self.sfp_buffer if t > cutoff],
            "ratio_buffer": [(t, v) for t, v in self.ratio_buffer if t > cutoff],
            "rpm_ratio_buffer": [(t, v) for t, v in self.rpm_ratio_buffer if t > cutoff],
            "last_filter_days": self.last_filter_days,
            "last_alert": self.last_alert,
            "saved_at": datetime.now().isoformat(),
        })

    def _restore(self, s):
        if not s:
            return
        self.sfp_buffer = s.get("sfp_buffer", [])
        self.ratio_buffer = s.get("ratio_buffer", [])
        self.rpm_ratio_buffer = s.get("rpm_ratio_buffer", [])
        self.last_filter_days = s.get("last_filter_days")
        self.last_alert.update(s.get("last_alert", {}))

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

        # Heat recovery eta (only valid with bypass off and meaningful dT)
        if (r["supply_temp"] is not None and r["outdoor_temp"] is not None
                and r["extract_temp"] is not None):
            dt = r["extract_temp"] - r["outdoor_temp"]
            if r["bypass"] < 5.0 and abs(dt) > 5.0:
                eta = ((r["supply_temp"] - r["outdoor_temp"]) / dt) * 100.0
                self.heat_recovery_eta = max(0.0, min(120.0, eta))

    # =================================================================
    # CONDITIONED SAMPLING
    # =================================================================

    def _sample(self, r):
        """Record metrics only under steady, comparable conditions."""
        now = time.time()
        fl = r.get("fan_level", "")
        bp = r.get("bypass", 100)
        pw = r.get("power", 0)

        if r["supply_flow"] < 1:
            return

        imbal = abs(r["supply_flow"] - r["exhaust_flow"]) / r["supply_flow"]

        if (fl == "Low" and bp < 5.0 and pw > 20.0
                and imbal < 0.10 and self.sfp > 0.1):
            self.sfp_buffer.append((now, self.sfp))
            self.ratio_buffer.append((now, self.duty_ratio))
            self.rpm_ratio_buffer.append((now, self.supply_rpm_per_flow))

        # Prune to window
        cutoff = now - (self.BUFFER_HOURS * 3600)
        self.sfp_buffer = [(t, v) for t, v in self.sfp_buffer if t > cutoff]
        self.ratio_buffer = [(t, v) for t, v in self.ratio_buffer if t > cutoff]
        self.rpm_ratio_buffer = [(t, v) for t, v in self.rpm_ratio_buffer if t > cutoff]

        self.sfp_trend_slope = (
            self._slope(self.sfp_buffer) if len(self.sfp_buffer) >= 20 else 0.0
        )

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
        self.baselines = {
            "sfp": round(self.sfp, 4),
            "duty_ratio": round(self.duty_ratio, 3),
            "supply_duty": round(r["supply_duty"], 1),
            "exhaust_duty": round(r["exhaust_duty"], 1),
            "supply_rpm_per_flow": round(self.supply_rpm_per_flow, 3),
            "fan_level_at_capture": r.get("fan_level", "?"),
            "captured_at": datetime.now().isoformat(),
            "filter_days_at_capture": r.get("filter_days", 0),
        }
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
            (self.SFP_REPLACE - self.sfp) /
            (self.SFP_REPLACE - self.SFP_PRISTINE) * 100))
        rat_s = max(0, min(100,
            (self.RATIO_REPLACE - self.duty_ratio) /
            (self.RATIO_REPLACE - self.RATIO_PRISTINE) * 100))
        days = r.get("filter_days") or 0
        tim_s = max(0, min(100, days / self.FILTER_CYCLE * 100))
        self.health_score = round(sfp_s * 0.50 + rat_s * 0.30 + tim_s * 0.20, 1)

    # =================================================================
    # ALERTS
    # =================================================================

    def _alerts(self, r):
        now = time.time()

        if self.health_score < self.HEALTH_CRITICAL or self.sfp > self.SFP_ABS_CRIT:
            if now - self.last_alert["critical"] > 3600:
                self.last_alert["critical"] = now
                self._notify(
                    "ZEHNDER: Replace Filters Now",
                    f"CRITICAL -- Health: {self.health_score:.0f}%\n"
                    f"SFP: {self.sfp:.3f} ({self._sfp_c()})\n"
                    f"Duty Ratio: {self.duty_ratio:.2f}x "
                    f"(supply {r['supply_duty']:.0f}% / exhaust {r['exhaust_duty']:.0f}%)\n"
                    f"Timer: {r.get('filter_days', '?')} days\n\n"
                    f"Excessive energy to maintain airflow. Risk of fan motor stress.",
                    "zehnder_filter_critical"
                )
            return

        if self.health_score < self.HEALTH_WARNING:
            if now - self.last_alert["warning"] > 21600:
                self.last_alert["warning"] = now
                self._notify(
                    "Zehnder Filter Warning",
                    f"Health: {self.health_score:.0f}%\n"
                    f"SFP: {self.sfp:.3f} | Ratio: {self.duty_ratio:.2f}x\n"
                    f"Timer: {r.get('filter_days', '?')} days\n"
                    f"Replace filters soon.",
                    "zehnder_filter_warning"
                )
            return

        if self.health_score < self.HEALTH_ADVISORY:
            if now - self.last_alert["advisory"] > 86400:
                self.last_alert["advisory"] = now
                trend = ""
                if abs(self.sfp_trend_slope) > 0.0001:
                    d = "rising" if self.sfp_trend_slope > 0 else "falling"
                    trend = (
                        f"\nSFP trend: {d} "
                        f"{abs(self.sfp_trend_slope*1000):.1f} mW/(m3/s)/day"
                    )
                self._notify(
                    "Zehnder Filter Advisory",
                    f"Health: {self.health_score:.0f}%\n"
                    f"SFP: {self.sfp:.3f} ({self._sfp_c()})\n"
                    f"Timer: {r.get('filter_days', '?')} days{trend}\n"
                    f"Consider ordering filters.",
                    "zehnder_filter_advisory"
                )

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
        self.set_state(
            "sensor.zehnder_sfp", state=round(self.sfp, 3),
            attributes={
                "unit_of_measurement": "kW/(m3/s)",
                "state_class": "measurement", "icon": "mdi:speedometer",
                "friendly_name": "Zehnder SFP", "sfp_class": self._sfp_c(),
            }
        )
        self.set_state(
            "sensor.zehnder_filter_health",
            state=round(self.health_score, 0),
            attributes={
                "unit_of_measurement": "%",
                "state_class": "measurement", "icon": "mdi:air-filter",
                "friendly_name": "Zehnder Filter Health",
                "status": self._health_l(),
            }
        )
        self.set_state(
            "sensor.zehnder_duty_ratio",
            state=round(self.duty_ratio, 3),
            attributes={
                "state_class": "measurement",
                "icon": "mdi:arrow-split-vertical",
                "friendly_name": "Zehnder Duty Ratio",
                "absolute_asymmetry_pct": round(self.duty_asymmetry_abs, 1),
            }
        )
        self.set_state(
            "sensor.zehnder_heat_recovery",
            state=round(self.heat_recovery_eta, 1),
            attributes={
                "unit_of_measurement": "%",
                "state_class": "measurement", "icon": "mdi:heat-wave",
                "friendly_name": "Zehnder Heat Recovery",
            }
        )
        self.set_state(
            "sensor.zehnder_sfp_trend",
            state=round(self.sfp_trend_slope * 1000, 2),
            attributes={
                "unit_of_measurement": "mW/(m3/s)/day",
                "state_class": "measurement", "icon": "mdi:trending-up",
                "friendly_name": "Zehnder SFP Trend",
                "conditioned_samples_7d": len(self.sfp_buffer),
            }
        )

    # =================================================================
    # MQTT
    # =================================================================

    def _publish_mqtt(self, r):
        payload = {
            "timestamp": datetime.now().isoformat(),
            "unit_online": True,
            "metrics": {
                "sfp": round(self.sfp, 4), "sfp_class": self._sfp_c(),
                "duty_ratio": round(self.duty_ratio, 3),
                "duty_asymmetry_pct": round(self.duty_asymmetry_abs, 1),
                "supply_rpm_per_flow": round(self.supply_rpm_per_flow, 3),
                "exhaust_rpm_per_flow": round(self.exhaust_rpm_per_flow, 3),
                "heat_recovery_eta": round(self.heat_recovery_eta, 1),
            },
            "health": {
                "score": self.health_score, "status": self._health_l(),
                "sfp_trend_per_day": round(self.sfp_trend_slope, 6),
                "conditioned_samples": len(self.sfp_buffer),
            },
            "raw": {
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
            },
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

    def _sfp_c(self):
        if self.sfp < 0.50: return "SFP 1 (Excellent)"
        if self.sfp < 0.75: return "SFP 2 (Good)"
        if self.sfp < 1.25: return "SFP 3 (Fair)"
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
        self._alerts(r)
        self._publish_sensors()
        self._publish_mqtt(r)

        if self.tick_count % 5 == 0:
            self._persist()
            self.log(
                f"[HB] Health:{self.health_score:.0f}% ({self._health_l()}) | "
                f"SFP:{self.sfp:.3f} ({self._sfp_c()}) | "
                f"Ratio:{self.duty_ratio:.2f}x | "
                f"Fan:{r['fan_level']} | eta:{self.heat_recovery_eta:.0f}% | "
                f"Filter:{r.get('filter_days','?')}d | "
                f"Buf:{len(self.sfp_buffer)}"
            )
