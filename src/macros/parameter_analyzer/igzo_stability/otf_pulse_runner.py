from __future__ import annotations

__version__ = "0.5.0"

from copy import deepcopy
import json
from pathlib import Path
from queue import Queue
import threading
import time
from typing import Any

import pandas as pd
try:
    from tqdm import tqdm
except Exception:
    tqdm = None

from src.macros.parameter_analyzer.stress_test.stress_test_runner import (
    _append_experiment_log,
    _as_channel_voltage_map,
    _as_opt_float,
    _as_opt_int,
    _as_opt_path,
    _build_initial_sweep_config,
    _coerce_dataframe,
    _configure_macro,
    _dedup_paths,
    _load_macro_yaml_metadata,
    _normalize_pulse_sequence,
    _resolve_main_config_path,
    _resolve_stress_condition_sequence,
)


def _resolve_named_bias_map(analyzer_macro: Any, condition_map: dict[str, float]) -> dict[str, float]:
    collect = getattr(analyzer_macro, "_collect_active_stress_source_channels", None)
    getter = getattr(analyzer_macro, "_get_source_channel_dict", None)
    if not callable(collect) or not callable(getter):
        return {}

    named: dict[str, float] = {}
    for channel in collect():
        key = str(channel).strip().upper()
        if key not in condition_map:
            continue
        cfg = getter(channel) or {}
        vname = str(cfg.get("vname", "")).strip().upper()
        if not vname:
            continue
        named[vname] = float(condition_map[key])
    return named


def _extract_read_level(
    settings: dict[str, Any],
    *,
    stress_v: float,
    channel_name: str,
) -> float:
    for key in ("read_v", "read_voltage", "read_level", "pulse_v", "pulse_level"):
        if key in settings and settings.get(key) not in (None, ""):
            return float(settings[key])

    high = _as_opt_float(settings.get("high"))
    low = _as_opt_float(settings.get("low"))
    if high is not None and low is not None:
        tol = 1.0e-12
        if abs(high - stress_v) <= tol and abs(low - stress_v) > tol:
            return float(low)
        if abs(low - stress_v) <= tol and abs(high - stress_v) > tol:
            return float(high)

    raise ValueError(
        f"Could not infer {channel_name} read voltage for OTF pulse generation. "
        f"Provide channel settings with one of read_v/read_voltage/read_level."
    )


