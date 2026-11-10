"""Hardware-free timing summary for the 4T1C parameter editor.

This describes requested firmware delays, not measured waveforms.
PSU/OPC, GPIO, ADC and serial overhead have no invented duration. In particular,
``read_min_us`` is the firmware's preflight lower bound, not a promised period.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
import struct
from typing import Any, Mapping

from .adc_verify import validate_adc_verify
from .update_bias import resolve_update_voltages, uses_vcap_input
from .lookup_table import LOOKUP_COMMAND, lookup_settings, point_config

# Must match the standalone raw-sample buffer in firmware 2.4.3.
MAX_READ_SAMPLES = 4096


@dataclass(frozen=True)
class Preview:
    command: str
    supported: bool = True
    update: bool = False
    has_read: bool = False
    compound: bool = False
    hold_read_bias: bool = False
    pair: bool = False
    adc_verify: bool = False
    lookup_table: bool = False
    lookup_caps: tuple[float, ...] = ()
    lookup_queries: tuple[float, ...] = ()
    lookup_cycles: int = 1
    adc_channel: int = 0
    resistance_kohm: float = 0.0
    capacitance_nf: float = 0.0
    signed_vread_v: float = 0.0
    vinp_v: float = 0.0
    expected_delta_v: float = 0.0
    expected_delta_adc: float = 0.0
    count: int = 1
    count_label: str = "read"
    q: float = 0.0
    precision: int = 0
    step_us: int = 0
    positive_q: bool = True
    wl_label: str = ""
    actual_route: str = ""
    reference_route: str = ""
    update_pulse_us: int = 0
    update_repeat: int = 0
    update_settle_us: int = 0
    update_gate_settle_us: int = 0
    update_top_v: float = 0.0
    update_bottom_v: float = 0.0
    update_cap_v: float = 0.0
    use_vcap_input: bool = False
    read_bias_v: float = 0.0
    initial_wait_us: int = 0
    interval_us: int = 0
    post_us: int = 0
    total_slots: int = 0
    on_slots: int = 0
    frame_us: int = 0
    pulse_us: int = 0
    read_delay_us: int = 0
    bias_settle_us: int = 0
    gate_settle_us: int = 0
    settle_us: int = 0
    samples: int = 0
    warmup: int = 0
    inter_us: int = 0
    adc_wait_us: int = 0
    reset_us: int = 3000
    read_min_us: int = 0
    block_min_us: int = 0
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


_SUPPORTED = {
    "UPDATE", "READ_ACTUAL", "READ_REFERENCE", "UPDATE_AND_READ",
    "RETENTION_TEST", "READ_DISTURB_TEST",
}
_UPDATE_COMMANDS = {
    "UPDATE", "UPDATE_AND_READ", "RETENTION_TEST", "READ_DISTURB_TEST",
}


def _number(cfg: Mapping[str, Any], key: str, default: float) -> float:
    try:
        value = float(cfg.get(key, default))
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{key}: enter a finite number.") from exc
    if not math.isfinite(value):
        raise ValueError(f"{key}: enter a finite number.")
    return value


def _integer(cfg: Mapping[str, Any], key: str, default: int,
             minimum: int = 0, maximum: int = 0x7fffffff) -> int:
    value = _number(cfg, key, default)
    if not value.is_integer() or not minimum <= value <= maximum:
        raise ValueError(f"{key}: enter an integer from {minimum} to {maximum}.")
    return int(value)


def _adc_preview(cfg: Mapping[str, Any]) -> Preview:
    values = validate_adc_verify(cfg)
    pulse = values["adc_pulse_us"]
    settle = values["adc_settle_us"]
    samples = values["adc_samples"]
    inter = values["adc_inter_us"]
    adc_wait = (samples - 1) * inter
    # A matched zero-input baseline and signal each have their own hold/ADC
    # window, with a 3 ms reset before and after each acquisition.
    repeat_min = 4 * 3000 + 2 * (pulse + settle + adc_wait)
    channel = values["adc_channel"]
    return Preview(
        command="ADC_VERIFY", has_read=True, adc_verify=True,
        adc_channel=channel, count=values["adc_repeats"], count_label="repeats",
        wl_label=f"WLU{channel}",
        actual_route=f"WLU{channel} → R → BLU{channel} → integrator → ADC{channel}",
        resistance_kohm=values["adc_resistance_kohm"],
        capacitance_nf=values["adc_capacitance_nf"],
        signed_vread_v=values["signed_vread_v"], vinp_v=values["adc_vinp_v"],
        expected_delta_v=values["expected_delta_v"],
        expected_delta_adc=values["expected_delta_adc"],
        pulse_us=pulse, frame_us=pulse, settle_us=settle,
        samples=samples, inter_us=inter, adc_wait_us=adc_wait,
        read_min_us=repeat_min, block_min_us=repeat_min,
        warnings=("이상적 R·C 계산값이며 실측 파형이 아닙니다.",
                  "반복 시간에는 ADC 변환·GPIO·통신 처리 시간이 추가됩니다."),
    )


def build_preview(cfg: Mapping[str, Any]) -> Preview:
    """Summarize currently edited values without saving or accessing hardware.

    Invalid or incomplete *active* timing values raise ``ValueError`` so an
    editor can replace the previous plot with an input hint. Unused command
    fields are ignored. Impossible compound periods are returned in ``errors``
    so the requested scheme can still be shown, visibly marked invalid.
    """
    command = str(cfg.get("command_name", "READ_ACTUAL")).strip().upper()
    if command == LOOKUP_COMMAND:
        settings = lookup_settings(cfg)
        children = [build_preview(point_config(cfg, k, q, settings.cycles))
                    for q in settings.queries for k in settings.caps]
        return replace(children[0], command=command, lookup_table=True,
                       lookup_caps=settings.caps, lookup_queries=settings.queries,
                       lookup_cycles=settings.cycles,
                       errors=tuple(dict.fromkeys(error for child in children for error in child.errors)))
    if command == "ADC_VERIFY":
        return _adc_preview(cfg)
    if command not in _SUPPORTED:
        return Preview(command=command, supported=False,
                       warnings=("No pulse preview for this command.",))

    data: dict[str, Any] = {
        "command": command,
        "update": command in _UPDATE_COMMANDS,
        "has_read": command != "UPDATE",
        "compound": command in ("RETENTION_TEST", "READ_DISTURB_TEST"),
        "hold_read_bias": command in ("UPDATE_AND_READ", "RETENTION_TEST", "READ_DISTURB_TEST"),
        "pair": command == "READ_DISTURB_TEST",
    }
    warnings = ["Scheme only; PSU/OPC, GPIO, ADC and serial overhead are not to scale."]
    errors = []
    if data["update"]:
        data["update_top_v"], data["update_bottom_v"] = resolve_update_voltages(
            {"bltop_v": 1.0, "blbtm_v": 1.0, **cfg})
        data["use_vcap_input"] = uses_vcap_input(cfg)
        data["update_cap_v"] = data["update_top_v"] - data["update_bottom_v"]
        data["update_pulse_us"] = _integer(cfg, "pw_us", 100, minimum=1)
        data["update_repeat"] = _integer(cfg, "repeat", 1, minimum=1)
        data["update_settle_us"] = _integer(cfg, "settle_after_opc_us", 50)
        data["update_gate_settle_us"] = _integer(cfg, "update_gate_settle_us", 50)

    if command == "UPDATE_AND_READ":
        data["post_us"] = _integer(cfg, "post_us", 0)

    if data["has_read"]:
        data["read_bias_v"] = _number(cfg, "write_bl_topbtm_v", 1.65)
        if data["read_bias_v"] < 0:
            raise ValueError("write_bl_topbtm_v: enter a nonnegative read bias.")
        q = _number(cfg, "q", 1.0)
        if not -1.0 <= q <= 1.0:
            raise ValueError("q: enter a value from -1 to 1.")
        precision = _integer(cfg, "precision", 8, minimum=1, maximum=16)
        step_us = _integer(cfg, "step_us", 10, minimum=1)
        # Arduino stores q as float (binary32), then floors the double product.
        q_float32 = struct.unpack("f", struct.pack("f", q))[0]
        slots = 1 << precision
        on_slots = math.floor(abs(q_float32) * slots + 1e-9)
        positive = q_float32 >= 0.0
        top_route = "BLTOP ON -> N3 -> S1"
        bottom_route = "BLBTM ON -> N4 -> S2"
        data.update(
            q=q, precision=precision, step_us=step_us,
            positive_q=positive, wl_label="WLU0 / S3" if positive else "WLD0 / S4",
            actual_route=bottom_route if positive else top_route,
            reference_route=top_route if positive else bottom_route,
            total_slots=slots, on_slots=on_slots,
            frame_us=slots * step_us, pulse_us=on_slots * step_us,
            read_delay_us=_integer(cfg, "read_delay", 0) * 10,
            bias_settle_us=_integer(cfg, "write_bl_settle_after_opc_us", 50),
            gate_settle_us=_integer(cfg, "write_bl_gate_settle_us", 50),
            settle_us=_integer(cfg, "settle_us", 250),
            samples=_integer(cfg, "samples", 1, minimum=1,
                             maximum=MAX_READ_SAMPLES if command in ("READ_ACTUAL", "READ_REFERENCE") else 2099202),
            warmup=_integer(cfg, "warmup", 0),
            inter_us=_integer(cfg, "inter_us", 50),
        )
        data["adc_wait_us"] = (data["warmup"] + data["samples"] - 1) * data["inter_us"]
        data["read_min_us"] = sum(data[key] for key in (
            "frame_us", "read_delay_us", "settle_us", "adc_wait_us"
        )) + 3000
        if not data["hold_read_bias"]:
            data["read_min_us"] += data["gate_settle_us"]
        data["block_min_us"] = data["read_min_us"] * (2 if data["pair"] else 1)
        if on_slots == 0:
            warnings.append("No VREAD ON slot; the complete read frame and ADC/reset still run.")

    if data["hold_read_bias"]:
        warnings.append("Both BL biases/MUXes stay ON through read, ADC and reset; only S1/S2 select access.")

    if data["compound"]:
        count_key = "cycles" if data["pair"] else "points"
        data["count"] = _integer(cfg, count_key, 10, minimum=1)
        data["count_label"] = count_key
        data["initial_wait_us"] = _integer(cfg, "initial_wait_ms", 0) * 1000
        data["interval_us"] = _integer(cfg, "interval_ms", 1000) * 1000
        warnings.append("initial_wait_ms starts after PSU/MUX preparation.")
        if data["count"] > 1:
            if data["interval_us"] == 0:
                warnings.append("interval_ms=0: fastest available; no fixed start-to-start period.")
            elif data["interval_us"] < data["block_min_us"]:
                errors.append(
                    f"PERIOD_TOO_SHORT: {data['interval_us']} us requested; "
                    f"at least {data['block_min_us']} us of configured delays required."
                )
            else:
                warnings.append("Period is a target between ACTUAL starts; runtime overhead can cause PERIOD_OVERRUN.")
        else:
            warnings.append("One measurement only; interval_ms is unused.")
    if data["hold_read_bias"]:
        update_min_us = (data["update_pulse_us"] * data["update_repeat"]
                         + data["update_settle_us"] + data["update_gate_settle_us"])
        if data["read_min_us"] > 0x7fffffff or update_min_us > 0x7fffffff:
            errors.append("BAD_ARG: configured timing block exceeds the firmware limit.")

    data["warnings"] = tuple(warnings)
    data["errors"] = tuple(errors)
    return Preview(**data)
