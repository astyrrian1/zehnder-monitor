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


def validate_bypass(inputs, now):
    """A latched position needs current same-device evidence after connection.

    Never change the original source timestamp or count corroboration as a
    new position report. Unknown/offline/reconnected evidence fails closed.
    """
    status = inputs.get("status") or {}
    if status and status.get("value") != "on":
        return None, "unavailable", "device_offline"
    value, quality, reason = _validate_dynamic(inputs, "bypass", now)
    if quality != "stale":
        return value, quality, reason
    status = inputs.get("status") or {}
    if status.get("value") != "on":
        return None, quality, reason
    try:
        connected = datetime.fromisoformat(status.get("connected_at") or status["reported_at"])
        position = datetime.fromisoformat(inputs["bypass"]["reported_at"])
        if connected.tzinfo is None or position < connected or connected > now:
            return None, quality, reason
    except (KeyError, TypeError, ValueError):
        return None, quality, reason
    if evaluate_sfp(inputs, now.isoformat())["quality"] != "current":
        return None, quality, reason
    return float(inputs["bypass"]["value"]), "current", "latched_position_live_device"


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
    """Apparent sensible recovery, never clipped or called certified efficiency."""
    now = datetime.fromisoformat(calculated_at)
    result = {"quality": "unavailable", "reason": None,
              "apparent_sensible_recovery_pct": None, "temperatures_c": None}
    bypass = inputs.get("bypass")
    if not isinstance(bypass, dict) or bypass.get("value") in (None, "", "unknown", "unavailable"):
        result.update(quality="unsupported", reason="unknown_bypass")
        return result
    values = {}
    for key in ("bypass", "supply_temp", "outdoor_temp", "extract_temp", "supply_flow", "exhaust_flow"):
        value, quality, reason = (validate_bypass(inputs, now) if key == "bypass"
                                  else _validate_dynamic(inputs, key, now))
        if quality != "current":
            result.update(quality=quality, reason=reason)
            return result
        if key.endswith("_temp") and inputs[key]["unit"] in ("°F", "F"):
            value = (value - 32) * 5 / 9
        values[key] = value
    if values["bypass"] >= 5:
        result.update(quality="unsupported", reason="bypass_open")
        return result
    if values["supply_flow"] <= 0 or values["exhaust_flow"] <= 0:
        result.update(quality="stopped", reason="zero_airflow")
        return result
    mode = inputs.get("fan_level")
    if not isinstance(mode, dict) or mode.get("value") not in ("Low", "Medium"):
        result.update(quality="unsupported", reason="unsupported_fan_level")
        return result
    temperatures = {key: values[key] for key in ("supply_temp", "outdoor_temp", "extract_temp")}
    result["temperatures_c"] = temperatures
    delta = values["extract_temp"] - values["outdoor_temp"]
    if abs(delta) < 5:
        result.update(quality="unsupported", reason="small_temperature_difference")
        return result
    ratio = (values["supply_temp"] - values["outdoor_temp"]) / delta * 100
    result["apparent_sensible_recovery_pct"] = ratio
    if ratio < 0 or ratio > 100:
        result.update(quality="anomalous", reason="out_of_range_apparent_ratio")
    else:
        result.update(quality="current", reason="fresh_reports")
    return result


def conditioned_recovery(raw_pct, fingerprint, now, history, window_seconds=900, reported_at=None):
    """Historical median stays labeled as history when current raw is missing."""
    from datetime import timedelta
    cutoff = now - timedelta(seconds=window_seconds)
    history = [item for item in history
               if cutoff <= datetime.fromisoformat(item['reported_at']) <= now]
    if raw_pct is not None and fingerprint and not any(item['fingerprint'] == fingerprint for item in history):
        history.append({'fingerprint': fingerprint, 'reported_at': reported_at or now.isoformat(), 'value': raw_pct})
    if len(history) < 5:
        return None, history
    return statistics.median(item['value'] for item in history), history


def evaluate_sampling_eligibility(inputs, sfp_result, recent, calculated_at):
    """Keep baseline suitability separate from whether raw SFP is current."""
    if sfp_result["quality"] != "current":
        return False, f"sfp_{sfp_result['quality']}", recent
    mode = inputs.get("fan_level")
    if not isinstance(mode, dict) or mode.get("value") not in ("Low", "Medium"):
        return False, "unsupported_fan_level", recent
    bypass, quality, reason = validate_bypass(inputs, datetime.fromisoformat(calculated_at))
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
    now = datetime.fromisoformat(calculated_at)
    recent = [item for item in recent if item.get("fan_level") == mode["value"]
              and item.get("observed_at")
              and 0 <= (now - datetime.fromisoformat(item["observed_at"])).total_seconds() <= 600]
    fingerprint = sfp_result["fingerprint"]
    if not any(item["fingerprint"] == fingerprint for item in recent):
        recent = [*recent, {"fingerprint": fingerprint, "fan_level": mode["value"], "observed_at": calculated_at, **values}][-3:]
    if len(recent) < 3:
        return False, "warming_up", recent
    for key in values:
        median = statistics.median(item[key] for item in recent)
        if median <= 0 or any(abs(item[key] - median) / median > 0.08 for item in recent):
            return False, "unstable", recent
    return True, "stable_conditions", recent


