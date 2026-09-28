"""Version 2 monitor calculations, isolated from legacy history."""


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