def _build_otf_channel_settings(
    template: dict[str, Any],
    *,
    stress_v: float,
    read_v: float,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("period", "width", "delay", "rise", "fall", "zsource", "zload"):
        if key in template and template.get(key) not in (None, ""):
            out[key] = template[key]

    # Keep the recipe intent explicit: idle/baseline = stress, pulse = read.
    # The 81110A driver already normalizes a reversed HIGH/LOW pair and flips
    # polarity when needed, so the runner only needs to express the two states.
    out["high"] = float(read_v)
    out["low"] = float(stress_v)
    return out


def _prepare_otf_recipe(
    pulse_entry: dict[str, Any],
    *,
    gate_stress_v: float,
    drain_stress_v: float,
) -> tuple[dict[str, Any], str]:
    recipe = {k: v for k, v in dict(pulse_entry).items() if k != "label"}
    channel_1_template = dict(recipe.get("channel_1_settings") or {})
    channel_2_template = dict(recipe.get("channel_2_settings") or {})

    if channel_1_template:
        gate_read_v = _extract_read_level(channel_1_template, stress_v=gate_stress_v, channel_name="gate")
        recipe["channel_1_settings"] = _build_otf_channel_settings(
            channel_1_template,
            stress_v=gate_stress_v,
            read_v=gate_read_v,
        )
    if channel_2_template:
        drain_read_v = _extract_read_level(channel_2_template, stress_v=drain_stress_v, channel_name="drain")
        recipe["channel_2_settings"] = _build_otf_channel_settings(
            channel_2_template,
            stress_v=drain_stress_v,
            read_v=drain_read_v,
        )

    recipe.setdefault("mode", "trigger")
    recipe.setdefault("enable_channel_1", True)
    recipe.setdefault("enable_channel_2", True)
    signature = json.dumps(recipe, sort_keys=True, default=str)
    return recipe, signature


def _program_otf_recipe(
    pulsegen_macro: Any,
    recipe: dict[str, Any],
    *,
    enable_outputs: bool = True,
) -> None:
    pulsegen_macro.enable_channel(1, False)
    pulsegen_macro.enable_channel(2, False)

    channel_1_settings = recipe.get("channel_1_settings")
    if isinstance(channel_1_settings, dict) and channel_1_settings:
        pulsegen_macro.set_channel(1, channel_1_settings)

    channel_2_settings = recipe.get("channel_2_settings")
    if isinstance(channel_2_settings, dict) and channel_2_settings:
        pulsegen_macro.set_channel(2, channel_2_settings)

    pulsegen_macro.enable_channel(1, enable_outputs and bool(recipe.get("enable_channel_1", True)))
    pulsegen_macro.enable_channel(2, enable_outputs and bool(recipe.get("enable_channel_2", True)))

    settle_s = max(float(recipe.get("settle_s", 0.0) or 0.0), 0.0)
    if settle_s > 0.0:
        time.sleep(settle_s)


def _enable_otf_stress_outputs(
    pulsegen_macro: Any,
    recipe: dict[str, Any],
) -> float:
    pulsegen_macro.enable_channel(1, bool(recipe.get("enable_channel_1", True)))
    pulsegen_macro.enable_channel(2, bool(recipe.get("enable_channel_2", True)))
    return time.perf_counter()


def _wait_for_otf_checkpoint(
    *,
    due_perf: float,
    show_progress: bool,
) -> None:
    remaining_s = due_perf - time.perf_counter()
    if remaining_s <= 0.0:
        return

    if not show_progress or tqdm is None:
        time.sleep(remaining_s)
        return

    total_s = max(float(remaining_s), 0.0)
    progress = tqdm(
        total=total_s,
        unit="s",
        desc="Stress phase progress",
    )
    try:
        while True:
            remaining_s = due_perf - time.perf_counter()
            elapsed_s = total_s - max(remaining_s, 0.0)
            progress.n = min(max(elapsed_s, 0.0), total_s)
            progress.refresh()
            if remaining_s <= 0.0:
                break
            time.sleep(min(0.1, remaining_s))
        progress.n = total_s
        progress.refresh()
    finally:
        progress.close()


def _sync_macro_stress_overrides(
    analyzer_macro: Any,
    stress_channel_values: dict[str, float],
) -> None:
    if not stress_channel_values:
        return

    merged_stress_map = dict(getattr(analyzer_macro, "stress_channel_values", {}) or {})
    merged_stress_map.update({str(k).strip().upper(): float(v) for k, v in stress_channel_values.items()})
    analyzer_macro.stress_channel_values = merged_stress_map

    getter = getattr(analyzer_macro, "_get_source_channel_dict", None)
    if callable(getter):
        for channel, value in merged_stress_map.items():
            cfg = getter(channel)
            if isinstance(cfg, dict):
                cfg["stress_vdc"] = float(value)


def _configure_otf_switch(
    params: dict[str, Any],
) -> tuple[Any | None, dict[str, Any]]:
    switch_resource = str(params.get("switch_resource", "") or "").strip()
    if not switch_resource:
        return None, {}

    from src.macros.switch_matrix.switch_matrix_macro import SwitchMatrixMacro

    switch_connect_mode = str(params.get("switch_connect_mode", "auto") or "auto").strip().lower()
    switch_debug = bool(params.get("switch_debug", False))
    switch_macro = SwitchMatrixMacro(
        switch_resource,
        debug_mode=switch_debug,
        connect_mode=switch_connect_mode,
    )
    switch_macro.connect()

    route_config_path = _as_opt_path(params.get("switch_route_config_path"))
    route_configurations = params.get("switch_route_configurations")
    if route_config_path is not None:
        switch_macro.load_route_config_file(route_config_path)
    elif route_configurations:
        if not isinstance(route_configurations, dict):
            raise ValueError("switch_route_configurations must be a mapping when provided.")
        switch_macro.set_route_configurations(route_configurations)
    else:
        raise ValueError(
            "OTF switch support requires switch_route_config_path or switch_route_configurations."
        )

    return switch_macro, {
        "resource": switch_resource,
        "connect_mode": switch_connect_mode,
        "route_config_path": str(route_config_path) if route_config_path is not None else None,
        "route_keys": sorted(list(getattr(switch_macro, "route_configurations", {}).keys())),
    }


def _apply_otf_switch_route(
    switch_macro: Any | None,
    *,
    route_key: str,
    clear_previous: bool,
    force_reapply: bool,
    settle_s: float,
) -> None:
    if switch_macro is None:
        return
    key = str(route_key or "").strip()
    if not key:
        raise ValueError("OTF switch routing requires a non-empty route key.")
    switch_macro.apply_routing(
        key,
        clear_previous=clear_previous,
        force_reapply=force_reapply,
    )
    if settle_s > 0.0:
        time.sleep(settle_s)


def _build_otf_oscope_config(
    params: dict[str, Any],
    *,
    base_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = deepcopy(base_config or {})

    window_cfg = dict(cfg.get("window") or {})
    timebase_cfg = dict(cfg.get("timebase") or {})
    trigger_cfg = dict(cfg.get("trigger") or {})
    chan1_cfg = dict(cfg.get("chan1") or {})
    chan2_cfg = dict(cfg.get("chan2") or {})

    def _float_param(key: str, fallback: float) -> float:
        value = _as_opt_float(params.get(key))
        return float(fallback if value is None else value)

    def _int_param(key: str, fallback: int) -> int:
        value = _as_opt_int(params.get(key))
        return int(fallback if value is None else value)

    def _string_param(key: str, fallback: str) -> str:
        value = params.get(key, fallback)
        text = str(fallback if value in (None, "") else value).strip()
        return text or str(fallback)

    def _bool_param(key: str, fallback: bool) -> bool:
        value = params.get(key, fallback)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        token = str(value).strip().lower()
        if token in {"1", "true", "yes", "on"}:
            return True
        if token in {"0", "false", "no", "off"}:
            return False
        return bool(fallback)

    window_cfg["channel"] = _int_param("oscope_window_channel", int(window_cfg.get("channel", 1) or 1))
    window_cfg["voltage_scale_mv"] = _float_param(
        "oscope_window_voltage_scale_mv",
        500.0 if window_cfg.get("voltage_scale_mv") in (None, "") else float(window_cfg.get("voltage_scale_mv")),
    )
    window_cfg["offset_voltage_mv"] = _float_param(
        "oscope_window_offset_voltage_mv",
        0.0 if window_cfg.get("offset_voltage_mv") in (None, "") else float(window_cfg.get("offset_voltage_mv")),
    )
    window_cfg["delay_time_s"] = _float_param(
        "oscope_delay_time_s",
        0.0 if window_cfg.get("delay_time_s") in (None, "") else float(window_cfg.get("delay_time_s")),
    )
    window_cfg["time_position_s"] = _float_param(
        "oscope_time_position_s",
        0.0 if window_cfg.get("time_position_s") in (None, "") else float(window_cfg.get("time_position_s")),
    )

    timebase_cfg["scale"] = _float_param(
        "oscope_time_scale_s",
        1.0e-6 if timebase_cfg.get("scale") in (None, "") else float(timebase_cfg.get("scale")),
    )
    timebase_cfg["position"] = _float_param(
        "oscope_time_position_s",
        0.0 if timebase_cfg.get("position") in (None, "") else float(timebase_cfg.get("position")),
    )
    timebase_cfg["delay_time_s"] = window_cfg["delay_time_s"]

    trigger_cfg["mode"] = _string_param("oscope_trigger_mode", str(trigger_cfg.get("mode", "EDGE") or "EDGE"))
    trigger_cfg["source"] = _string_param(
        "oscope_trigger_source",
        str(trigger_cfg.get("source", "EXTernal") or "EXTernal"),
    )
    trigger_cfg["slope"] = _string_param(
        "oscope_trigger_slope",
        str(trigger_cfg.get("slope", "POSitive") or "POSitive"),
    )
    trigger_cfg["level"] = _float_param(
        "oscope_trigger_level_mv",
        0.0 if trigger_cfg.get("level") in (None, "") else float(trigger_cfg.get("level")),
    )
    trigger_cfg["coupling"] = _string_param(
        "oscope_trigger_coupling",
        str(trigger_cfg.get("coupling", "DC") or "DC"),
    )
    trigger_cfg["sweep"] = _string_param(
        "oscope_trigger_sweep",
        str(trigger_cfg.get("sweep", "NORM") or "NORM"),
    )

    chan1_cfg["display"] = _bool_param("oscope_chan1_display", bool(chan1_cfg.get("display", True)))
    chan1_cfg["coupling"] = _string_param("oscope_chan1_coupling", str(chan1_cfg.get("coupling", "DC") or "DC"))
    chan1_cfg["impedance"] = _string_param(
        "oscope_chan1_impedance",
        str(chan1_cfg.get("impedance", "ONEMeg") or "ONEMeg"),
    )
    chan1_cfg["offset"] = _float_param(
        "oscope_chan1_offset_mv",
        0.0 if chan1_cfg.get("offset") in (None, "") else float(chan1_cfg.get("offset")),
    )
    chan1_cfg["scale"] = _float_param(
        "oscope_chan1_scale_mv",
        500.0 if chan1_cfg.get("scale") in (None, "") else float(chan1_cfg.get("scale")),
    )

    chan2_cfg["display"] = _bool_param("oscope_chan2_display", bool(chan2_cfg.get("display", False)))
    chan2_cfg["coupling"] = _string_param("oscope_chan2_coupling", str(chan2_cfg.get("coupling", "DC") or "DC"))
    chan2_cfg["impedance"] = _string_param(
        "oscope_chan2_impedance",
        str(chan2_cfg.get("impedance", "ONEMeg") or "ONEMeg"),
    )
    chan2_cfg["offset"] = _float_param(
        "oscope_chan2_offset_mv",
        0.0 if chan2_cfg.get("offset") in (None, "") else float(chan2_cfg.get("offset")),
    )
    chan2_cfg["scale"] = _float_param(
        "oscope_chan2_scale_mv",
        500.0 if chan2_cfg.get("scale") in (None, "") else float(chan2_cfg.get("scale")),
    )

    cfg["window"] = window_cfg
    cfg["timebase"] = timebase_cfg
    cfg["trigger"] = trigger_cfg
    cfg["chan1"] = chan1_cfg
    cfg["chan2"] = chan2_cfg
    return cfg


def run_igzo_otf_pulse_test(
    ctx: Any,
    *,
    analyzer_macro: Any,
    pulsegen_macro: Any,
    oscope_macro: Any,
    params: dict[str, Any],
) -> Any:
    """
    Dedicated IGZO OTF runner.

    The analyzer is used only for the optional initial sweep and for loading the
    scenario YAML metadata (stress time list / channel naming). Continuous
    stress and read pulses are owned by the pulse generator, while the
    oscilloscope is armed and read out around each triggered read pulse.
    """
    from src.configs.orchestration.local.parameter_analyzer_4155.experiment_orchestrator import RunResult

    run_dir = Path(getattr(ctx, "run_dir"))
    run_dir.mkdir(parents=True, exist_ok=True)

    config_yaml_path = _as_opt_path(params.get("config_yaml_path"))
    fast_sweep = bool(params.get("fast_sweep", True))
    debug_timing = bool(params.get("debug_timing", False))
    show_progress = bool(params.get("show_progress", True))
    timing_log_path = _as_opt_path(params.get("timing_log_path"))
    stress_channel_values = _as_channel_voltage_map(params.get("stress_channel_values"))
    stress_voltage_list = params.get("stress_voltage_list")
    notes = str(params.get("notes", "") or "").strip()
    perform_initial_sweep = bool(params.get("perform_initial_sweep", True))

    initial_sweep_config_yaml_path = _as_opt_path(params.get("initial_sweep_config_yaml_path"))
    pre_stress_delay_raw = params.get("pre_stress_delay_s", params.get("initial_recovery_wait_s", 0.0))
    pre_stress_delay_s = max(float(pre_stress_delay_raw or 0.0), 0.0)
    initial_sweep_var = _as_opt_int(params.get("initial_sweep_var"))
    if initial_sweep_var is None:
        initial_sweep_var = 1
    initial_vstart = _as_opt_float(params.get("initial_vstart"))
    initial_vstop = _as_opt_float(params.get("initial_vstop"))
    initial_vstep = _as_opt_float(params.get("initial_vstep"))
    initial_sweep_mode_raw = params.get("initial_sweep_mode")
    initial_sweep_mode = (
        None if initial_sweep_mode_raw is None or not str(initial_sweep_mode_raw).strip()
        else str(initial_sweep_mode_raw).strip()
    )

    oscope_enabled = bool(params.get("oscope_enabled", True))
    oscope_resource = str(params.get("oscope_resource", "Oscope") or "Oscope").strip()
    oscope_timeout_ms = int(float(params.get("oscope_timeout_ms", 5000) or 5000))
    oscope_debug = bool(params.get("oscope_debug", False))
    oscope_config_yaml_path = _as_opt_path(params.get("oscope_config_yaml_path"))
    oscope_mode = str(params.get("oscope_mode", "single") or "single").strip()
    oscope_channels_raw = params.get("oscope_channels", [1])
    if not isinstance(oscope_channels_raw, list):
        raise ValueError("oscope_channels must be a list of channel numbers.")
    oscope_channels = [int(float(ch)) for ch in oscope_channels_raw]
    oscope_points = int(float(params.get("oscope_points", 2000) or 2000))
    oscope_wait_timeout_s = float(params.get("oscope_wait_timeout_s", 8.0) or 8.0)
    oscope_arm_settle_s = max(float(params.get("oscope_arm_settle_s", 0.1) or 0.0), 0.0)
    oscope_output_subdir = str(params.get("oscope_output_subdir", "oscope_waveforms") or "oscope_waveforms").strip()

    pulsegen_resource = str(params.get("pulsegen_resource", "PulseGen") or "PulseGen").strip()
    pulsegen_timeout_ms = int(float(params.get("pulsegen_timeout_ms", 5000) or 5000))
    pulsegen_debug = bool(params.get("pulsegen_debug", False))
    pulse_sequence = _normalize_pulse_sequence(params.get("pulse_sequence"))

    switch_clear_previous = bool(params.get("switch_clear_previous", True))
    switch_force_reapply = bool(params.get("switch_force_reapply", False))
    switch_settle_s = max(float(params.get("switch_settle_s", 0.0) or 0.0), 0.0)
    switch_analyzer_route_key = str(params.get("switch_analyzer_route_key", "") or "").strip()
    switch_pulsegen_route_key = str(params.get("switch_pulsegen_route_key", "") or "").strip()

    exp_log_file = _as_opt_path(params.get("exp_log_file"))
    if exp_log_file is None:
        exp_log_file = run_dir / "experiment_log.csv"

    save_path = _as_opt_path(params.get("save_path"))
    if save_path is None:
        save_path = run_dir / "artifacts"
    save_path.mkdir(parents=True, exist_ok=True)

    header_list = params.get("header_list")
    if header_list is not None and not isinstance(header_list, list):
        raise ValueError("header_list must be a list or null")
    if isinstance(header_list, list) and len(header_list) == 0:
        header_list = None

    if not oscope_enabled:
        raise ValueError("bti_otf_test requires oscilloscope capture to be enabled.")
    if not oscope_channels:
        raise ValueError("oscope_channels must be a non-empty list.")

    pre_sweep = set(save_path.glob("sweep_*.csv"))
    artifacts: list[Path] = []
    warnings: list[str] = []
    switch_macro = None
    switch_info: dict[str, Any] = {}
    analyzer_connected_by_runner = False

    main_cfg = _resolve_main_config_path(analyzer_macro, config_yaml_path)
    main_cfg = _load_macro_yaml_metadata(analyzer_macro, main_cfg)
    _sync_macro_stress_overrides(analyzer_macro, stress_channel_values)

    initial_cfg = _build_initial_sweep_config(
        main_config_yaml_path=main_cfg,
        initial_config_yaml_path=initial_sweep_config_yaml_path,
        initial_sweep_var=initial_sweep_var,
        initial_vstart=initial_vstart,
        initial_vstop=initial_vstop,
        initial_vstep=initial_vstep,
        initial_sweep_mode=initial_sweep_mode,
        save_path=save_path,
    )
    if initial_cfg is None:
        initial_cfg = main_cfg

    stress_conditions, stress_condition_labels = _resolve_stress_condition_sequence(analyzer_macro, stress_voltage_list)
    stress_times = [float(v) for v in list(getattr(analyzer_macro, "stress_time_list", []) or [])]
    if not stress_times:
        raise ValueError("OTF pulse test requires a non-empty stress_time_list in the configured analyzer YAML.")
    checkpoint_targets = list(stress_times)
    if checkpoint_targets[0] > 1.0e-12:
        checkpoint_targets.insert(0, 0.0)

    initial_df = pd.DataFrame()
    initial_sweep_artifacts: list[Path] = []
    waveform_paths: list[Path] = []
    pulse_rows: list[dict[str, Any]] = []
    checkpoint_rows: list[dict[str, Any]] = []

    scope_queue: Queue[Any] = Queue()
    pulse_queue: Queue[Any] = Queue()

    try:
        switch_macro, switch_info = _configure_otf_switch(params)
        if switch_macro is not None:
            if perform_initial_sweep and not switch_analyzer_route_key:
                raise ValueError(
                    "OTF switch support requires switch_analyzer_route_key when perform_initial_sweep is true."
                )
            if not switch_pulsegen_route_key:
                raise ValueError("OTF switch support requires switch_pulsegen_route_key.")

        if perform_initial_sweep:
            _apply_otf_switch_route(
                switch_macro,
                route_key=switch_analyzer_route_key,
                clear_previous=switch_clear_previous,
                force_reapply=switch_force_reapply,
                settle_s=switch_settle_s,
            )
            if getattr(analyzer_macro, "analyzer", None) is None and hasattr(analyzer_macro, "connect"):
                analyzer_macro.connect()
                analyzer_connected_by_runner = True
            _configure_macro(
                analyzer_macro,
                str(initial_cfg),
                fast_sweep=fast_sweep,
                setup_mode="sweep",
            )
            pre_initial_sweeps = set(save_path.glob("sweep_*.csv"))
            initial_df = _coerce_dataframe(
                analyzer_macro.run_sweep(
                    exp_log_file=str(exp_log_file),
                    header_list=header_list,
                    save_path=str(save_path),
                )
            )
            initial_sweep_artifacts = sorted(set(save_path.glob("sweep_*.csv")) - pre_initial_sweeps)
        else:
            ignored_initial_params = any(
                value is not None
                for value in (
                    initial_sweep_config_yaml_path,
                    initial_vstart,
                    initial_vstop,
                    initial_vstep,
                    initial_sweep_mode,
                )
            )
            if ignored_initial_params:
                warnings.append(
                    "perform_initial_sweep is false; initial-sweep-specific OTF overrides were ignored."
                )

        _append_experiment_log(analyzer_macro, exp_log_file)

        _apply_otf_switch_route(
            switch_macro,
            route_key=switch_pulsegen_route_key,
            clear_previous=switch_clear_previous,
            force_reapply=switch_force_reapply,
            settle_s=switch_settle_s,
        )

        if analyzer_connected_by_runner and hasattr(analyzer_macro, "disconnect"):
            analyzer_macro.disconnect()
            analyzer_connected_by_runner = False

        if pre_stress_delay_s > 0.0:
            time.sleep(pre_stress_delay_s)

        if getattr(oscope_macro, "resource_name", None) != oscope_resource:
            try:
                oscope_macro.disconnect()
            except Exception:
                pass
            oscope_macro.resource_name = oscope_resource
        if getattr(pulsegen_macro, "resource_name", None) != pulsegen_resource:
            try:
                pulsegen_macro.disconnect()
            except Exception:
                pass
            pulsegen_macro.resource_name = pulsegen_resource

        oscope_macro.connect(timeout_ms=oscope_timeout_ms, debug_mode=oscope_debug)
        oscope_config: dict[str, Any] = {}
        if oscope_config_yaml_path is not None:
            loaded_config = oscope_macro.load_config(oscope_config_yaml_path)
            if isinstance(loaded_config, dict):
                oscope_config = dict(loaded_config)
        oscope_config = _build_otf_oscope_config(params, base_config=oscope_config)
        if oscope_config:
            oscope_macro.config = oscope_config
            oscope_macro.apply_configuration()
        pulsegen_macro.connect(timeout_ms=pulsegen_timeout_ms, debug_mode=pulsegen_debug)

        waveform_dir = save_path / oscope_output_subdir
        waveform_dir.mkdir(parents=True, exist_ok=True)

        def _scope_worker() -> None:
            while True:
                job = scope_queue.get()
                if job is None:
                    return
                result_queue = job["result_queue"]
                armed_evt = job["armed_evt"]
                try:
                    scope_obj = oscope_macro.scope if oscope_macro is not None else None
                    if scope_obj is None:
                        raise RuntimeError("Oscilloscope is not connected.")
                    arm_t0 = time.perf_counter()
                    scope_obj.ready_for_capture(
                        mode=oscope_mode,
                        channels=oscope_channels,
                        wait=False,
                    )
                    if oscope_arm_settle_s > 0.0:
                        time.sleep(oscope_arm_settle_s)
                    arm_t1 = time.perf_counter()
                    armed_evt.set()
                    capture_wait_t0 = time.perf_counter()
                    scope_obj.wait_for_capture_complete(
                        mode=oscope_mode,
                        timeout_s=oscope_wait_timeout_s,
                    )
                    capture_wait_t1 = time.perf_counter()
                    waveforms = scope_obj.retrieve_channels(
                        oscope_channels,
                        points=oscope_points,
                        fmt="BYTE",
                        points_mode="RAW",
                    )
                    retrieve_t1 = time.perf_counter()
                    out_path = waveform_dir / (
                        f"checkpoint_{job['checkpoint_global_index']:03d}_"
                        f"pulse_{job['pulse_index']:02d}_{job['pulse_label']}.csv"
                    )
                    saved = oscope_macro.save_capture_csv(
                        waveforms,
                        filepath=out_path,
                        experiment_mark=(
                            f"otf_cp_{job['checkpoint_global_index']:03d}_"
                            f"pulse_{job['pulse_index']:02d}_{job['pulse_label']}"
                        ),
                    )
                    result_queue.put((
                        "scope",
                        {
                            "arm_elapsed_s": arm_t1 - arm_t0,
                            "capture_wait_elapsed_s": capture_wait_t1 - capture_wait_t0,
                            "retrieve_elapsed_s": retrieve_t1 - capture_wait_t1,
                            "waveform_path": str(saved),
                        },
                    ))
                except Exception as exc:
                    armed_evt.set()
                    result_queue.put(("error", exc))

        def _pulse_worker() -> None:
            while True:
                job = pulse_queue.get()
                if job is None:
                    return
                result_queue = job["result_queue"]
                armed_evt = job["armed_evt"]
                try:
                    if not armed_evt.wait(timeout=max(oscope_wait_timeout_s, 1.0)):
                        raise TimeoutError("Timed out waiting for oscilloscope arm before pulse trigger.")
                    stress_start_perf = job.get("stress_start_perf")
                    before_trigger = job.get("before_trigger")
                    if callable(before_trigger):
                        stress_start_perf = float(before_trigger())
                    trigger_t0 = time.perf_counter()
                    pulsegen_macro.trigger_pulse()
                    trigger_t1 = time.perf_counter()
                    actual_stress_s = float("nan")
                    if stress_start_perf is not None:
                        actual_stress_s = trigger_t0 - float(stress_start_perf)
                    result_queue.put((
                        "pulse",
                        {
                            "trigger_elapsed_s": trigger_t1 - trigger_t0,
                            "trigger_sent_at_s": trigger_t0,
                            "actual_stress_time_s": actual_stress_s,
                            "stress_start_perf": stress_start_perf,
                        },
                    ))
                except Exception as exc:
                    result_queue.put(("error", exc))

        scope_thread = threading.Thread(target=_scope_worker, name="igzo_otf_scope_worker", daemon=True)
        pulse_thread = threading.Thread(target=_pulse_worker, name="igzo_otf_pulse_worker", daemon=True)
        scope_thread.start()
        pulse_thread.start()

        elapsed_offset_s = 0.0
        checkpoint_global_index = 0
        for condition_index, condition_map in enumerate(stress_conditions):
            named_bias_map = _resolve_named_bias_map(analyzer_macro, condition_map)
            gate_stress_v = float(named_bias_map.get("VG", 0.0))
            drain_stress_v = float(named_bias_map.get("VD", 0.0))
            condition_label = stress_condition_labels[condition_index]

            prepared_sequence: list[dict[str, Any]] = []
            prepared_signatures: list[str] = []
            for pulse_entry in pulse_sequence:
                prepared_recipe, prepared_signature = _prepare_otf_recipe(
                    pulse_entry,
                    gate_stress_v=gate_stress_v,
                    drain_stress_v=drain_stress_v,
                )
                prepared_sequence.append(prepared_recipe)
                prepared_signatures.append(prepared_signature)

            unique_recipe_count = len(set(prepared_signatures))
            if unique_recipe_count > 1:
                warnings.append(
                    "pulse_sequence contains multiple distinct pulse templates. "
                    "The pulse generator is reprogrammed between those pulses, which may briefly perturb idle stress "
                    f"during the measurement phase for condition {condition_index}."
                )

            stress_start_t0 = time.perf_counter()
            _program_otf_recipe(
                pulsegen_macro,
                prepared_sequence[0],
                enable_outputs=False,
            )
            stress_start_t1 = time.perf_counter()
            stress_start_perf: float | None = None
            current_signature = prepared_signatures[0]

            for checkpoint_index, target_t in enumerate(checkpoint_targets):
                if stress_start_perf is not None:
                    due = stress_start_perf + float(target_t)
                    _wait_for_otf_checkpoint(
                        due_perf=due,
                        show_progress=show_progress,
                    )
                checkpoint_measure_t0 = time.perf_counter()
                total_target_t = elapsed_offset_s + target_t
                checkpoint_pulse_count = 0
                first_actual_stress_s: float | None = None
                last_actual_stress_s: float | None = None

                for pulse_index, (recipe, recipe_signature, pulse_entry) in enumerate(
                    zip(prepared_sequence, prepared_signatures, pulse_sequence)
                ):
                    pulse_label = str(pulse_entry.get("label", f"pulse_{pulse_index + 1}") or f"pulse_{pulse_index + 1}")
                    reconfig_elapsed_s = 0.0
                    if recipe_signature != current_signature:
                        reconfig_t0 = time.perf_counter()
                        _program_otf_recipe(
                            pulsegen_macro,
                            recipe,
                            enable_outputs=(stress_start_perf is not None),
                        )
                        reconfig_t1 = time.perf_counter()
                        reconfig_elapsed_s = reconfig_t1 - reconfig_t0
                        current_signature = recipe_signature

                    start_stress_before_trigger = stress_start_perf is None and pulse_index == 0

                    result_queue: Queue[Any] = Queue()
                    armed_evt = threading.Event()
                    scope_queue.put(
                        {
                            "checkpoint_global_index": checkpoint_global_index,
                            "pulse_index": pulse_index,
                            "pulse_label": pulse_label,
                            "armed_evt": armed_evt,
                            "result_queue": result_queue,
                        }
                    )
                    pulse_queue.put(
                        {
                            "checkpoint_global_index": checkpoint_global_index,
                            "pulse_index": pulse_index,
                            "pulse_label": pulse_label,
                            "armed_evt": armed_evt,
                            "result_queue": result_queue,
                            "stress_start_perf": stress_start_perf,
                            "before_trigger": (
                                (lambda recipe=recipe: _enable_otf_stress_outputs(pulsegen_macro, recipe))
                                if start_stress_before_trigger else None
                            ),
                        }
                    )

                    pulse_info: dict[str, Any] = {}
                    scope_info: dict[str, Any] = {}
                    for _ in range(2):
                        role, payload = result_queue.get()
                        if role == "error":
                            raise payload
                        if role == "pulse":
                            pulse_info = dict(payload)
                        elif role == "scope":
                            scope_info = dict(payload)

                    pulse_stress_start_perf = pulse_info.get("stress_start_perf")
                    if (
                        stress_start_perf is None
                        and pulse_stress_start_perf is not None
                    ):
                        stress_start_perf = float(pulse_stress_start_perf)

                    actual_stress_s = float(pulse_info.get("actual_stress_time_s", float("nan")))
                    if first_actual_stress_s is None:
                        first_actual_stress_s = actual_stress_s
                    last_actual_stress_s = actual_stress_s

                    waveform_path = Path(str(scope_info["waveform_path"]))
                    waveform_paths.append(waveform_path)
                    checkpoint_pulse_count += 1
                    pulse_rows.append(
                        {
                            "stress_condition_index": condition_index,
                            "stress_condition_label": condition_label,
                            "checkpoint_global_index": checkpoint_global_index,
                            "checkpoint_index": checkpoint_index,
                            "target_stress_s": target_t,
                            "target_total_stress_s": total_target_t,
                            "actual_stress_s": actual_stress_s,
                            "actual_total_stress_s": elapsed_offset_s + actual_stress_s,
                            "lateness_s": actual_stress_s - target_t,
                            "pulse_index": pulse_index,
                            "pulse_label": pulse_label,
                            "gate_stress_v": gate_stress_v,
                            "drain_stress_v": drain_stress_v,
                            "trigger_sent_at_s": float(pulse_info.get("trigger_sent_at_s", float("nan"))),
                            "pulse_trigger_elapsed_s": float(pulse_info.get("trigger_elapsed_s", float("nan"))),
                            "pulse_reconfigure_elapsed_s": reconfig_elapsed_s,
                            "scope_arm_elapsed_s": float(scope_info.get("arm_elapsed_s", float("nan"))),
                            "scope_capture_wait_elapsed_s": float(scope_info.get("capture_wait_elapsed_s", float("nan"))),
                            "scope_retrieve_elapsed_s": float(scope_info.get("retrieve_elapsed_s", float("nan"))),
                            "waveform_csv": str(waveform_path),
                        }
                    )

                checkpoint_measure_t1 = time.perf_counter()
                checkpoint_rows.append(
                    {
                        "stress_condition_index": condition_index,
                        "stress_condition_label": condition_label,
                        "checkpoint_global_index": checkpoint_global_index,
                        "checkpoint_index": checkpoint_index,
                        "target_stress_s": target_t,
                        "target_total_stress_s": total_target_t,
                        "condition_start_overhead_s": stress_start_t1 - stress_start_t0 if checkpoint_index == 0 else 0.0,
                        "measure_phase_elapsed_s": checkpoint_measure_t1 - checkpoint_measure_t0,
                        "pulse_count": checkpoint_pulse_count,
                        "first_actual_stress_s": first_actual_stress_s,
                        "last_actual_stress_s": last_actual_stress_s,
                    }
                )
                checkpoint_global_index += 1
            elapsed_offset_s += float(checkpoint_targets[-1])

        pulse_df = pd.DataFrame(pulse_rows)
        checkpoint_df = pd.DataFrame(checkpoint_rows)
        ts = time.strftime("%y%m%d_%H%M%S")
        pulse_log_path = save_path / f"otf_pulse_log_{ts}.csv"
        checkpoint_log_path = Path(timing_log_path) if timing_log_path is not None else (save_path / f"otf_checkpoint_timing_{ts}.csv")
        pulse_df.to_csv(pulse_log_path, index=False)
        checkpoint_df.to_csv(checkpoint_log_path, index=False)

        artifacts.extend(initial_sweep_artifacts)
        artifacts.extend(sorted(set(save_path.glob("sweep_*.csv")) - pre_sweep))
        artifacts.extend([pulse_log_path, checkpoint_log_path, *waveform_paths])
        if exp_log_file.exists():
            artifacts.append(exp_log_file)
        artifacts = _dedup_paths(artifacts)

        macro_info = {
            "mode": "OTF",
            "initial_sweep_rows": int(len(initial_df)),
            "pulse_rows": int(len(pulse_df)),
            "checkpoint_rows": int(len(checkpoint_df)),
            "stress_time_list": stress_times,
            "checkpoint_target_list": checkpoint_targets,
            "stress_condition_count": int(len(stress_conditions)),
            "stress_condition_labels": stress_condition_labels,
            "initial_sweep_config_yaml_path": str(initial_cfg),
            "main_config_yaml_path": str(main_cfg),
            "perform_initial_sweep": bool(perform_initial_sweep),
            "pre_stress_delay_s": float(pre_stress_delay_s),
            "initial_sweep_artifacts": [str(path) for path in initial_sweep_artifacts],
            "pulse_sequence_len": int(len(pulse_sequence)),
            "oscope_config_yaml_path": str(oscope_config_yaml_path) if oscope_config_yaml_path is not None else None,
            "oscope_applied_config": dict(oscope_config),
            "pulse_log_path": str(pulse_log_path),
            "checkpoint_log_path": str(checkpoint_log_path),
            "debug_timing": bool(debug_timing),
            "show_progress": bool(show_progress),
            "switch_info": dict(switch_info),
        }
        if hasattr(analyzer_macro, "last_stress_test_info"):
            analyzer_macro.last_stress_test_info = dict(macro_info)

        return RunResult(
            data=pulse_df,
            summary={
                "kind": "otf_pulse_capture",
                "mode": "OTF",
                "num_rows": int(len(pulse_df)),
                "num_cols": int(len(pulse_df.columns)),
                "initial_sweep_rows": int(len(initial_df)),
                "checkpoint_rows": int(len(checkpoint_df)),
            },
            artifacts=artifacts,
            metadata={
                "runner_version": __version__,
                "config_yaml_path": str(config_yaml_path) if config_yaml_path is not None else None,
                "fast_sweep": fast_sweep,
                "save_path": str(save_path),
                "debug_timing": debug_timing,
                "show_progress": bool(show_progress),
                "timing_log_path": str(timing_log_path) if timing_log_path is not None else None,
                "stress_channel_values": stress_channel_values,
                "stress_time_list": stress_times,
                "checkpoint_target_list": checkpoint_targets,
                "stress_voltage_list": stress_voltage_list,
                "perform_initial_sweep": bool(perform_initial_sweep),
                "initial_sweep_config_yaml_path": str(initial_sweep_config_yaml_path) if initial_sweep_config_yaml_path is not None else None,
                "pre_stress_delay_s": pre_stress_delay_s,
                "initial_sweep_var": int(initial_sweep_var),
                "initial_sweep_overrides": {
                    "vstart": initial_vstart,
                    "vstop": initial_vstop,
                    "vstep": initial_vstep,
                    "sweep_mode": initial_sweep_mode,
                },
                "oscope_enabled": oscope_enabled,
                "oscope_resource": oscope_resource,
                "oscope_config_yaml_path": str(oscope_config_yaml_path) if oscope_config_yaml_path is not None else None,
                "oscope_applied_config": dict(oscope_config),
                "oscope_channels": oscope_channels,
                "oscope_points": oscope_points,
                "oscope_mode": oscope_mode,
                "oscope_wait_timeout_s": oscope_wait_timeout_s,
                "oscope_arm_settle_s": oscope_arm_settle_s,
                "oscope_output_subdir": oscope_output_subdir,
                "pulsegen_resource": pulsegen_resource,
                "pulse_sequence_len": len(pulse_sequence),
                "switch_info": dict(switch_info),
                "switch_analyzer_route_key": switch_analyzer_route_key,
                "switch_pulsegen_route_key": switch_pulsegen_route_key,
                "macro_info": macro_info,
                "notes": notes,
            },
            warnings=warnings,
        )
    finally:
        try:
            scope_queue.put(None)
        except Exception:
            pass
        try:
            pulse_queue.put(None)
        except Exception:
            pass
        try:
            if pulsegen_macro is not None:
                pulsegen_macro.disconnect()
        except Exception:
            pass
        try:
            if oscope_macro is not None:
                oscope_macro.disconnect()
        except Exception:
            pass
        try:
            if switch_macro is not None:
                switch_macro.disconnect()
        except Exception:
            pass
        try:
            if analyzer_connected_by_runner and analyzer_macro is not None and hasattr(analyzer_macro, "disconnect"):
                analyzer_macro.disconnect()
        except Exception:
            pass
