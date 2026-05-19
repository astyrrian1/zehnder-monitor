"""Baseline-aware filter capacity helpers.

These helpers are deliberately pure so capability math can be unit-tested
without AppDaemon or Home Assistant.
"""


VALID_BASELINE_QUALITIES = ("conditioned", "single_sample")


def clamp_pct(value):
    """Clamp a numeric value into the 0-100 percentage range."""
    if value is None:
        return None
    return max(0.0, min(100.0, float(value)))


def capacity_remaining_pct(current, baseline, replace):
    """
    Remaining capacity relative to a captured clean-filter baseline.

    A value better than the baseline caps at 100%. Invalid thresholds return
    None so callers can mark dependent sensors unavailable.
    """
    if current is None or baseline is None or replace is None:
        return None
    if replace <= baseline:
        return None
    return round(clamp_pct((replace - current) / (replace - baseline) * 100.0), 1)


def baseline_system_resistance_pct(baseline_sfp, pristine_sfp, replace_sfp):
    """
    Inferred clean-filter system resistance on the generic SFP envelope.

    This is a telemetry-derived context metric, not a pressure measurement.
    """
    if baseline_sfp is None or replace_sfp <= pristine_sfp:
        return None
    return round(
        clamp_pct((baseline_sfp - pristine_sfp) / (replace_sfp - pristine_sfp) * 100.0),
        1,
    )


def weighted_filter_capacity_pct(sfp_capacity, duty_capacity):
    """Composite filter capacity with SFP weighted above duty ratio."""
    if sfp_capacity is None or duty_capacity is None:
        return None
    return round((sfp_capacity * 0.65) + (duty_capacity * 0.35), 1)


def compute_capability(
    current_sfp,
    current_ratio,
    baseline_sfp,
    baseline_ratio,
    sfp_pristine,
    sfp_replace,
    ratio_replace,
    baseline_quality,
):
    """Return the additive baseline-aware capability payload."""
    quality = baseline_quality or "invalid"
    result = {
        "filter_capacity_remaining": None,
        "sfp_capacity_remaining": None,
        "duty_capacity_remaining": None,
        "baseline_system_resistance": None,
        "baseline_quality": quality,
        "limiting_factor": None,
    }

    if quality not in VALID_BASELINE_QUALITIES:
        return result

    sfp_capacity = capacity_remaining_pct(current_sfp, baseline_sfp, sfp_replace)
    duty_capacity = capacity_remaining_pct(current_ratio, baseline_ratio, ratio_replace)
    filter_capacity = weighted_filter_capacity_pct(sfp_capacity, duty_capacity)

    result.update({
        "filter_capacity_remaining": filter_capacity,
        "sfp_capacity_remaining": sfp_capacity,
        "duty_capacity_remaining": duty_capacity,
        "baseline_system_resistance": baseline_system_resistance_pct(
            baseline_sfp, sfp_pristine, sfp_replace
        ),
    })

    if sfp_capacity is not None and duty_capacity is not None:
        result["limiting_factor"] = (
            "sfp" if sfp_capacity <= duty_capacity else "duty_ratio"
        )

    return result
