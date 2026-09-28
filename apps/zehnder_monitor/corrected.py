"""Version 2 monitor calculations, isolated from legacy history."""

from datetime import datetime
import hashlib
import json
import math
import statistics


MAX_REPORT_AGE_SECONDS = 600
UNITS = {
    "power": {"W": 1, "kW": 1000},
    "supply_flow": {"m³/h": 1, "m3/h": 1, "m³/s": 3600, "m3/s": 3600, "L/s": 3.6, "l/s": 3.6},
    "exhaust_flow": {"m³/h": 1, "m3/h": 1, "m³/s": 3600, "m3/s": 3600, "L/s": 3.6, "l/s": 3.6},
    "supply_duty": {"%": 1},
    "exhaust_duty": {"%": 1},
    "supply_rpm": {"rpm": 1},
    "exhaust_rpm": {"rpm": 1},
    "bypass": {"%": 1},
    "supply_temp": {"°C": 1, "C": 1, "°F": 1, "F": 1},
    "outdoor_temp": {"°C": 1, "C": 1, "°F": 1, "F": 1},
    "extract_temp": {"°C": 1, "C": 1, "°F": 1, "F": 1},
}


def corrected_sfp(inputs, calculated_at):
    """Return kW/(m³/s) using the mean of supply and exhaust volume flow."""
    power_w = float(inputs["power"]["value"])
    supply_m3h = float(inputs["supply_flow"]["value"])
    exhaust_m3h = float(inputs["exhaust_flow"]["value"])
    mean_m3s = ((supply_m3h + exhaust_m3h) / 2) / 3600
    return {
        "sfp": power_w / 1000 / mean_m3s,
        "inputs": inputs,
        "calculated_at": calculated_at,
    }


def evaluate_sfp(inputs, calculated_at):
    """Validate dynamic source reports before exposing a current SFP value."""
    result = {"sfp": None, "inputs": inputs, "calculated_at": calculated_at,
              "quality": "unavailable", "reason": None, "fingerprint": None}
    normalized = {}
    try:
        now = datetime.fromisoformat(calculated_at)
    except (TypeError, ValueError):
        raise ValueError("calculated_at must be an ISO timestamp")
    if now.tzinfo is None:
        raise ValueError("calculated_at must include a timezone")
    for key in ("power", "supply_flow", "exhaust_flow"):
        source = inputs.get(key)
        if not isinstance(source, dict) or source.get("value") in (None, "unknown", "unavailable", ""):
            result.update(quality="unavailable", reason=f"missing_{key}")
            return result
        unit = source.get("unit")
        if unit not in UNITS[key]:
            result.update(quality="unsupported", reason=f"unsupported_{key}_unit")
            return result
        try:
            value = float(source["value"])
        except (TypeError, ValueError):
            result.update(quality="invalid", reason=f"invalid_{key}")
            return result
        if not math.isfinite(value) or value < 0:
            result.update(quality="invalid", reason=f"invalid_{key}")
            return result
        normalized[key] = value * UNITS[key][unit]

    if normalized["supply_flow"] == 0 or normalized["exhaust_flow"] == 0:
        result.update(quality="stopped", reason="zero_airflow")
        return result

    for key in ("power", "supply_flow", "exhaust_flow"):
        timestamp = inputs[key].get("reported_at")
        if not timestamp:
            result.update(quality="unknown_freshness", reason=f"missing_{key}_report_time")
            return result
        try:
            reported = datetime.fromisoformat(timestamp)
        except (TypeError, ValueError):
            result.update(quality="unknown_freshness", reason=f"invalid_{key}_report_time")
            return result
        if reported.tzinfo is None:
            result.update(quality="unknown_freshness", reason=f"naive_{key}_report_time")
            return result
        age = (now - reported).total_seconds()
        if age < -30:
            result.update(quality="invalid", reason=f"future_{key}_report_time")
            return result
        if age > MAX_REPORT_AGE_SECONDS:
            result.update(quality="stale", reason=f"stale_{key}")
            return result

    normalized_inputs = {
        "power": {"value": normalized["power"], "unit": "W"},
        "supply_flow": {"value": normalized["supply_flow"], "unit": "m³/h"},
        "exhaust_flow": {"value": normalized["exhaust_flow"], "unit": "m³/h"},
    }
    sfp = corrected_sfp(normalized_inputs, calculated_at)["sfp"]
    fingerprint_data = {key: inputs[key] for key in ("power", "supply_flow", "exhaust_flow")}
    fingerprint = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True).encode()).hexdigest()
    result.update(sfp=sfp, quality="current", reason="fresh_reports", fingerprint=fingerprint)
    return result


