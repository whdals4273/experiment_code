"""Configuration and ideal RC prediction for the resistor/integrator check."""

import math


ADC_VERIFY_DEFAULTS = {
    "adc_channel": 0,
    "adc_resistance_kohm": 100.0,
    "adc_capacitance_nf": 10.0,
    "adc_vread_v": 0.3,
    "adc_reverse_polarity": True,
    "adc_pulse_us": 1000,
    "adc_repeats": 3,
    "adc_samples": 4,
    "adc_inter_us": 25,
    "adc_settle_us": 250,
    "adc_vref_v": 3.3,
    "adc_vinp_v": 0.0,
    "adc_tolerance_pct": 10.0,
    "adc_tolerance_counts": 3.0,
}


def validate_adc_verify(cfg):
    """Return normalized settings; polarity describes wiring, not a PSU command."""
    values = cfg if isinstance(cfg, dict) else vars(cfg)
    result = {key: values.get(key, default) for key, default in ADC_VERIFY_DEFAULTS.items()}
    limits = {
        "adc_channel": (0, 4), "adc_pulse_us": (1, 1000000),
        "adc_repeats": (1, 10000), "adc_samples": (1, 4096),
        "adc_inter_us": (0, 1000000), "adc_settle_us": (0, 1000000),
    }
    for key, (low, high) in limits.items():
        try:
            number = float(result[key])
            if isinstance(result[key], bool) or not math.isfinite(number) or not number.is_integer() or not low <= number <= high:
                raise ValueError
            result[key] = int(number)
        except (ValueError, TypeError, OverflowError):
            raise ValueError(f"{key} must be an integer in {low}..{high}") from None
    for key in ("adc_resistance_kohm", "adc_capacitance_nf", "adc_vread_v", "adc_vref_v", "adc_vinp_v", "adc_tolerance_pct", "adc_tolerance_counts"):
        try:
            number = float(result[key])
            if not math.isfinite(number):
                raise ValueError
            if key in {"adc_resistance_kohm", "adc_capacitance_nf", "adc_vref_v"} and number <= 0:
                raise ValueError
            if key in {"adc_vread_v", "adc_tolerance_pct", "adc_tolerance_counts"} and number < 0:
                raise ValueError
            result[key] = number
        except (ValueError, TypeError, OverflowError):
            raise ValueError(f"Invalid {key}: {result[key]!r}") from None
    polarity = result["adc_reverse_polarity"]
    if isinstance(polarity, str):
        if polarity.lower() not in {"true", "false"}:
            raise ValueError("adc_reverse_polarity must be true or false")
        polarity = polarity.lower() == "true"
    if not isinstance(polarity, bool):
        raise ValueError("adc_reverse_polarity must be true or false")
    result["adc_reverse_polarity"] = polarity
    result["signed_vread_v"] = (-1 if polarity else 1) * result["adc_vread_v"]
    # kohm * nF and microseconds have the same scale factor.
    rc = result["adc_resistance_kohm"] * result["adc_capacitance_nf"]
    if not math.isfinite(rc) or rc <= 0:
        raise ValueError("R * C must be finite and positive")
    result["expected_delta_v"] = -(result["signed_vread_v"] - result["adc_vinp_v"]) * result["adc_pulse_us"] / rc
    result["expected_delta_adc"] = result["expected_delta_v"] / result["adc_vref_v"] * 1023.0
    tolerance = result["adc_tolerance_counts"] + abs(result["expected_delta_adc"]) * result["adc_tolerance_pct"] / 100.0
    if not all(math.isfinite(value) for value in (result["expected_delta_adc"], tolerance)):
        raise ValueError("ADC prediction is not finite; check R, C and pulse time")
    return result


def predict_adc_delta(cfg, pulse_us=None):
    values = validate_adc_verify(cfg)
    if pulse_us is None:
        return values["expected_delta_adc"]
    duration = float(pulse_us)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("pulse_us must be finite and positive")
    prediction = values["expected_delta_adc"] * (duration / values["adc_pulse_us"])
    if not math.isfinite(prediction):
        raise ValueError("ADC prediction is not finite")
    return prediction


