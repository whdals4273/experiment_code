from __future__ import annotations

__version__ = "0.1.0"

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

try:
    import pandas as pd
except Exception:
    pd = None

try:
    from src.configs.orchestration.local.parameter_analyzer_4155.plotting import (
        plot_bti_msm_overlay,
        plot_iv_sweep,
        plot_stress_test,
    )
except Exception:
    plot_bti_msm_overlay = None
    plot_iv_sweep = None
    plot_stress_test = None

CONFIG_DIR = Path(__file__).resolve().parent / "configs"

ExperimentRunner = Callable[..., Any]
Plotter = Callable[..., list[Path]]
ParamPrompt = Callable[[Any, Any], Optional[dict[str, Any]]]


def _extract_analyzer_macro(macro: Any) -> Any:
    return getattr(macro, "analyzer_macro", macro)


@dataclass(frozen=True)
class ExperimentSpec:
    key: str
    display_name: str
    runner: ExperimentRunner
    param_yaml_path: Path
    param_defaults: Optional[dict[str, Any]] = None
    param_prompt: Optional[ParamPrompt] = None
    plotter: Optional[Plotter] = None
    description: str = ""


def _config_path(root: Path, name: str) -> Path:
    return (root / name).resolve()


def _setdefault_path(params: dict[str, Any], key: str, value: Path) -> None:
    raw = params.get(key)
    text = str(raw).strip() if raw is not None else ""
    if not text:
        params[key] = str(value)


def _apply_plot_meta(
    result: Any,
    *,
    x_candidates: list[str],
    y_candidates: list[str],
    fallback_prefix: str | None = None,
) -> Any:
    data = getattr(result, "data", None)
    if pd is None or not isinstance(data, pd.DataFrame):
        return result

    cols = [str(col) for col in data.columns]
    if not cols:
        return result

    by_upper = {str(col).upper(): str(col) for col in cols}
    x_col = None
    for cand in x_candidates:
        found = by_upper.get(str(cand).upper())
        if found is not None:
            x_col = found
            break

    y_cols: list[str] = []
    for cand in y_candidates:
        found = by_upper.get(str(cand).upper())
        if found is None:
            continue
        if x_col is not None and found == x_col:
            continue
        if found not in y_cols:
            y_cols.append(found)

    if not y_cols and fallback_prefix is not None:
        prefix = str(fallback_prefix).upper()
        for col in cols:
            if x_col is not None and col == x_col:
                continue
            if col.upper().startswith(prefix):
                y_cols.append(col)

    if not y_cols:
        return result

    meta = getattr(result, "metadata", None)
    if not isinstance(meta, dict):
        return result

    plot_meta = meta.get("plot")
    if not isinstance(plot_meta, dict):
        plot_meta = {}
    if x_col is not None:
        plot_meta["x_col"] = x_col
    plot_meta["y_cols"] = y_cols
    meta["plot"] = plot_meta
    return result


# --------------------------------------------------------------------- #
#  Runner wrappers                                                       #
# --------------------------------------------------------------------- #

def _run_bti_transfer_curve(
    ctx: Any,
    analyzer: Any,
    macro: Any,
    params: dict[str, Any],
    *,
    config_dir: Path,
) -> Any:
    """Single transfer curve sweep for pre/post-stress characterization."""
    from src.macros.parameter_analyzer.iv_sweep.iv_sweep_runner import run_pa_iv_sweep

    merged = dict(params or {})
    merged["mode"] = "sweep"
    _setdefault_path(merged, "config_yaml_path", _config_path(config_dir, "pa_sweep_bti.yml"))
    result = run_pa_iv_sweep(ctx=ctx, analyzer=analyzer, macro=_extract_analyzer_macro(macro), params=merged)
    return _apply_plot_meta(result, x_candidates=["VG"], y_candidates=["ID", "IG"])


