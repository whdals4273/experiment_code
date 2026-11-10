"""Host-side K/Q grid planning and reduction; no hardware or GUI dependencies."""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from statistics import mean, stdev
from typing import Any, Mapping


LOOKUP_COMMAND = "LOOK_UP_TABLE_TEST"
LOOKUP_DEFAULTS = {
    "lut_vcap_values": "-1.65, -1, 0, 1, 1.65",
    "lut_q_values": "-1, -0.5, 0, 0.5, 1",
    "lut_cycles": 1,
}
LOOKUP_TOOLTIPS = {
    "lut_vcap_values": "LOOK_UP_TABLE_TEST 전용. 쉼표로 구분한 VCAP(V) 목록. 각 조합마다 TOP=max(K,0), BTM=max(-K,0)로 다시 씁니다.",
    "lut_q_values": "LOOK_UP_TABLE_TEST 전용. −1~1의 q 목록. 표의 행은 q, 열은 VCAP이며 입력 순서를 유지합니다.",
    "lut_cycles": "각 K·q 조합의 ACTUAL+REFERENCE 반복 횟수. 1이면 delta 하나, 여러 회면 delta 평균을 표에 표시합니다. 일반 cycles 대신 이 값을 사용합니다.",
}


@dataclass(frozen=True)
class LookupSettings:
    caps: tuple[float, ...]
    queries: tuple[float, ...]
    cycles: int

    @property
    def point_count(self):
        return len(self.caps) * len(self.queries)


def _axis(value: Any, name: str) -> tuple[float, ...]:
    if isinstance(value, str):
        value = re.split(r"[,\s]+", value.strip())
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{name}: enter a comma-separated list of finite numbers.")
    try:
        numbers = tuple(float(item) for item in value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{name}: enter a comma-separated list of finite numbers.") from exc
    if not all(math.isfinite(number) for number in numbers):
        raise ValueError(f"{name}: values must be finite.")
    if len(set(numbers)) != len(numbers):
        raise ValueError(f"{name}: remove duplicate values.")
    return numbers


def lookup_settings(cfg: Mapping[str, Any]) -> LookupSettings:
    caps = _axis(cfg.get("lut_vcap_values", LOOKUP_DEFAULTS["lut_vcap_values"]), "lut_vcap_values")
    queries = _axis(cfg.get("lut_q_values", LOOKUP_DEFAULTS["lut_q_values"]), "lut_q_values")
    if any(not -1 <= q <= 1 for q in queries):
        raise ValueError("lut_q_values: every q must be from -1 to 1.")
    try:
        cycles = float(cfg.get("lut_cycles", 1))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("lut_cycles: enter an integer from 1 to 10000.") from exc
    if not math.isfinite(cycles) or not cycles.is_integer() or not 1 <= cycles <= 10000:
        raise ValueError("lut_cycles: enter an integer from 1 to 10000.")
    return LookupSettings(caps, queries, int(cycles))


def point_config(cfg: Mapping[str, Any], cap: float, query: float, cycles: int) -> dict[str, Any]:
    # Always rewrite K for each pair: a previous REFERENCE read must not become
    # the starting condition of a different q experiment.
    return dict(cfg, command_name="READ_DISTURB_TEST", use_vcap_input=True,
                vcap_v=cap, q=query, cycles=cycles)


def summarize_point(rows, cycles: int, command_ok: bool) -> dict[str, Any]:
    """Only a complete set of valid pairs can populate a table cell."""
    valid = []
    indices = []
    for row in rows:
        try:
            idx = int(row["idx"])
            actual = float(row.get("actual_avg", row.get("actual", "nan")))
            reference = float(row.get("reference_avg", row.get("reference", "nan")))
            delta = float(row["delta"])
            if (row.get("timing_schema") == "invalid" or not 0 <= idx < cycles
                    or not 0 <= actual <= 1023 or not 0 <= reference <= 1023
                    or not math.isfinite(delta)
                    or not math.isclose(delta, actual-reference, rel_tol=0, abs_tol=0.002)):
                continue
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        indices.append(idx)
        valid.append((actual, reference, delta))
    complete = (command_ok and len(rows) == cycles and sorted(indices) == list(range(cycles)))
    deltas = [row[2] for row in valid]
    return {
        "status": "completed" if complete else "incomplete",
        "received_cycles": len(set(indices)), "requested_cycles": cycles,
        "actual_mean": mean(row[0] for row in valid) if valid else "",
        "reference_mean": mean(row[1] for row in valid) if valid else "",
        "delta_mean": mean(deltas) if valid else "",
        "delta_std": stdev(deltas) if len(deltas) > 1 else 0.0 if deltas else "",
    }


def lookup_matrix(settings: LookupSettings, points) -> list[list[float | None]]:
    matrix = [[None for _ in settings.caps] for _ in settings.queries]
    for point in points:
        if point["status"] == "completed":
            matrix[point["q_index"]][point["k_index"]] = float(point["delta_mean"])
    return matrix


def plot_lookup_table(plt, settings: LookupSettings, points, path, cfg):
    """The GUI opens this same PNG; missing/failed cells are never filled."""
    matrix = lookup_matrix(settings, points)
    cells = [["—" if value is None else f"{value:.3f}".rstrip("0").rstrip(".")
              for value in row] for row in matrix]
    complete = sum(point["status"] == "completed" for point in points)
    fig, ax = plt.subplots(figsize=(max(7, len(settings.caps)*1.1+1.5),
                                    max(4, len(settings.queries)*0.5+2)))
    ax.axis("off")
    table = ax.table(cellText=cells, rowLabels=[f"{q:g}" for q in settings.queries],
                     colLabels=[f"{k:g}" for k in settings.caps],
                     cellLoc="center", rowLoc="center", loc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1, 1.8)
    ax.set_title(f"LOOK_UP_TABLE_TEST ({complete}/{settings.point_count} combinations)\n"
                 "Columns: K / VCAP (V)    |    Rows: q\n"
                 "Mean Delta = Actual - Reference (ADC counts)", pad=16)
    fig.text(0.5, 0.05, f"{settings.cycles} pair(s)/combination  |  precision={cfg.get('precision', 8)}"
             f"  |  step_us={cfg.get('step_us', 10)} us\n"
             "Each combination rewrites VCAP. Blank (—): unmeasured or incomplete.",
             ha="center", fontsize=9)
    fig.tight_layout(rect=(0.04, 0.13, 1, 1))
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"Plot saved: {path}")
    return path
