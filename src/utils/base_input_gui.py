from __future__ import annotations
__version__ = "0.3.0"

import copy
import tkinter as tk
from tkinter import ttk
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, Callable, Sequence, Tuple


# =====================================================
# Base input gui based on yaml files
# =====================================================
class BaseInputGUI:
    """
    GUI class for user input with persistent YAML configuration.

    Supports YAML-level UI metadata:
        __groups__:
            Group Name:
                - field1
                - field2

        __tooltips__:
            field1: "Tooltip text"
            field2: "Another tooltip"

        __choices__:
            field1: [OPTION_A, OPTION_B]

        __scenario_tooltips__:
            extra1:
                OPTION_A: "What extra1 means while OPTION_A is selected."

    A field listed in ``__choices__`` renders as a read-only dropdown instead of
    a free-text entry, so only firmware-supported values can be submitted.

    ``__scenario_tooltips__`` makes a field's tooltip follow the selected
    ``scenario``: the entry for the current scenario is shown instead of one
    chunk documenting every scenario at once. A scenario left out of a field's
    map is reported as not used by that scenario.
    """

    GROUP_KEY = "__groups__"
    TOOLTIP_KEY = "__tooltips__"
    CHOICES_KEY = "__choices__"
    SCENARIO_TOOLTIP_KEY = "__scenario_tooltips__"
    SCENARIO_DEFAULT_KEY = "_default"
    PASSTHROUGH_METADATA_KEYS = {"inherit_from"}

    def __init__(
        self,
        config_path: str | Path,
        defaults: Optional[Dict[str, Any]] = None,
        section_key: Optional[str] = None,
        single_column_groups: Optional[Sequence[str]] = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.defaults = defaults
        self.section_key = section_key
        self.single_column_groups = {
            str(name).strip().lower()
            for name in (single_column_groups or ())
        }
        self._section_container: Optional[str] = None
        self._full_raw_data: Optional[Dict[str, Any]] = None
        self._passthrough_metadata: Dict[str, Any] = {}

        raw = self.load_inputs()

        # Extract UI metadata
        self.groups, raw = self._extract_groups(raw)
        self.tooltips, raw = self._extract_tooltips(raw)
        self.choices, raw = self._extract_choices(raw)
        self.scenario_tooltips, raw = self._extract_scenario_tooltips(raw)

        self.data = raw
        self.type_map: Dict[Tuple[str, ...], Callable[[str], Any]] = self._build_type_map(self.data)
        self.entries: Dict[Tuple[str, ...], ttk.Entry] = {}
        self._submitted = False
        # Field names an embedding host syncs across editors; used only to note
        # the fact in the tooltip so the behaviour is discoverable.
        self.shared_fields: set[str] = set()
        self._tooltips_by_path: Dict[Tuple[str, ...], "_Tooltip"] = {}

    # =====================================================
    # YAML Handling
    # =====================================================
    def load_inputs(self) -> Dict[str, Any]:
        raw_data: Optional[Dict[str, Any]] = None
        if self.config_path.exists():
            with self.config_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                raise ValueError("YAML must contain a dictionary at top level.")
            raw_data = data
        elif self.defaults is not None:
            raw_data = self.defaults.copy()
        else:
            raise ValueError(f"No YAML found at '{self.config_path}' and no defaults provided.")

        self._full_raw_data = dict(raw_data)
        self._section_container = None
        self._passthrough_metadata = {}

        if self.section_key:
            section = raw_data.get(self.section_key)
            if isinstance(section, dict):
                self._section_container = "root"
                return dict(section)

            experiments = raw_data.get("experiments")
            if isinstance(experiments, dict):
                nested_section = experiments.get(self.section_key)
                if isinstance(nested_section, dict):
                    self._section_container = "experiments"
                    return dict(nested_section)

        editable = dict(raw_data)
        for key in self.PASSTHROUGH_METADATA_KEYS:
            if key in editable:
                self._passthrough_metadata[key] = editable.pop(key)
        return editable

    def save_inputs(self, data: Dict[str, Any]) -> None:
        out = dict(data)

        if self.groups:
            out[self.GROUP_KEY] = self.groups
        if self.tooltips:
            out[self.TOOLTIP_KEY] = self.tooltips
        if self.choices:
            out[self.CHOICES_KEY] = self.choices
        if self.scenario_tooltips:
            out[self.SCENARIO_TOOLTIP_KEY] = self.scenario_tooltips

        if self.section_key and self._section_container:
            full = dict(self._full_raw_data or {})
            if self._section_container == "root":
                full[self.section_key] = out
                out = full
            elif self._section_container == "experiments":
                exps = full.get("experiments")
                if not isinstance(exps, dict):
                    exps = {}
                exps[self.section_key] = out
                full["experiments"] = exps
                out = full
        else:
            out = {**self._passthrough_metadata, **out}

        with self.config_path.open("w", encoding="utf-8") as f:
            yaml.dump(out, f, allow_unicode=True, sort_keys=False)

    # =====================================================
    # Metadata extraction
    # =====================================================
    def _extract_groups(
        self, data: Dict[str, Any]
    ) -> tuple[Dict[str, list[str]], Dict[str, Any]]:
        groups = data.get(self.GROUP_KEY, {}) or {}
        clean = dict(data)
        clean.pop(self.GROUP_KEY, None)

        if not isinstance(groups, dict):
            raise ValueError("__groups__ must be a dictionary")

        for name, keys in groups.items():
            if not isinstance(keys, list):
                raise ValueError(f"Group '{name}' must be a list of field names")
            for k in keys:
                if k not in clean:
                    raise KeyError(f"Grouped key '{k}' not found in YAML")

        return groups, clean

    def _extract_tooltips(
        self, data: Dict[str, Any]
    ) -> tuple[Dict[str, str], Dict[str, Any]]:
        tips = data.get(self.TOOLTIP_KEY, {}) or {}
        clean = dict(data)
        clean.pop(self.TOOLTIP_KEY, None)

        if not isinstance(tips, dict):
            raise ValueError("__tooltips__ must be a dictionary")

        return tips, clean

    def _extract_choices(
        self, data: Dict[str, Any]
    ) -> tuple[Dict[str, list[str]], Dict[str, Any]]:
        choices = data.get(self.CHOICES_KEY, {}) or {}
        clean = dict(data)
        clean.pop(self.CHOICES_KEY, None)

        if not isinstance(choices, dict):
            raise ValueError("__choices__ must be a dictionary")

        for field, options in choices.items():
            if not isinstance(options, list) or not options:
                raise ValueError(f"__choices__['{field}'] must be a non-empty list")
            if field.split(".")[0] not in clean:
                raise KeyError(f"__choices__ key '{field}' not found in YAML")

        return choices, clean

    def _extract_scenario_tooltips(
        self, data: Dict[str, Any]
    ) -> tuple[Dict[str, Dict[str, str]], Dict[str, Any]]:
        per = data.get(self.SCENARIO_TOOLTIP_KEY, {}) or {}
        clean = dict(data)
        clean.pop(self.SCENARIO_TOOLTIP_KEY, None)

        if not isinstance(per, dict):
            raise ValueError("__scenario_tooltips__ must be a dictionary")
        for field, mapping in per.items():
            if not isinstance(mapping, dict) or not mapping:
                raise ValueError(f"__scenario_tooltips__['{field}'] must be a non-empty mapping")
            if field not in clean:
                raise KeyError(f"__scenario_tooltips__ key '{field}' not found in YAML")

        return per, clean

    # =====================================================
    # Type handling
    # =====================================================
    @staticmethod
    def _parse_int_flexible(s: str) -> int:
        s = s.strip().lower()
        try:
            return int(s, 0)
        except ValueError:
            try:
                return int(round(float(s)))
            except ValueError:
                raise ValueError(f"Cannot parse integer from '{s}'")

    @staticmethod
    def _parse_yaml_literal(s: str) -> Any:
        """
        Parse a YAML literal from a string (e.g., '[1,2,3]' or '{a: 1}').
        Falls back to raw string if parsing fails or yields None.
        """
        try:
            v = yaml.safe_load(s)
        except Exception:
            return s
        return s if v is None else v

    def _build_type_map(
        self,
        data: Dict[str, Any],
        *,
        prefix: Tuple[str, ...] = (),
    ) -> Dict[Tuple[str, ...], Callable[[str], Any]]:
        out: Dict[Tuple[str, ...], Callable[[str], Any]] = {}
        for k, v in data.items():
            path = prefix + (k,)
            if isinstance(v, dict):
                if v:
                    out.update(self._build_type_map(v, prefix=path))
                else:
                    out[path] = self._parse_yaml_literal
                continue
            if isinstance(v, bool):
                out[path] = lambda s: str(s).strip().lower() in {"1", "true", "yes", "y", "on"}
            elif isinstance(v, int):
                out[path] = self._parse_int_flexible
            elif isinstance(v, float):
                out[path] = float
            elif isinstance(v, (list, dict)):
                # Important for YAML defaults like [0, 1, 2]
                out[path] = self._parse_yaml_literal
            else:
                out[path] = str
        return out

    @staticmethod
    def _path_to_str(path: Tuple[str, ...]) -> str:
        return ".".join(path)

    def _tooltip_for_path(self, path: Tuple[str, ...]) -> Optional[str]:
        key = self._path_to_str(path)
        if key in self.tooltips:
            return self.tooltips[key]
        if len(path) == 1 and path[0] in self.tooltips:
            return self.tooltips[path[0]]
        return None

    def _current_scenario(self) -> str:
        entry = self.entries.get(("scenario",))
        if entry is not None:
            try:
                return str(entry.get()).strip().upper()
            except Exception:
                pass
        return str(self.data.get("scenario", "")).strip().upper()

    def _tooltip_text_for(self, path: Tuple[str, ...]) -> str:
        """Tooltip body: the scenario-specific line first, then the general note."""
        parts: list[str] = []
        field = path[0] if len(path) == 1 else ""
        per_scenario = self.scenario_tooltips.get(field) if field else None

        if per_scenario:
            scenario = self._current_scenario()
            specific = per_scenario.get(scenario)
            if specific:
                parts.append(f"{scenario}: {specific}")
            elif scenario:
                # ``_default`` lets a family say "undocumented here" instead of
                # claiming the field is unused, which would not be true.
                parts.append(per_scenario.get(self.SCENARIO_DEFAULT_KEY) or f"Not used by {scenario}.")

        base = self._tooltip_for_path(path)
        if base:
            parts.append(base)
        if field and field in self.shared_fields:
            parts.append("Shared across modes - edits here carry over when you switch mode.")
        return "\n\n".join(parts)

    def refresh_scenario_tooltips(self) -> None:
        """Re-point every tooltip at the currently selected scenario."""
        for path, tip in self._tooltips_by_path.items():
            tip.set_text(self._tooltip_text_for(path))

    def _choices_for_path(self, path: Tuple[str, ...]) -> Optional[list[str]]:
        key = self._path_to_str(path)
        if key in self.choices:
            return [str(v) for v in self.choices[key]]
        if len(path) == 1 and path[0] in self.choices:
            return [str(v) for v in self.choices[path[0]]]
        return None

    # =====================================================
    # GUI
    # =====================================================
    def _bring_to_front(self, root: tk.Tk) -> None:
        """
        Force window to front briefly (IDE safe) then release.
        Kept as an instance method so BaseInputGUI and subclasses share behavior.
        """
        root.geometry("+20+20")
        root.update_idletasks()
        root.attributes("-topmost", True)
        root.lift()
        root.focus_force()
        root.after(200, lambda: root.attributes("-topmost", False))

    def open_window(self) -> Dict[str, Any]:
        # Refresh from disk each time (ensures persistence is respected)
        self.refresh_from_disk()

        root = tk.Tk()
        root.title("Experiment Configuration")
        root.resizable(True, True)
        root.minsize(640, 480)
        root.maxsize(1200, 800)
        self._bring_to_front(root)

        def _cancel() -> None:
            self._on_cancel(root)

        root.protocol("WM_DELETE_WINDOW", _cancel)
        root.bind("<Escape>", lambda _: _cancel())
        root.bind("<Return>", lambda _: self._on_submit(root))

        # Scrollable container
        container = ttk.Frame(root)
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(container, highlightthickness=0)
        v_scroll = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        h_scroll = ttk.Scrollbar(container, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

        v_scroll.pack(side="right", fill="y")
        h_scroll.pack(side="bottom", fill="x")
        canvas.pack(side="left", fill="both", expand=True)

        content = ttk.Frame(canvas)
        content_id = canvas.create_window((0, 0), window=content, anchor="nw")

        def _on_content_config(_=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(content_id, width=canvas.winfo_width())

        def _on_canvas_config(_=None) -> None:
            canvas.itemconfigure(content_id, width=canvas.winfo_width())

        content.bind("<Configure>", _on_content_config)
        canvas.bind("<Configure>", _on_canvas_config)

        def _on_mousewheel(event):
            if event.state & 0x0001:  # Shift -> horizontal
                if event.delta:
                    canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")
                elif event.num in (4, 5):
                    canvas.xview_scroll(-1 if event.num == 4 else 1, "units")
            else:
                if event.delta:
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                elif event.num in (4, 5):  # Linux
                    canvas.yview_scroll(-1 if event.num == 4 else 1, "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        canvas.bind_all("<Button-4>", _on_mousewheel)
        canvas.bind_all("<Button-5>", _on_mousewheel)

        self.build_fields(content)

        # Buttons
        btns = ttk.Frame(content)
        btns.pack(pady=10)

        ttk.Button(btns, text="Save & Close", command=lambda: self._on_submit(root)).pack(side="left", padx=6)
        ttk.Button(btns, text="Cancel", command=_cancel).pack(side="left", padx=6)

        root.mainloop()

        if not self._submitted:
            return self.load_inputs()

        return self.data

    # =====================================================
    # 2-column field layout helper
    # =====================================================
    def _cursor_for_group(self, group_name: str) -> "_GridCursor":
        columns = 1 if group_name.strip().lower() in self.single_column_groups else 2
        return _GridCursor(columns=columns)

    def _create_entry_2col(
        self,
        parent: ttk.Frame,
        key: str,
        value: Any,
        *,
        row: int,
        col_slot: int,
        path: Tuple[str, ...],
    ) -> None:
        col_offset = col_slot * 2  # 0 or 2

        label = ttk.Label(parent, text=f"{key}:")
        label.grid(row=row, column=col_offset, padx=(8, 4), pady=4, sticky="e")

        self._tooltips_by_path[path] = _Tooltip(label, self._tooltip_text_for(path))

        options = self._choices_for_path(path)
        if options is not None:
            current = str(value).strip()
            values = list(options)
            # Keep an unlisted current value visible rather than silently
            # switching the user's config to the first option.
            if current and current not in values:
                values.insert(0, current)
            entry = ttk.Combobox(parent, values=values, width=24, state="readonly")
            entry.set(current or values[0])
            entry.grid(row=row, column=col_offset + 1, padx=(4, 12), pady=4, sticky="w")
            self.entries[path] = entry
        elif isinstance(value, bool):
            var = tk.BooleanVar(value=value)
            entry = ttk.Checkbutton(parent, variable=var)
            entry.grid(row=row, column=col_offset + 1, padx=(4, 12), pady=4, sticky="w")
            self.entries[path] = var
        else:
            entry = ttk.Entry(parent, width=26)
            entry.insert(0, str(value))
            entry.grid(row=row, column=col_offset + 1, padx=(4, 12), pady=4, sticky="w")
            self.entries[path] = entry

    def _render_field(
        self,
        parent: ttk.Frame,
        *,
        key: str,
        value: Any,
        cursor: "_GridCursor",
        prefix: Tuple[str, ...],
    ) -> None:
        path = prefix + (key,)
        if isinstance(value, dict) and value:
            row = cursor.next_full_row()
            group = _CollapsibleGroup(parent, self._path_to_str(path))
            group.grid(row=row, column=0, columnspan=cursor.columns * 2, sticky="ew", padx=6, pady=4)
            tip = self._tooltip_for_path(path)
            if tip and group.label is not None:
                _Tooltip(group.label, tip)

            nested_cursor = _GridCursor(columns=cursor.columns)
            for nested_key, nested_val in value.items():
                self._render_field(
                    parent=group.body,
                    key=nested_key,
                    value=nested_val,
                    cursor=nested_cursor,
                    prefix=path,
                )
            return

        row, col_slot = cursor.next_entry_position()
        self._create_entry_2col(
            parent=parent,
            key=key,
            value=value,
            row=row,
            col_slot=col_slot,
            path=path,
        )

    # =====================================================
    # Callbacks
    # =====================================================
    # =====================================================
    # Reusable pieces (shared by the standalone windows and by any host
    # window that embeds this editor, e.g. the 3T1C mode workbench)
    # =====================================================
    def refresh_from_disk(self) -> None:
        """Reload YAML + UI metadata and clear widget state."""
        raw = self.load_inputs()
        self.groups, raw = self._extract_groups(raw)
        self.tooltips, raw = self._extract_tooltips(raw)
        self.choices, raw = self._extract_choices(raw)
        self.scenario_tooltips, raw = self._extract_scenario_tooltips(raw)
        self.data = raw
        self.type_map = self._build_type_map(self.data)
        self.entries = {}
        self._tooltips_by_path = {}
        self._submitted = False

    def _bind_scenario_refresh(self) -> None:
        entry = self.entries.get(("scenario",))
        if entry is None or not self.scenario_tooltips:
            return
        handler = lambda _event=None: self.refresh_scenario_tooltips()
        try:
            entry.bind("<<ComboboxSelected>>", handler, add="+")
            entry.bind("<KeyRelease>", handler, add="+")
        except Exception:
            return

    def build_fields(self, parent: ttk.Frame) -> None:
        """Render every grouped and ungrouped field into ``parent``.

        ``parent`` need not be a window of our own making, so an embedding host
        can lay several editors out side by side and show one at a time.
        """
        used = set()

        for group_name, keys in self.groups.items():
            group = _CollapsibleGroup(parent, group_name)
            group.pack(fill="x", padx=10, pady=6)

            cursor = self._cursor_for_group(group_name)
            for key in keys:
                self._render_field(
                    parent=group.body,
                    key=key,
                    value=self.data[key],
                    cursor=cursor,
                    prefix=(),
                )
                used.add(key)

        remaining = [k for k in self.data if k not in used]
        if remaining:
            group = _CollapsibleGroup(parent, "Other")
            group.pack(fill="x", padx=10, pady=6)
            cursor = self._cursor_for_group("Other")
            for key in remaining:
                self._render_field(
                    parent=group.body,
                    key=key,
                    value=self.data[key],
                    cursor=cursor,
                    prefix=(),
                )

        self._bind_scenario_refresh()

    def apply_values(self, values: Dict[str, Any]) -> list[str]:
        """Push values into already-built widgets. Returns the keys applied.

        Only top-level fields this editor actually renders are touched, so a
        value a family does not have is silently skipped rather than invented.
        """
        applied: list[str] = []
        for key, value in values.items():
            entry = self.entries.get((key,))
            if entry is None:
                continue
            try:
                if isinstance(entry, tk.BooleanVar):
                    entry.set(bool(value))
                elif isinstance(entry, ttk.Combobox):
                    entry.set(str(value))
                else:
                    entry.delete(0, "end")
                    entry.insert(0, "" if value is None else str(value))
            except Exception:
                continue
            applied.append(key)
        return applied

    def collect_values(self, *, save: bool = True) -> Dict[str, Any]:
        """Read the widgets back into a config dict, optionally persisting it."""
        # Preserve keys that have no concrete entry widgets (e.g., empty dict fields
        # such as `power_supply_voltages: {}`), then overwrite edited widget values.
        updated: Dict[str, Any] = copy.deepcopy(self.data)

        for path, entry in self.entries.items():
            raw = entry.get()
            conv = self.type_map.get(path, str)
            try:
                value = raw if isinstance(raw, bool) else conv(raw)
            except Exception:
                value = raw
            self._set_by_path(updated, path, value)

        if save:
            self.save_inputs(updated)
        self.data = updated
        self.type_map = self._build_type_map(self.data)
        return updated

    def _on_submit(self, root: tk.Tk) -> None:
        self.collect_values(save=True)
        self._submitted = True
        root.destroy()

    @staticmethod
    def _set_by_path(target: Dict[str, Any], path: Tuple[str, ...], value: Any) -> None:
        cur = target
        for key in path[:-1]:
            if key not in cur or not isinstance(cur[key], dict):
                cur[key] = {}
            cur = cur[key]
        cur[path[-1]] = value

    def _on_cancel(self, root: tk.Tk) -> None:
        self._submitted = False
        root.destroy()


# =====================================================
# Dropdown style GUI
# =====================================================
class DropdownInputGUI(BaseInputGUI):
    """
    Adds:
      - Phase 1: experiment selection via readonly dropdown (no YAML needed)
      - Phase 2: parameter window with Back/Cancel semantics
    """

    @staticmethod
    def select_experiment_dropdown_popup(
        choices: Sequence[dict[str, str]],
        *,
        title: str = "Select Experiment",
        width: int = 42,
    ) -> Optional[str]:
        """
        Standalone dropdown selector popup.

        Returns:
          - selected experiment key (str)
          - None if canceled/closed/Escape
        """
        root = tk.Tk()
        root.title(title)
        root.resizable(False, False)

        # bring to front
        root.update_idletasks()
        root.attributes("-topmost", True)
        root.lift()
        root.focus_force()
        root.after(200, lambda: root.attributes("-topmost", False))

        selected_key: dict[str, Optional[str]] = {"key": None}

        def _cancel() -> None:
            selected_key["key"] = None
            root.destroy()

        def _submit() -> None:
            disp = combo.get().strip()
            selected_key["key"] = disp_to_key.get(disp)
            root.destroy()

        root.protocol("WM_DELETE_WINDOW", _cancel)
        root.bind("<Escape>", lambda _: _cancel())
        root.bind("<Return>", lambda _: _submit())

        if not choices:
            ttk.Label(root, text="No experiments registered.").pack(padx=14, pady=10)
            ttk.Button(root, text="Close", command=_cancel).pack(pady=(0, 12))
            root.mainloop()
            return None

        disp_to_key: dict[str, str] = {}
        display_values: list[str] = []
        key_to_desc: dict[str, str] = {}

        for c in choices:
            key = (c.get("key") or "").strip()
            dn = (c.get("display_name") or key).strip()
            desc = (c.get("description") or "").strip()
            if not key:
                continue
            disp = dn if dn not in disp_to_key else f"{dn} ({key})"
            disp_to_key[disp] = key
            display_values.append(disp)
            key_to_desc[key] = desc

        if not display_values:
            ttk.Label(root, text="No valid experiment choices.").pack(padx=14, pady=10)
            ttk.Button(root, text="Close", command=_cancel).pack(pady=(0, 12))
            root.mainloop()
            return None

        frm = ttk.Frame(root)
        frm.pack(padx=14, pady=12)

        ttk.Label(frm, text="Experiment:").grid(row=0, column=0, sticky="e", padx=(0, 8), pady=6)

        combo = ttk.Combobox(frm, values=display_values, width=width, state="readonly")
        combo.grid(row=0, column=1, sticky="w", pady=6)
        combo.set(display_values[0])

        desc = ttk.Label(frm, text="", wraplength=420, justify="left")
        desc.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 2))

        def _update_desc(_=None) -> None:
            disp = combo.get().strip()
            k = disp_to_key.get(disp, "")
            desc.config(text=key_to_desc.get(k, ""))

        combo.bind("<<ComboboxSelected>>", _update_desc)
        _update_desc()

        btns = ttk.Frame(root)
        btns.pack(pady=(2, 12))

        ttk.Button(btns, text="Select", command=_submit).pack(side="left", padx=6)
        ttk.Button(btns, text="Cancel", command=_cancel).pack(side="left", padx=6)

        root.mainloop()
        return selected_key["key"]

    def open_window_with_back(
        self,
        *,
        title: str = "Experiment Parameters",
    ) -> Optional[Dict[str, Any]]:
        """
        Parameter editor with Back/Cancel semantics.

        Returns:
          - dict[str, Any] on Save & Close (and persists to YAML)
          - None on Back (does NOT persist)
          - {"__exit__": True} on Cancel (does NOT persist)
        """
        # Refresh from disk each time (ensures persistence is respected)
        self.refresh_from_disk()

        root = tk.Tk()
        root.title(title)
        root.resizable(True, True)
        root.minsize(640, 480)
        root.maxsize(1200, 800)
        self._bring_to_front(root)

        back_requested: dict[str, bool] = {"back": False}
        cancel_requested: dict[str, bool] = {"cancel": False}

        def _on_back() -> None:
            back_requested["back"] = True
            root.destroy()

        def _on_cancel() -> None:
            cancel_requested["cancel"] = True
            root.destroy()

        root.protocol("WM_DELETE_WINDOW", _on_cancel)
        root.bind("<Escape>", lambda _: _on_cancel())
        root.bind("<Return>", lambda _: self._on_submit(root))

        # Scrollable container
        container = ttk.Frame(root)
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(container, highlightthickness=0)
        v_scroll = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        h_scroll = ttk.Scrollbar(container, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

        v_scroll.pack(side="right", fill="y")
        h_scroll.pack(side="bottom", fill="x")
        canvas.pack(side="left", fill="both", expand=True)

        content = ttk.Frame(canvas)
        content_id = canvas.create_window((0, 0), window=content, anchor="nw")

        def _on_content_config(_=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(content_id, width=canvas.winfo_width())

        def _on_canvas_config(_=None) -> None:
            canvas.itemconfigure(content_id, width=canvas.winfo_width())

        content.bind("<Configure>", _on_content_config)
        canvas.bind("<Configure>", _on_canvas_config)

        def _on_mousewheel(event):
            if event.state & 0x0001:  # Shift -> horizontal
                if event.delta:
                    canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")
                elif event.num in (4, 5):
                    canvas.xview_scroll(-1 if event.num == 4 else 1, "units")
            else:
                if event.delta:
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                elif event.num in (4, 5):  # Linux
                    canvas.yview_scroll(-1 if event.num == 4 else 1, "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        canvas.bind_all("<Button-4>", _on_mousewheel)
        canvas.bind_all("<Button-5>", _on_mousewheel)

        self.build_fields(content)

        btns = ttk.Frame(content)
        btns.pack(pady=10)

        ttk.Button(btns, text="Save & Close", command=lambda: self._on_submit(root)).pack(side="left", padx=6)
        ttk.Button(btns, text="Back", command=_on_back).pack(side="left", padx=6)
        ttk.Button(btns, text="Cancel", command=_on_cancel).pack(side="left", padx=6)

        root.mainloop()

        if cancel_requested["cancel"]:
            return {"__exit__": True}
        if back_requested["back"]:
            return None
        if self._submitted:
            return self.data

        # Closed unexpectedly: safest is Back
        return None


# =====================================================
# UI Helpers
# =====================================================
class _GridCursor:
    def __init__(self, *, columns: int = 2) -> None:
        self.columns = max(1, int(columns))
        self.row = 0
        self.col_slot = 0

    def next_entry_position(self) -> tuple[int, int]:
        row = self.row
        col_slot = self.col_slot
        self.col_slot += 1
        if self.col_slot >= self.columns:
            self.col_slot = 0
            self.row += 1
        return row, col_slot

    def next_full_row(self) -> int:
        if self.col_slot != 0:
            self.col_slot = 0
            self.row += 1
        row = self.row
        self.row += 1
        return row


class _CollapsibleGroup(ttk.Frame):
    def __init__(self, parent, title: str):
        super().__init__(parent)
        self._expanded = True
        self.label: Optional[ttk.Label] = None

        header = ttk.Frame(self)
        header.pack(fill="x")

        self.btn = ttk.Button(header, text="▼", width=2, command=self.toggle)
        self.btn.pack(side="left")

        self.label = ttk.Label(
            header,
            text=title,
            font=("TkDefaultFont", 10, "bold"),
        )
        self.label.pack(side="left", padx=4)

        self.body = ttk.Frame(self)
        self.body.pack(fill="x", padx=18, pady=4)

    def toggle(self) -> None:
        if self._expanded:
            self.body.pack_forget()
            self.btn.config(text="▶")
        else:
            self.body.pack(fill="x", padx=18, pady=4)
            self.btn.config(text="▼")
        self._expanded = not self._expanded


class _Tooltip:
    WRAP_LENGTH_PX = 520

    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self.tip: Optional[tk.Toplevel] = None

        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def set_text(self, text: str) -> None:
        """Replace the tip text; a visible tip is torn down so it re-renders."""
        self.text = text
        self._hide()

    def _show(self, _=None) -> None:
        if self.tip or not self.text:
            return

        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + 20

        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")

        ttk.Label(
            self.tip,
            text=self.text,
            wraplength=self.WRAP_LENGTH_PX,
            justify="left",
            background="#ffffe0",
            relief="solid",
            borderwidth=1,
            padding=6,
        ).pack()

    def _hide(self, _=None) -> None:
        if self.tip:
            self.tip.destroy()
            self.tip = None