def _run_bti_msm_stress(
    ctx: Any,
    analyzer: Any,
    macro: Any,
    params: dict[str, Any],
    *,
    config_dir: Path,
) -> Any:
    """MSM stress test: periodic transfer curve sweeps at log-spaced stress intervals."""
    from src.macros.parameter_analyzer.stress_test.stress_test_runner import run_pa_stress_test

    merged = dict(params or {})
    merged["mode"] = "MSM"
    _setdefault_path(merged, "config_yaml_path", _config_path(config_dir, "pa_sweep_bti.yml"))
    result = run_pa_stress_test(ctx=ctx, analyzer=analyzer, macro=_extract_analyzer_macro(macro), params=merged)
    return _apply_plot_meta(result, x_candidates=["VG"], y_candidates=["ID", "IG"])


def _run_bti_otf_test(
    ctx: Any,
    analyzer: Any,
    macro: Any,
    params: dict[str, Any],
    *,
    config_dir: Path,
) -> Any:
    """OTF pulse/scope test: analyzer initial sweep, then pulse-generator stress with oscilloscope-gated read pulses."""
    from src.macros.parameter_analyzer.igzo_stability.otf_pulse_runner import run_igzo_otf_pulse_test

    merged = dict(params or {})
    _setdefault_path(merged, "config_yaml_path", _config_path(config_dir, "pa_sweep_bti.yml"))
    analyzer_macro = getattr(macro, "analyzer_macro", None)
    pulsegen_macro = getattr(macro, "pulsegen_macro", None)
    oscope_macro = getattr(macro, "oscope_macro", None)
    if analyzer_macro is None or pulsegen_macro is None or oscope_macro is None:
        raise TypeError(
            "bti_otf_test requires an instrument bundle with "
            "analyzer_macro, pulsegen_macro, and oscope_macro."
        )
    return run_igzo_otf_pulse_test(
        ctx=ctx,
        analyzer_macro=analyzer_macro,
        pulsegen_macro=pulsegen_macro,
        oscope_macro=oscope_macro,
        params=merged,
    )


# --------------------------------------------------------------------- #
#  Registry                                                              #
# --------------------------------------------------------------------- #

def get_registry(config_dir: str | Path | None = None) -> dict[str, ExperimentSpec]:
    root = Path(config_dir).resolve() if config_dir is not None else CONFIG_DIR

    reg: dict[str, ExperimentSpec] = {}

    reg["bti_transfer_curve"] = ExperimentSpec(
        key="bti_transfer_curve",
        display_name="Transfer Curve (Pre/Post Stress)",
        runner=lambda ctx, analyzer, macro, params, _root=root: _run_bti_transfer_curve(
            ctx, analyzer, macro, params, config_dir=_root,
        ),
        param_yaml_path=_config_path(root, "bti_transfer_curve_params.yml"),
        param_defaults=None,
        param_prompt=None,
        plotter=plot_iv_sweep,
        description="Single I_DS-V_GS transfer curve sweep for pre- or post-stress device characterization.",
    )

    reg["bti_msm_test"] = ExperimentSpec(
        key="bti_msm_test",
        display_name="MSM Stress Test",
        runner=lambda ctx, analyzer, macro, params, _root=root: _run_bti_msm_stress(
            ctx, analyzer, macro, params, config_dir=_root,
        ),
        param_yaml_path=_config_path(root, "bti_msm_stress_params.yml"),
        param_defaults=None,
        param_prompt=None,
        plotter=plot_bti_msm_overlay,
        description="Measure-Stress-Measure: DC bias stress with periodic transfer curve sweeps at log-spaced time points.",
    )

    reg["bti_otf_test"] = ExperimentSpec(
        key="bti_otf_test",
        display_name="OTF Pulse/Scope Test",
        runner=lambda ctx, analyzer, macro, params, _root=root: _run_bti_otf_test(
            ctx, analyzer, macro, params, config_dir=_root,
        ),
        param_yaml_path=_config_path(root, "bti_otf_test_params.yml"),
        param_defaults=None,
        param_prompt=None,
        plotter=None,
        description="Initial analyzer sweep, then continuous pulse-generator stress with oscilloscope-arm and read-pulse capture at every OTF checkpoint.",
    )

    return reg