def select_reference(calibration, fan_level, supply_flow_m3h, exhaust_flow_m3h):
    """Choose one same-cycle/level reference by both flow medians, without blending."""
    if fan_level not in ('Low', 'Medium') or not calibration.get('cycle_id'):
        return None
    candidates = []
    for point, metrics in calibration.get('references', {}).items():
        if not point.startswith(fan_level + ':') or 'sfp' not in metrics:
            continue
        ref = metrics['sfp']
        supply_ref = ref.get('supply_flow_m3h')
        exhaust_ref = ref.get('exhaust_flow_m3h')
        if not supply_ref or not exhaust_ref:
            continue
        supply_gap = abs(supply_flow_m3h - supply_ref) / supply_ref
        exhaust_gap = abs(exhaust_flow_m3h - exhaust_ref) / exhaust_ref
        if supply_gap <= 0.05 + 1e-12 and exhaust_gap <= 0.05 + 1e-12:
            candidates.append((supply_gap + exhaust_gap, point, metrics))
    if not candidates:
        return None
    _, point, metrics = min(candidates, key=lambda item: (item[0], item[1]))
    return {'cycle_id': calibration['cycle_id'], 'point': point, 'metrics': metrics}


def calculate_reference_change(sample, selected):
    """Signed changes: SFP/RPM per airflow in percent; duties in percentage points."""
    metrics = selected['metrics']
    sfp_ref = metrics['sfp']['sfp']
    result = {'sfp_change_pct': (sample['sfp'] / sfp_ref - 1) * 100}
    duty_ref = metrics.get('duty')
    if duty_ref and sample.get('supply_duty') is not None and sample.get('exhaust_duty') is not None:
        result['supply_duty_change_pp'] = sample['supply_duty'] - duty_ref['supply_duty']
        result['exhaust_duty_change_pp'] = sample['exhaust_duty'] - duty_ref['exhaust_duty']
    rpm_ref = metrics.get('rpm_flow')
    if rpm_ref and sample.get('supply_rpm') is not None and sample.get('exhaust_rpm') is not None:
        for fan in ('supply', 'exhaust'):
            current = sample[fan + '_rpm'] / sample[fan + '_flow_m3h']
            baseline = rpm_ref[fan + '_rpm'] / rpm_ref[fan + '_flow_m3h']
            result[fan + '_rpm_flow_change_pct'] = (current / baseline - 1) * 100
    return result


def conditioned_comparison(sample, selected, history, now, window_seconds=900):
    """Median of at least five distinct matched observations in the past 15 minutes."""
    from datetime import timedelta
    reference_key = selected['cycle_id'] + ':' + selected['point']
    cutoff = now - timedelta(seconds=window_seconds)
    history = [item for item in history
               if item.get('reference_key') == reference_key
               and cutoff <= datetime.fromisoformat(item['reported_at']) <= now]
    fingerprint = sample.get('fingerprint')
    if fingerprint and not any(item['fingerprint'] == fingerprint for item in history):
        history.append({**sample, 'reference_key': reference_key})
    if len(history) < 5:
        return None, history
    values = [calculate_reference_change(item, selected) for item in history]
    keys = set.intersection(*(set(item) for item in values))
    return {key: statistics.median(item[key] for item in values) for key in keys}, history


def hourly_sfp_trend(reports, cycle_id, point, now):
    """Equal-weight UTC hourly medians regressed on actual elapsed days."""
    from datetime import timedelta, timezone
    result = {'quality': 'insufficient_coverage', 'slope_w_per_m3s_day': None,
              'bucket_count': 0, 'span_hours': 0, 'first_hour': None, 'last_hour': None}
    if now.tzinfo is None:
        raise ValueError('now must be timezone aware')
    now = now.astimezone(timezone.utc)
    cutoff = now - timedelta(days=7)
    buckets = {}
    for report in reports:
        if report.get('cycle_id') != cycle_id or report.get('point') != point:
            continue
        try:
            timestamp = datetime.fromisoformat(report['reported_at']).astimezone(timezone.utc)
            value = float(report['sfp'])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(value) or not cutoff <= timestamp <= now:
            continue
        hour = timestamp.replace(minute=0, second=0, microsecond=0)
        buckets.setdefault(hour, []).append(value)
    if not buckets:
        return result
    hours = sorted(buckets)
    span = (hours[-1] - hours[0]).total_seconds() / 3600
    result.update(bucket_count=len(hours), span_hours=span,
                  first_hour=hours[0].isoformat(), last_hour=hours[-1].isoformat())
    if len(hours) < 24 or span < 72:
        return result
    xs = [(hour - hours[0]).total_seconds() / 86400 for hour in hours]
    ys = [statistics.median(buckets[hour]) for hour in hours]
    xbar = statistics.mean(xs)
    ybar = statistics.mean(ys)
    denominator = sum((x - xbar) ** 2 for x in xs)
    if denominator == 0:
        return result
    slope = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys)) / denominator
    result.update(quality='ready', slope_w_per_m3s_day=slope * 1000)
    return result