def _validate_dynamic(inputs, key, now):
    source = inputs.get(key)
    if not isinstance(source, dict) or source.get("value") in (None, "unknown", "unavailable", ""):
        return None, "unavailable", f"missing_{key}"
    unit = source.get("unit")
    if unit not in UNITS[key]:
        return None, "unsupported", f"unsupported_{key}_unit"
    try:
        value = float(source["value"])
    except (TypeError, ValueError):
        return None, "invalid", f"invalid_{key}"
    if not math.isfinite(value):
        return None, "invalid", f"invalid_{key}"
    if key in ("supply_flow", "exhaust_flow", "supply_rpm", "exhaust_rpm") and value < 0:
        return None, "invalid", f"invalid_{key}"
    if key in ("supply_duty", "exhaust_duty", "bypass") and not 0 <= value <= 100:
        return None, "invalid", f"invalid_{key}"
    timestamp = source.get("reported_at")
    if not timestamp:
        return None, "unknown_freshness", f"missing_{key}_report_time"
    try:
        reported = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return None, "unknown_freshness", f"invalid_{key}_report_time"
    if reported.tzinfo is None:
        return None, "unknown_freshness", f"naive_{key}_report_time"
    age = (now - reported).total_seconds()
    if age < -30:
        return None, "invalid", f"future_{key}_report_time"
    if age > MAX_REPORT_AGE_SECONDS:
        return None, "stale", f"stale_{key}"
    return value * UNITS[key][unit], "current", "fresh_reports"


def evaluate_fan_effort(inputs, calculated_at):
    """Fan-effort inputs are independent of SFP and temperature inputs."""
    now = datetime.fromisoformat(calculated_at)
    values = {}
    for key in ("supply_duty", "exhaust_duty", "supply_rpm", "exhaust_rpm", "supply_flow", "exhaust_flow"):
        value, quality, reason = _validate_dynamic(inputs, key, now)
        if quality != "current":
            return {"quality": quality, "reason": reason}
        values[key] = value
    if values["supply_flow"] == 0 or values["exhaust_flow"] == 0:
        return {"quality": "stopped", "reason": "zero_airflow"}
    return {"quality": "current", "reason": "fresh_reports"}


def evaluate_recovery_inputs(inputs, calculated_at):
    """Screen recovery operating evidence; calculation is added in milestone 7."""
    now = datetime.fromisoformat(calculated_at)
    bypass = inputs.get("bypass")
    if not isinstance(bypass, dict) or bypass.get("value") in (None, "", "unknown", "unavailable"):
        return {"quality": "unsupported", "reason": "unknown_bypass"}
    values = {}
    for key in ("bypass", "supply_temp", "outdoor_temp", "extract_temp"):
        value, quality, reason = _validate_dynamic(inputs, key, now)
        if quality != "current":
            return {"quality": quality, "reason": reason}
        values[key] = value
    if values["bypass"] >= 5:
        return {"quality": "unsupported", "reason": "bypass_open"}
    return {"quality": "current", "reason": "fresh_reports"}


def evaluate_sampling_eligibility(inputs, sfp_result, recent, calculated_at):
    """Keep baseline suitability separate from whether raw SFP is current."""
    if sfp_result["quality"] != "current":
        return False, f"sfp_{sfp_result['quality']}", recent
    mode = inputs.get("fan_level")
    if not isinstance(mode, dict) or mode.get("value") not in ("Low", "Medium"):
        return False, "unsupported_fan_level", recent
    bypass, quality, reason = _validate_dynamic(inputs, "bypass", datetime.fromisoformat(calculated_at))
    if quality != "current":
        return False, reason, recent
    if bypass >= 5:
        return False, "bypass_open", recent
    values = {
        key: float(inputs[key]["value"]) * UNITS[key][inputs[key]["unit"]]
        for key in ("power", "supply_flow", "exhaust_flow")
    }
    if values["power"] == 0:
        return False, "zero_power", recent
    mean_flow = (values["supply_flow"] + values["exhaust_flow"]) / 2
    if abs(values["supply_flow"] - values["exhaust_flow"]) / mean_flow >= 0.10:
        return False, "flow_imbalance", recent
    fingerprint = sfp_result["fingerprint"]
    if not any(item["fingerprint"] == fingerprint for item in recent):
        recent = [*recent, {"fingerprint": fingerprint, **values}][-3:]
    if len(recent) < 3:
        return False, "warming_up", recent
    for key in values:
        median = statistics.median(item[key] for item in recent)
        if median <= 0 or any(abs(item[key] - median) / median > 0.08 for item in recent):
            return False, "unstable", recent
    return True, "stable_conditions", recent
