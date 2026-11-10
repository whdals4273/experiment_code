"""Resolve signed VCAP intent into two normally wired, positive PSU settings."""
from __future__ import annotations

import math
from typing import Any, Mapping


UPDATE_COMMANDS = {"UPDATE", "UPDATE_AND_READ", "RETENTION_TEST", "READ_DISTURB_TEST"}
UPDATE_INPUT_DEFAULTS = {"use_vcap_input": False, "vcap_v": 0.0}
UPDATE_INPUT_TOOLTIPS = {
    "use_vcap_input": "체크하면 VCAP으로 UPDATE 전압을 계산합니다. 해제하면 BLTOP/BLBTM을 직접 입력합니다. 두 BL ON 전원은 정상 결선입니다.",
    "vcap_v": "목표 VCAP = BLTOP − BLBTM (V). 양수: TOP=VCAP, BTM=0. 음수: TOP=0, BTM=|VCAP|. 실제 저장값은 소자와 측정 조건에 따라 달라집니다.",
}


def uses_vcap_input(cfg: Mapping[str, Any]) -> bool:
    value = cfg.get("use_vcap_input", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise ValueError("use_vcap_input must be true or false.")


def resolve_update_voltages(cfg: Mapping[str, Any]) -> tuple[float, float]:
    """Read only active inputs; never overwrite the saved manual setpoints."""
    def number(key):
        try:
            value = float(cfg[key])
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            raise ValueError(f"{key}: enter a finite voltage.") from exc
        if not math.isfinite(value):
            raise ValueError(f"{key}: enter a finite voltage.")
        return value

    if uses_vcap_input(cfg):
        cap = number("vcap_v")
        return max(cap, 0.0), max(-cap, 0.0)
    top, bottom = number("bltop_v"), number("blbtm_v")
    if top < 0 or bottom < 0:
        raise ValueError("bltop_v/blbtm_v must be nonnegative PSU magnitudes for normal wiring.")
    return top, bottom
