from __future__ import annotations

__version__ = "0.7.0"

import argparse
from copy import deepcopy
from pathlib import Path
from typing import Any

import pyvisa
import tkinter as tk
from tkinter import ttk

from src.configs.orchestration.local.parameter_analyzer_4155.experiment_orchestrator import (
    ExperimentOrchestrator,
    OrchestratorConfig,
)
from src.configs.orchestration.local.parameter_analyzer_4155.run_4155_gui import (
    ParameterAnalyzer4155OrchestratorGUI,
)
from src.macros.parameter_analyzer.igzo_stability.instrument_bundle import (
    IGZOStabilityInstrumentBundle,
    build_igzo_stability_bundle,
)
from src.macros.parameter_analyzer.igzo_stability.registry import get_registry


PRESET_DIR = Path("scenarios/IGZO_stability/frequently_used")
RESULTS_DIR = Path("scenarios/IGZO_stability/results")


class IGZOStabilityOrchestratorGUI(ParameterAnalyzer4155OrchestratorGUI):
    """
    IGZO BTI / stability launcher using the shared 4155 advanced GUI flow,
    backed by BTI-specific experiments (MSM, OTF pulse/scope) and
    scenario-local preset/result locations.
    """

    HIDDEN_GUI_PARAM_KEYS = {"config_yaml_path", "stress_channel_values", "stress_voltage_list"}
    OTF_CHANNEL_SETTING_FIELDS = (
        ("read_v", "Read V"),
        ("width", "Width (s)"),
        ("period", "Period (s)"),
        ("delay", "Delay (s)"),
        ("rise", "Rise (s)"),
        ("fall", "Fall (s)"),
        ("zsource", "Zsource (Ohm)"),
        ("zload", "Zload (Ohm)"),
    )

    def __init__(self) -> None:
        super().__init__(
            registry=get_registry(),
            window_title="IGZO Stability (BTI) Orchestrator Runner",
            config_preset_dir=PRESET_DIR,
            out_root_default=RESULTS_DIR,
            status_text="Ready (IGZO stability / BTI)",
            project_name="igzo_stability_4155",
        )
        self.runner_use_generated_cfg.set(True)
        self._retitle_otf_tab()
        self._load_or_init_experiment_state(self._selected_key())
        self._show_page(0)
        self._place_top_right()

    @staticmethod
    def _is_igzo_bti_experiment_key(exp_key: str) -> bool:
        return exp_key in {"bti_transfer_curve", "bti_msm_test", "bti_otf_test"}

    def _load_spec_param_defaults(self, spec: Any) -> dict[str, Any]:
        defaults = super()._load_spec_param_defaults(spec)
        if self._is_igzo_bti_experiment_key(str(getattr(spec, "key", ""))):
            defaults = dict(defaults)
            for key in self.HIDDEN_GUI_PARAM_KEYS:
                defaults.pop(key, None)
        return defaults

    def _force_generated_config_mode(self) -> None:
        if hasattr(self, "runner_use_generated_cfg") and self.runner_use_generated_cfg is not None:
            self.runner_use_generated_cfg.set(True)

    def _collect_params(self, exp_key: str) -> dict[str, Any]:
        params = super()._collect_params(exp_key)
        if exp_key in {"bti_msm_test", "bti_otf_test"}:
            base_stress_map, stress_conditions = self._collect_measurement_stress_profiles(
                allow_sequences=True,
            )
            params["stress_channel_values"] = dict(base_stress_map)
            params["stress_voltage_list"] = list(stress_conditions) if len(stress_conditions) > 1 else []
        params["config_yaml_path"] = str(self._generate_macro_config_yaml(exp_key))
        return params

    def _capture_experiment_state(self) -> dict[str, Any]:
        state = super()._capture_experiment_state()
        state["runner_use_generated_cfg"] = True
        runner_params = state.get("runner_params")
        if isinstance(runner_params, dict):
            runner_params = dict(runner_params)
            for key in self.HIDDEN_GUI_PARAM_KEYS:
                runner_params.pop(key, None)
            state["runner_params"] = runner_params
        advanced_params = state.get("advanced_params")
        if isinstance(advanced_params, dict):
            advanced_params = dict(advanced_params)
            for key in self.HIDDEN_GUI_PARAM_KEYS:
                advanced_params.pop(key, None)
            state["advanced_params"] = advanced_params
        return state

    def _apply_experiment_state(self, state: dict[str, Any]) -> None:
        patched_state = dict(state or {})
        patched_state["runner_use_generated_cfg"] = True

        runner_params = patched_state.get("runner_params")
        if isinstance(runner_params, dict):
            runner_params = dict(runner_params)
            for key in self.HIDDEN_GUI_PARAM_KEYS:
                runner_params.pop(key, None)
            patched_state["runner_params"] = runner_params

        advanced_params = patched_state.get("advanced_params")
        if isinstance(advanced_params, dict):
            advanced_params = dict(advanced_params)
            for key in self.HIDDEN_GUI_PARAM_KEYS:
                advanced_params.pop(key, None)
            patched_state["advanced_params"] = advanced_params

        super()._apply_experiment_state(patched_state)
        self._force_generated_config_mode()

    @staticmethod
    def _is_oscope_param_key(key: str) -> bool:
        text = str(key).strip()
        if not text:
            return False
        return text.startswith("oscope_")

    def _retitle_otf_tab(self) -> None:
        if self.settings_notebook is None or self.pulsegen_params_tab is None:
            return
        try:
            self.settings_notebook.tab(self.pulsegen_params_tab, text="OTF")
        except Exception:
            pass

    @staticmethod
    def _default_otf_pulse_entry() -> dict[str, Any]:
        return {
            "label": "read_pulse_1",
            "mode": "trigger",
            "enable_channel_1": True,
            "enable_channel_2": True,
            "channel_1_settings": {
                "read_v": 0.5,
                "width": 1.0e-3,
                "period": 5.0e-3,
                "rise": 1.0e-6,
                "fall": 1.0e-6,
                "zsource": 50.0,
                "zload": 1.0e6,
            },
            "channel_2_settings": {
                "read_v": 0.1,
                "width": 1.0e-3,
                "period": 5.0e-3,
                "rise": 1.0e-6,
                "fall": 1.0e-6,
                "zsource": 50.0,
                "zload": 1.0e6,
            },
        }

    @classmethod
    def _seed_otf_pulse_sequence(cls, value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            seeded = [deepcopy(item) for item in value if isinstance(item, dict)]
            if seeded:
                return seeded
        return [cls._default_otf_pulse_entry()]

    def _normalize_otf_pulse_sequence_value(self, value: Any, *, schema_default: Any) -> list[dict[str, Any]]:
        parsed = value
        if isinstance(value, str):
            text = value.strip()
            parsed = self._parse_structured_text(text) if text else schema_default
        if parsed in (None, ""):
            parsed = schema_default

        template_sequence = self._seed_otf_pulse_sequence(schema_default)
        template = deepcopy(template_sequence[0])
        raw_sequence = parsed if isinstance(parsed, list) else template_sequence

        known_top_level = {
            "label",
            "mode",
            "enable_channel_1",
            "enable_channel_2",
            "settle_s",
            "channel_1_settings",
            "channel_2_settings",
        }
        known_channel_fields = {key for key, _ in self.OTF_CHANNEL_SETTING_FIELDS}

        normalized: list[dict[str, Any]] = []
        for idx, raw in enumerate(raw_sequence):
            base = deepcopy(template)
            if isinstance(raw, dict):
                for key, value in raw.items():
                    if key in {"channel_1_settings", "channel_2_settings"}:
                        continue
                    base[key] = deepcopy(value)

                for channel_key in ("channel_1_settings", "channel_2_settings"):
                    merged = deepcopy(template.get(channel_key, {}) or {})
                    raw_settings = raw.get(channel_key)
                    if isinstance(raw_settings, dict):
                        merged.update(deepcopy(raw_settings))
                    base[channel_key] = merged

            base["label"] = str(base.get("label") or f"read_pulse_{idx + 1}")
            base["mode"] = str(base.get("mode") or "trigger")
            base["enable_channel_1"] = bool(base.get("enable_channel_1", True))
            base["enable_channel_2"] = bool(base.get("enable_channel_2", True))
            for channel_key in ("channel_1_settings", "channel_2_settings"):
                settings = base.get(channel_key)
                if not isinstance(settings, dict):
                    settings = {}
                base[channel_key] = settings
                extra_key = f"{channel_key}__extra"
                base[extra_key] = {k: deepcopy(v) for k, v in settings.items() if k not in known_channel_fields}
            base["_extra_top_level"] = {
                k: deepcopy(v)
                for k, v in base.items()
                if k not in known_top_level and not str(k).endswith("__extra")
            }
            normalized.append(base)

        if not normalized:
            normalized = [deepcopy(template)]
        return normalized

    def _coerce_otf_channel_selection(self, value: Any) -> list[int]:
        parsed = value
        if isinstance(value, str):
            text = value.strip()
            parsed = self._parse_structured_text(text) if text else []
        if parsed in (None, ""):
            parsed = []
        if isinstance(parsed, (int, float)) and not isinstance(parsed, bool):
            parsed = [int(float(parsed))]
        if not isinstance(parsed, list):
            return [1]

        out: list[int] = []
        for item in parsed:
            try:
                channel = int(float(item))
            except Exception:
                continue
            if channel in {1, 2} and channel not in out:
                out.append(channel)
        return out or [1]

    @staticmethod
    def _add_otf_entry_row(
        parent: ttk.Frame,
        *,
        label: str,
        variable: tk.StringVar,
        width: int = 16,
    ) -> ttk.Entry:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text=label, width=16).pack(side="left")
        entry = ttk.Entry(row, textvariable=variable, width=width)
        entry.pack(side="left", fill="x", expand=True)
        return entry

    def _build_otf_oscope_channels_editor(
        self,
        parent: ttk.Frame,
        *,
        defaults: dict[str, Any],
        cached_adv: dict[str, Any],
    ) -> None:
        key = "oscope_channels"
        if key not in defaults:
            return

        schema_default = defaults[key]
        current_value = cached_adv.get(key, schema_default)

        group = ttk.LabelFrame(parent, text="Capture Channels", padding=8)
        group.pack(fill="x", pady=(8, 0))
        ttk.Label(
            group,
            text="Select which oscilloscope channels are retrieved and saved for each OTF capture.",
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))

        selections = {
            1: tk.BooleanVar(value=False),
            2: tk.BooleanVar(value=False),
        }

        def get_value() -> list[int]:
            return [channel for channel in (1, 2) if selections[channel].get()]

        def set_value(value: Any) -> None:
            channels = self._coerce_otf_channel_selection(value)
            for channel, var in selections.items():
                var.set(channel in channels)

        row = ttk.Frame(group)
        row.pack(fill="x")
        ttk.Checkbutton(row, text="Channel 1", variable=selections[1]).pack(side="left")
        ttk.Checkbutton(row, text="Channel 2", variable=selections[2]).pack(side="left", padx=(12, 0))

        set_value(current_value)
        self.param_widgets[key] = {
            "kind": type(schema_default).__name__,
            "default": schema_default,
            "get_value": get_value,
            "set_value": set_value,
        }

    def _build_otf_param_group(
        self,
        parent: ttk.Frame,
        *,
        title: str,
        keys: list[str],
        defaults: dict[str, Any],
        cached_adv: dict[str, Any],
        description: str | None = None,
    ) -> ttk.LabelFrame:
        group = ttk.LabelFrame(parent, text=title, padding=8)
        group.pack(fill="x", pady=(0, 8))
        if description:
            ttk.Label(
                group,
                text=description,
                wraplength=760,
                justify="left",
            ).pack(anchor="w", pady=(0, 6))
        self._build_param_rows(
            group,
            keys=keys,
            defaults=defaults,
            cached_adv=cached_adv,
        )
        return group

    def _build_otf_oscope_editor(
        self,
        parent: ttk.Frame,
        *,
        defaults: dict[str, Any],
        cached_adv: dict[str, Any],
    ) -> None:
        ttk.Label(
            parent,
            text=(
                "These oscilloscope settings are applied directly from the GUI on top of the base "
                "scope YAML, so time scale, voltage scale, delay, trigger source, and channel "
                "settings are all explicit here."
            ),
            wraplength=780,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))

        acquisition_group = self._build_otf_param_group(
            parent,
            title="Acquisition",
            keys=[
                "oscope_enabled",
                "oscope_resource",
                "oscope_timeout_ms",
                "oscope_debug",
                "oscope_config_yaml_path",
                "oscope_mode",
                "oscope_points",
                "oscope_wait_timeout_s",
                "oscope_arm_settle_s",
                "oscope_output_subdir",
            ],
            defaults=defaults,
            cached_adv=cached_adv,
            description="Capture/save controls and the optional base YAML used before GUI overrides are applied.",
        )
        self._build_otf_oscope_channels_editor(
            acquisition_group,
            defaults=defaults,
            cached_adv=cached_adv,
        )

        self._build_otf_param_group(
            parent,
            title="Window",
            keys=[
                "oscope_window_channel",
                "oscope_time_scale_s",
                "oscope_delay_time_s",
                "oscope_time_position_s",
                "oscope_window_voltage_scale_mv",
                "oscope_window_offset_voltage_mv",
            ],
            defaults=defaults,
            cached_adv=cached_adv,
            description="Overall timebase/window view used when configuring the scope.",
        )

        self._build_otf_param_group(
            parent,
            title="Trigger",
            keys=[
                "oscope_trigger_mode",
                "oscope_trigger_source",
                "oscope_trigger_slope",
                "oscope_trigger_level_mv",
                "oscope_trigger_coupling",
                "oscope_trigger_sweep",
            ],
            defaults=defaults,
            cached_adv=cached_adv,
            description="Default trigger source is external, but you can override it here for other scope trigger paths.",
        )

        channel_wrap = ttk.Frame(parent)
        channel_wrap.pack(fill="x")
        self._build_otf_param_group(
            channel_wrap,
            title="Channel 1",
            keys=[
                "oscope_chan1_display",
                "oscope_chan1_coupling",
                "oscope_chan1_impedance",
                "oscope_chan1_offset_mv",
                "oscope_chan1_scale_mv",
            ],
            defaults=defaults,
            cached_adv=cached_adv,
            description="Channel 1 display and scaling setup.",
        )
        self._build_otf_param_group(
            channel_wrap,
            title="Channel 2",
            keys=[
                "oscope_chan2_display",
                "oscope_chan2_coupling",
                "oscope_chan2_impedance",
                "oscope_chan2_offset_mv",
                "oscope_chan2_scale_mv",
            ],
            defaults=defaults,
            cached_adv=cached_adv,
            description="Channel 2 display and scaling setup.",
        )

    def _build_otf_pulse_sequence_editor(
        self,
        parent: ttk.Frame,
        *,
        defaults: dict[str, Any],
        cached_adv: dict[str, Any],
    ) -> None:
        key = "pulse_sequence"
        if key not in defaults:
            return

        schema_default = defaults[key]
        current_value = cached_adv.get(key, schema_default)
        template_sequence = self._seed_otf_pulse_sequence(schema_default)
        known_channel_fields = [field for field, _ in self.OTF_CHANNEL_SETTING_FIELDS]
        state: dict[str, Any] = {"items": []}

        group = ttk.LabelFrame(parent, text="Pulse Sequence", padding=8)
        group.pack(fill="x", pady=(8, 0))
        ttk.Label(
            group,
            text=(
                "Each sequence entry is fired at every OTF checkpoint. Multiple entries are already "
                "supported here for future expansion; today they still execute sequentially and save "
                "one waveform capture per pulse."
            ),
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))

        controls = ttk.Frame(group)
        controls.pack(fill="x", pady=(0, 6))
        cards = ttk.Frame(group)
        cards.pack(fill="x")

        def build_sequence_from_state() -> list[dict[str, Any]]:
            sequence: list[dict[str, Any]] = []
            for idx, item in enumerate(state["items"]):
                entry = deepcopy(item["extra_top_level"])
                entry["label"] = str(item["label"].get()).strip() or f"read_pulse_{idx + 1}"
                entry["mode"] = str(item["mode"].get()).strip() or "trigger"
                entry["enable_channel_1"] = bool(item["enable_channel_1"].get())
                entry["enable_channel_2"] = bool(item["enable_channel_2"].get())

                settle_text = str(item["settle_s"].get()).strip()
                if settle_text:
                    parsed_settle = self._parse_structured_text(settle_text)
                    entry["settle_s"] = parsed_settle if isinstance(parsed_settle, (int, float)) else settle_text
                else:
                    entry.pop("settle_s", None)

                for channel_key in ("channel_1_settings", "channel_2_settings"):
                    settings = deepcopy(item[f"{channel_key}__extra"])
                    for field in known_channel_fields:
                        field_text = str(item[channel_key][field].get()).strip()
                        if field_text:
                            parsed_field = self._parse_structured_text(field_text)
                            settings[field] = (
                                parsed_field if isinstance(parsed_field, (int, float)) else field_text
                            )
                        else:
                            settings.pop(field, None)
                    entry[channel_key] = settings
                sequence.append(entry)
            return sequence or self._seed_otf_pulse_sequence(schema_default)

        def render(sequence_value: Any) -> None:
            for child in cards.winfo_children():
                child.destroy()
            state["items"] = []

            sequence = self.normalize_otf_pulse_sequence_for_gui(sequence_value, schema_default=schema_default)
            for idx, pulse in enumerate(sequence):
                card = ttk.LabelFrame(cards, text=f"Pulse {idx + 1}", padding=8)
                card.pack(fill="x", pady=(0, 8))

                header = ttk.Frame(card)
                header.pack(fill="x", pady=(0, 6))
                label_var = tk.StringVar(value=str(pulse.get("label", f"read_pulse_{idx + 1}")))
                mode_var = tk.StringVar(value=str(pulse.get("mode", "trigger")))
                settle_var = tk.StringVar(
                    value="" if pulse.get("settle_s") in (None, "") else str(pulse.get("settle_s"))
                )
                self._add_otf_entry_row(header, label="Label", variable=label_var, width=18)
                self._add_otf_entry_row(header, label="Mode", variable=mode_var, width=12)
                self._add_otf_entry_row(header, label="Settle (s)", variable=settle_var, width=12)

                enable_row = ttk.Frame(card)
                enable_row.pack(fill="x", pady=(0, 6))
                enable_ch1 = tk.BooleanVar(value=bool(pulse.get("enable_channel_1", True)))
                enable_ch2 = tk.BooleanVar(value=bool(pulse.get("enable_channel_2", True)))
                ttk.Checkbutton(enable_row, text="Enable Channel 1", variable=enable_ch1).pack(side="left")
                ttk.Checkbutton(enable_row, text="Enable Channel 2", variable=enable_ch2).pack(side="left", padx=(12, 0))

                channel_wrap = ttk.Frame(card)
                channel_wrap.pack(fill="x")
                channel_items: dict[str, dict[str, tk.StringVar]] = {}
                channel_extras: dict[str, dict[str, Any]] = {}
                for channel_key, title in (
                    ("channel_1_settings", "Channel 1"),
                    ("channel_2_settings", "Channel 2"),
                ):
                    group_frame = ttk.LabelFrame(channel_wrap, text=title, padding=8)
                    group_frame.pack(side="left", fill="x", expand=True, padx=(0, 6) if channel_key == "channel_1_settings" else (0, 0))
                    settings = pulse.get(channel_key, {})
                    channel_items[channel_key] = {}
                    channel_extras[channel_key] = deepcopy(pulse.get(f"{channel_key}__extra", {}))
                    for field, label in self.OTF_CHANNEL_SETTING_FIELDS:
                        var = tk.StringVar(
                            value="" if settings.get(field) in (None, "") else str(settings.get(field))
                        )
                        self._add_otf_entry_row(group_frame, label=label, variable=var, width=14)
                        channel_items[channel_key][field] = var

                state["items"].append(
                    {
                        "label": label_var,
                        "mode": mode_var,
                        "settle_s": settle_var,
                        "enable_channel_1": enable_ch1,
                        "enable_channel_2": enable_ch2,
                        "channel_1_settings": channel_items["channel_1_settings"],
                        "channel_2_settings": channel_items["channel_2_settings"],
                        "channel_1_settings__extra": channel_extras["channel_1_settings"],
                        "channel_2_settings__extra": channel_extras["channel_2_settings"],
                        "extra_top_level": deepcopy(pulse.get("_extra_top_level", {})),
                    }
                )

        def set_value(value: Any) -> None:
            render(value)

        def get_value() -> list[dict[str, Any]]:
            return build_sequence_from_state()

        def add_pulse() -> None:
            current = build_sequence_from_state()
            new_pulse = deepcopy(current[-1] if current else template_sequence[0])
            new_pulse["label"] = f"read_pulse_{len(current) + 1}"
            render(current + [new_pulse])

        def duplicate_last_pulse() -> None:
            current = build_sequence_from_state()
            if not current:
                render(template_sequence)
                return
            new_pulse = deepcopy(current[-1])
            new_pulse["label"] = f"{str(new_pulse.get('label') or 'read_pulse')}_copy"
            render(current + [new_pulse])

        def remove_last_pulse() -> None:
            current = build_sequence_from_state()
            if len(current) <= 1:
                render(template_sequence[:1])
                return
            render(current[:-1])

        ttk.Button(controls, text="Add Pulse", command=add_pulse).pack(side="left")
        ttk.Button(controls, text="Duplicate Last", command=duplicate_last_pulse).pack(side="left", padx=(6, 0))
        ttk.Button(controls, text="Remove Last", command=remove_last_pulse).pack(side="left", padx=(6, 0))

        set_value(current_value)
        self.param_widgets[key] = {
            "kind": type(schema_default).__name__,
            "default": schema_default,
            "get_value": get_value,
            "set_value": set_value,
        }

    def normalize_otf_pulse_sequence_for_gui(self, value: Any, *, schema_default: Any) -> list[dict[str, Any]]:
        return self._normalize_otf_pulse_sequence_value(value, schema_default=schema_default)

    def _build_runner_params_tab(self) -> None:
        spec = self._selected_spec()
        if spec.key not in {"bti_transfer_curve", "bti_msm_test", "bti_otf_test"}:
            super()._build_runner_params_tab()
            return

        if self.runner_params_tab is None or self.pulsegen_params_tab is None:
            return
        self._retitle_otf_tab()
        for child in self.runner_params_tab.winfo_children():
            child.destroy()
        for child in self.pulsegen_params_tab.winfo_children():
            child.destroy()
        self.param_widgets.clear()

        defaults = self._load_spec_param_defaults(spec)
        cached_adv = self.advanced_params_by_experiment.get(self._selected_key(), {})

        ttk.Label(
            self.pulsegen_params_tab,
            text=f"Registry: {spec.key} | YAML: {spec.param_yaml_path}",
        ).pack(anchor="w", pady=(0, 6))
        ttk.Label(
            self.runner_params_tab,
            text=f"Registry: {spec.key} | YAML: {spec.param_yaml_path}",
        ).pack(anchor="w", pady=(0, 6))

        self.runner_use_generated_cfg = tk.BooleanVar(value=True)
        ttk.Label(
            self.runner_params_tab,
            text=(
                "This IGZO BTI GUI always generates the analyzer channel/stress YAML from the "
                "entries on the GUI tabs. External config_yaml_path and runner-level stress "
                "override fields are hidden here so the executed run always matches the current "
                "GUI settings. For multi-condition MSM/OTF stress, enter per-channel lists in "
                "Measurement Settings > Stress."
            ),
            wraplength=780,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))

        keys = [k for k in defaults.keys() if not str(k).startswith("__")]
        pulsegen_keys = [k for k in keys if self._is_pulsegen_param_key(k)]
        oscope_keys = [k for k in keys if self._is_oscope_param_key(k)]
        runner_keys = [k for k in keys if k not in pulsegen_keys and k not in oscope_keys]

        if spec.key == "bti_otf_test" and (pulsegen_keys or oscope_keys):
            ttk.Label(
                self.pulsegen_params_tab,
                text="OTF instrument settings for oscilloscope capture and pulse-generator read/stress recipes.",
            ).pack(anchor="w", pady=(0, 6))
            otf_notebook = ttk.Notebook(self.pulsegen_params_tab)
            otf_notebook.pack(fill="both", expand=True)

            oscope_tab = ttk.Frame(otf_notebook, padding=8)
            pulse_tab = ttk.Frame(otf_notebook, padding=8)
            otf_notebook.add(oscope_tab, text="Oscilloscope")
            otf_notebook.add(pulse_tab, text="Pulse Generator")

            if oscope_keys:
                self._build_otf_oscope_editor(
                    oscope_tab,
                    defaults=defaults,
                    cached_adv=cached_adv,
                )

            if pulsegen_keys:
                before_sequence = [key for key in pulsegen_keys if key != "pulse_sequence"]
                self._build_param_rows(
                    pulse_tab,
                    keys=before_sequence,
                    defaults=defaults,
                    cached_adv=cached_adv,
                )
                self._build_otf_pulse_sequence_editor(
                    pulse_tab,
                    defaults=defaults,
                    cached_adv=cached_adv,
                )
        elif pulsegen_keys or oscope_keys:
            ttk.Label(
                self.pulsegen_params_tab,
                text="This experiment does not expose OTF auxiliary instrument parameters.",
            ).pack(anchor="w", pady=(0, 6))
        else:
            ttk.Label(
                self.pulsegen_params_tab,
                text="This BTI experiment does not expose OTF auxiliary instrument parameters.",
            ).pack(anchor="w", pady=(0, 6))

        self._build_param_rows(
            self.runner_params_tab,
            keys=runner_keys,
            defaults=defaults,
            cached_adv=cached_adv,
        )

        self._force_generated_config_mode()
        self._refresh_measure_states()
        self._refresh_header_preview()
        self._refresh_header_plot_state()

    @staticmethod
    def _param_widget_text(
        param_widgets: dict[str, dict[str, Any]],
        key: str,
        default: str,
    ) -> str:
        meta = param_widgets.get(key)
        if not isinstance(meta, dict):
            return str(default)
        var = meta.get("var")
        if var is None or not hasattr(var, "get"):
            return str(default)
        text = str(var.get()).strip()
        return text or str(default)

    def _current_bundle_resource(self, key: str, default: str) -> str:
        return self._param_widget_text(self.param_widgets, key, default)

    def _orchestrator_sig(self) -> str:
        base_sig = super()._orchestrator_sig()
        pulsegen_resource = self._current_bundle_resource("pulsegen_resource", "PulseGen")
        oscope_resource = self._current_bundle_resource("oscope_resource", "Oscope")
        return f"{base_sig}::{pulsegen_resource}::{oscope_resource}"

    def _ensure_orchestrator(self) -> ExperimentOrchestrator:
        sig = self._orchestrator_sig()
        if self.orchestrator is not None and self._orchestrator_signature == sig:
            return self.orchestrator
        if self.orchestrator is not None:
            try:
                self.orchestrator.close()
            except Exception:
                pass

        config = OrchestratorConfig(
            out_root=Path(self.out_root_var.get().strip() or self.out_root_default),
            log_root=Path(self.log_root_var.get().strip() or str(self.project_root / "logs")),
            show_plots=bool(self.show_plots_var.get()),
            operator=self.operator_var.get().strip() or None,
            project=self.project_name,
        )

        analyzer_resource = self.resource_var.get().strip()
        pulsegen_resource = self._current_bundle_resource("pulsegen_resource", "PulseGen")
        oscope_resource = self._current_bundle_resource("oscope_resource", "Oscope")

        def analyzer_factory(_: OrchestratorConfig) -> pyvisa.ResourceManager:
            return pyvisa.ResourceManager()

        def macro_factory(rm: pyvisa.ResourceManager, _: OrchestratorConfig) -> IGZOStabilityInstrumentBundle:
            return build_igzo_stability_bundle(
                rm,
                analyzer_resource=analyzer_resource,
                pulsegen_resource=pulsegen_resource,
                oscope_resource=oscope_resource,
            )

        orch = ExperimentOrchestrator(
            config=config,
            analyzer_factory=analyzer_factory,
            macro_factory=macro_factory,
            gui=None,
            registry=self.registry,
        )
        orch.initialize()
        self.orchestrator = orch
        self._orchestrator_signature = sig
        return orch


def run_advanced_gui() -> None:
    app = IGZOStabilityOrchestratorGUI()
    app.run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["gui"],
        default="gui",
        help="Execution mode. GUI is the default and only mode.",
    )
    _ = parser.parse_args(argv)
    run_advanced_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