def evaluate_adc_rows(rows, cfg):
    """Keep incomplete records visible; never grade clipped measurements as PASS."""
    settings = validate_adc_verify(cfg)
    output = []
    for original in rows:
        row = dict(original)
        if row.get("_type") != "ADC_VERIFY":
            output.append(row)
            continue
        row.update({key: settings[key] for key in ADC_VERIFY_DEFAULTS})
        row["signed_vread_v"] = settings["signed_vread_v"]
        row["nominal_delta_adc"] = settings["expected_delta_adc"]
        try:
            baseline, measured = float(row["baseline"]), float(row["adc"])
            duration = float(row["pulse_actual_us"])
            channel = float(row["channel"])
            repeat = float(row["repeat"])
            if not all(math.isfinite(value) for value in (baseline, measured, duration)) or duration <= 0:
                raise ValueError
            if not math.isfinite(repeat) or not repeat.is_integer() or not 1 <= repeat <= settings["adc_repeats"]:
                raise ValueError
            if channel != settings["adc_channel"] or not 0 <= baseline <= 1023 or not 0 <= measured <= 1023:
                raise ValueError
            for key, expected_value in (("pulse_us", settings["adc_pulse_us"]), ("samples", settings["adc_samples"])):
                if float(row[key]) != expected_value:
                    raise ValueError
            expected_delta = predict_adc_delta(settings, duration)
            expected = baseline + expected_delta
            delta = measured - baseline
            error = delta - expected_delta
            tolerance = settings["adc_tolerance_counts"] + abs(expected_delta) * settings["adc_tolerance_pct"] / 100.0
            if not all(math.isfinite(value) for value in (expected, error, tolerance)):
                raise ValueError
            row.update(expected_adc=expected, expected_delta_adc=expected_delta,
                       measured_delta_adc=delta, error_adc=error, tolerance_adc=tolerance,
                       expected_delta_v=expected_delta / 1023.0 * settings["adc_vref_v"])
            extremes = [float(row.get(key, measured)) for key in ("adc_min", "adc_max")]
            if not all(math.isfinite(value) and 0 <= value <= 1023 for value in extremes):
                raise ValueError
            if not 0 < expected < 1023:
                status = "EXPECTED_SATURATION"
            elif measured <= 0 or measured >= 1023 or any(value <= 0 or value >= 1023 for value in extremes):
                status = "MEASURED_SATURATION"
            elif abs(expected_delta) <= settings["adc_tolerance_counts"]:
                status = "LOW_SIGNAL"
            else:
                status = "PASS" if abs(error) <= tolerance else "FAIL"
            row["verification_status"] = status
        except (ValueError, KeyError, TypeError, OverflowError):
            row["verification_status"] = "INVALID_DATA"
        output.append(row)
    return output


def summarize_adc_verification(rows, cfg, terminal, error=None):
    """A run passes only after all requested repeats and the exact DONE reply."""
    settings = validate_adc_verify(cfg)
    measurements = [row for row in rows if row.get("_type") == "ADC_VERIFY"]
    repeats = []
    try:
        for row in measurements:
            value = float(row["repeat"])
            if not math.isfinite(value) or not value.is_integer():
                raise ValueError
            repeats.append(int(value))
    except (KeyError, ValueError, TypeError, OverflowError):
        repeats = []
    complete = (not error and terminal == "OK ADC_VERIFY_DONE"
                and len(measurements) == settings["adc_repeats"]
                and sorted(repeats) == list(range(1, settings["adc_repeats"] + 1)))
    statuses = [row.get("verification_status", "INVALID_DATA") for row in measurements]
    return {
        "status": "INCOMPLETE" if not complete else "PASS" if all(status == "PASS" for status in statuses) else "FAIL",
        "complete": bool(complete),
        "requested_repeats": settings["adc_repeats"],
        "received_repeats": len(measurements),
        "row_statuses": statuses,
    }
