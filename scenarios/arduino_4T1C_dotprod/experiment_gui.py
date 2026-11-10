"""Persistent 4T1C window with pulse preview, latest result plot and live logs."""

from __future__ import annotations

import copy
import math
from queue import Empty
import tkinter as tk
from tkinter import messagebox, ttk

from src.utils.base_input_gui import BaseInputGUI, _GridCursor
from .adc_verify import ADC_VERIFY_DEFAULTS
from .lookup_table import LOOKUP_COMMAND, LOOKUP_DEFAULTS, LOOKUP_TOOLTIPS
from .pulse_preview import build_preview
from .preview_canvas import draw_preview
from .preview_viewport import PreviewViewport
from .schematic_canvas import draw_schematic
from .psu_channel_canvas import draw_psu_channels
from .update_bias import UPDATE_COMMANDS, UPDATE_INPUT_DEFAULTS, UPDATE_INPUT_TOOLTIPS, uses_vcap_input


class ExperimentGUI(BaseInputGUI):
    """Render on the Tk thread; instrument work belongs to the session worker."""

    COMMANDS = ("UPDATE", "READ_ACTUAL", "READ_REFERENCE", "UPDATE_AND_READ",
                "RETENTION_TEST", "READ_DISTURB_TEST", LOOKUP_COMMAND, "ADC_VERIFY", "DEBUG_FIRE", "PING", "ABORT")
    EXPERIMENT_GROUPS = ("Update Parameters", "Read Parameters", "Compound Parameters",
                         "Lookup Table Parameters", "ADC Verify Parameters", "Results")
    WIDE_FIELDS = {"command_name", "psu_driver", "psu_resource_1", "psu_resource_2", "visa_backend", "result_dir",
                   "lut_vcap_values", "lut_q_values"}
    GROUP_TITLES = {
        "Command": "실험 명령", "Connection": "Arduino 연결", "PSU Setup": "PSU 연결 · 제어",
        "Channel Mapping": "PSU 채널 매핑", "Initial Bias": "초기 전압 (V)",
        "Update Parameters": "UPDATE 설정", "Read Parameters": "READ 설정",
        "Compound Parameters": "반복 측정 설정", "Results": "결과 저장",
        "ADC Verify Parameters": "ADC 검증 · 외부 R/C (ADC_VERIFY 전용)",
        "Lookup Table Parameters": "룩업 테이블 (LOOK_UP_TABLE_TEST 전용)",
    }
    # Presentation only: entries, YAML keys, conversion and tooltips keep the
    # original parameter names. Short labels let all sections share one grid.
    FIELD_LABELS = {
        "command_name": "Command", "arduino_port": "Arduino port", "arduino_baud": "Baud rate",
        "arduino_boot_wait_s": "부팅 대기 (s)", "serial_timeout_s": "Serial timeout (s)",
        "psu_driver": "PSU driver", "psu_resource_1": "PSU 1 VISA", "psu_resource_2": "PSU 2 VISA",
        "enable_psu_control": "PSU 제어 사용", "psu_settle_s": "PSU 안정화 (s)",
        "psu_timeout_ms": "PSU timeout (ms)", "visa_backend": "VISA backend",
        "safe_shutdown": "종료 시 PSU OFF", "result_dir": "저장 경로",
        "settle_after_opc_us": "OPC 후 대기 (µs)", "opc_timeout_ms": "OPC timeout (ms)",
        "update_gate_settle_us": "MUX 안정화 (µs)",
        "use_vcap_input": "VCAP 입력 사용", "vcap_v": "목표 VCAP (V)",
        "write_bl_topbtm_v": "읽기 바이어스 (V)",
        "write_bl_settle_after_opc_us": "OPC 후 대기 (µs)",
        "write_bl_opc_timeout_ms": "OPC timeout (ms)",
        "write_bl_gate_settle_us": "MUX 안정화 (µs)",
        "bltop_supply": "BLTOP ON PSU", "bltop_channel": "BLTOP ON CH",
        "blbtm_supply": "BLBTM ON PSU", "blbtm_channel": "BLBTM ON CH",
        "bltop_off_supply": "BLTOP OFF PSU", "bltop_off_channel": "BLTOP OFF CH",
        "blbtm_off_supply": "BLBTM OFF PSU", "blbtm_off_channel": "BLBTM OFF CH",
        "vdd_supply": "N1/N2 OFF PSU", "vdd_channel": "N1/N2 OFF CH",
        "vread_supply": "VREAD PSU", "vread_channel": "VREAD CH",
        "bltop_init_v": "BLTOP ON", "blbtm_init_v": "BLBTM ON",
        "bltop_off_init_v": "BLTOP OFF", "blbtm_off_init_v": "BLBTM OFF",
        "vdd_init_v": "N1/N2 OFF", "vread_init_v": "VREAD",
        "adc_channel": "검증 채널 (0–4)", "adc_resistance_kohm": "저항 (kΩ)",
        "adc_capacitance_nf": "적분 C (nF)", "adc_vread_v": "PSU VREAD (V)",
        "adc_reverse_polarity": "VREAD 역결선", "adc_pulse_us": "펄스 폭 (µs)",
        "adc_repeats": "검증 반복", "adc_samples": "ADC 평균 횟수",
        "adc_inter_us": "샘플 간격 (µs)", "adc_settle_us": "ADC 전 대기 (µs)",
        "adc_vref_v": "ADC 기준 (V)", "adc_vinp_v": "VINP (V)",
        "adc_tolerance_pct": "허용 오차 (%)", "adc_tolerance_counts": "허용 오차 (count)",
        "lut_vcap_values": "VCAP 목록 (V)", "lut_q_values": "q 목록", "lut_cycles": "조합별 반복",
    }

    def load_inputs(self):
        """Expose ADC controls even when loading a pre-ADC experiment YAML."""
        values = copy.deepcopy(super().load_inputs())
        for key, default in UPDATE_INPUT_DEFAULTS.items():
            values.setdefault(key, default)
        for key, default in ADC_VERIFY_DEFAULTS.items():
            values.setdefault(key, default)
        for key, default in LOOKUP_DEFAULTS.items():
            values.setdefault(key, default)
            if key != "lut_cycles" and isinstance(values[key], (list, tuple)):
                values[key] = ", ".join(str(value) for value in values[key])
        groups = values.setdefault("__groups__", {})
        update_fields = groups.setdefault("Update Parameters", [])
        update_fields[:] = list(UPDATE_INPUT_DEFAULTS) + [
            key for key in update_fields if key not in UPDATE_INPUT_DEFAULTS]
        fields = groups.setdefault("ADC Verify Parameters", [])
        for key in ADC_VERIFY_DEFAULTS:
            if key not in fields:
                fields.append(key)
        fields = groups.setdefault("Lookup Table Parameters", [])
        for key in LOOKUP_DEFAULTS:
            if key not in fields:
                fields.append(key)
        tooltips = values.setdefault("__tooltips__", {})
        tooltips.update(UPDATE_INPUT_TOOLTIPS)
        tooltips.update(LOOKUP_TOOLTIPS)
        # The legacy optional VDD slot is the user's access-gate OFF supply.
        # Keep its YAML keys and runner mapping compatible with saved presets.
        tooltips["vdd_supply"] = "N1/N2 OFF 공통 전원의 PSU 번호. 0이면 사용하지 않습니다. 저장 키: vdd_supply."
        tooltips["vdd_channel"] = "N1/N2 OFF 공통 전원의 PSU 채널(1–3). 저장 키: vdd_channel."
        tooltips["vdd_init_v"] = "N1/N2 OFF 전원의 초기 PSU 설정값(V). 예: 역결선 2V는 실제 −2V. 극성은 외부 결선으로 정하며 자동 반전하지 않습니다. 저장 키: vdd_init_v."
        tooltips.setdefault("adc_channel", "저항을 선택한 WLUx–BLUx에 연결한 뒤 해당 채널만 실행합니다.")
        tooltips.setdefault("adc_resistance_kohm", "외부 검증 저항의 실제 측정값 또는 공칭값 (kΩ).")
        tooltips.setdefault("adc_capacitance_nf", "선택한 채널의 적분 커패시터 용량 (nF). 셀의 VCAP이 아닙니다.")
        tooltips.setdefault("adc_vread_v", "PSU에 입력할 양의 전압 크기. 역결선 여부는 아래 항목에 지정합니다.")
        tooltips.setdefault("adc_reverse_polarity", "실제 VREAD 결선과 일치시킵니다. 체크 시 계산에 음전압을 사용하며 결선을 자동 변경하지 않습니다.")
        tooltips.setdefault("adc_vinp_v", "적분기의 virtual-ground 기준 전압. 외부 VINP의 실제 공급값과 일치시킵니다.")
        return values

    def __init__(self, config_path, *, validate=None):
        super().__init__(config_path)
        self.validate = validate
        self._preview_job = None
        self._variables = []
        self.session = None
        self._running = False
        self._closing = False
        self._fatal = False
        self._session_closed = False
        self._poll_job = None
        self._result_job = None
        self._result_image = None
        self._result_photo = None
        self._schematic_job = None
        self._psu_channel_job = None
        self._pane_layout_job = None
        self._pane_layout_initialized = False
        self._manual_sashes = set()
        self._field_layouts = {}

    def attach_session(self, session):
        self.session = session

    def _set_inputs_enabled(self, enabled):
        def visit(widget):
            if widget.winfo_class() in ("TEntry", "TCombobox", "TCheckbutton", "TSpinbox"):
                # Preserve readonly on dropdowns when restoring their state.
                widget.state(["!disabled" if enabled else "disabled"])
            for child in widget.winfo_children():
                visit(child)
        visit(self.controls)
        if enabled:
            self._sync_update_inputs()

    def _sync_update_inputs(self):
        """Apply mode-specific states without enabling controls during a run."""
        raw = self._raw_values()
        selected = uses_vcap_input(raw)
        lookup = str(raw.get("command_name", "")).strip().upper() == LOOKUP_COMMAND
        blocked = self._running or self._closing or self._fatal
        for key in ("bltop_v", "blbtm_v", "vcap_v"):
            entry = self.entries.get((key,))
            if entry is not None:
                active = selected if key == "vcap_v" else not selected
                entry.state(["!disabled" if active and not lookup and not blocked else "disabled"])
        for key in (*LOOKUP_DEFAULTS, "use_vcap_input", "q", "cycles"):
            entry = self.entries.get((key,))
            if entry is not None and hasattr(entry, "state"):
                active = lookup if key in LOOKUP_DEFAULTS else not lookup
                entry.state(["!disabled" if active and not blocked else "disabled"])

    def _append_log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        # Keep rendering responsive during long experiments; files keep the
        # full result records independently of this rolling console view.
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 5000:
            self.log_text.delete("1.0", f"{lines - 5000}.0")
        self.log_text.configure(state="disabled")
        if self.follow_logs.get():
            self.log_text.see("end")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _poll_session(self):
        self._poll_job = None
        chunks = []
        # Bound each callback so incoming logs cannot starve window events.
        for _ in range(250):
            try:
                kind, payload = self.session.events.get_nowait()
            except Empty:
                break
            if kind == "log":
                chunks.append(payload)
                continue
            if chunks:
                self._append_log("".join(chunks))
                chunks.clear()
            if kind == "done":
                self._running = False
                self._show_result(payload)
                self.stop_button.configure(text="중단", state="disabled")
                self._fatal = bool(payload.get("fatal"))
                self._set_inputs_enabled(not (self._closing or self._fatal))
                self._refresh_preview()
                if self._closing:
                    self.status.set("실험 종료 · 연결 정리 후 창을 닫습니다.")
                elif str(payload.get("error", "")).startswith("KeyboardInterrupt:"):
                    self.status.set("실험 중단 · 다음 실행은 러너를 다시 시작해 주세요. 장비 정리 결과는 로그를 확인하세요.")
                elif self._fatal:
                    self.status.set("실행 오류 · 로그를 확인한 뒤 러너를 다시 시작해 주세요.")
                elif payload["success"]:
                    self.status.set("실험 완료 · 같은 창에서 다음 실험을 시작할 수 있습니다.")
                else:
                    self.status.set("실행 실패 · 로그와 설정을 확인한 뒤 다시 시작할 수 있습니다.")
            elif kind == "closed":
                self._session_closed = True
                if payload.get("error"):
                    self._fatal = True
                    self._closing = False
                    self.run_button.configure(state="disabled")
                    self.close_button.configure(text="창 닫기", state="normal")
                    self.status.set("연결 정리 중 오류 · 로그와 장비 상태를 확인해 주세요.")
                else:
                    self._close_window()
                    return
        if chunks:
            self._append_log("".join(chunks))
        if not self._session_closed:
            self._poll_job = self.root.after(50, self._poll_session)

    def _clear_result(self, message):
        """Drop only the displayed image; saved artifacts remain on disk."""
        self._result_image = None
        self._result_photo = None
        self.result_message.set(message)
        self.result_canvas.delete("all")
        self._schedule_result()

    def _show_result(self, payload):
        command = payload.get("command_name", self.data.get("command_name", ""))
        number = payload.get("run_number")
        label = f"실험 #{number:03d} · {command}" if number is not None else command
        result = payload.get("result") or {}
        cycles = result.get("disturb_cycles")
        if cycles is not None:
            label += f" · 수신 {cycles['received']}/{cycles['requested']}회"
        points = result.get("lookup_points")
        if points is not None:
            label += f" · 완료 {points['received']}/{points['requested']}조합"
        complete = payload["success"] and result.get("status") == "completed"
        state = "완료" if complete else "오류·중단 또는 미완료"
        path = result.get("plot_path")
        self._clear_result(f"{label} | {state}")
        if path:
            try:
                # Decode/detach once. Tk images and resizing stay on this thread;
                # the worker has finished writing the corresponding PNG.
                from PIL import Image
                with Image.open(path) as source:
                    self._result_image = source.convert("RGB")
                note = "결과 그래프" if complete else "수신된 부분 데이터 그래프"
                self.result_message.set(f"{label} | {note}\n{path}")
            except (ImportError, OSError, ValueError) as exc:
                self.result_message.set(f"{label} | 그래프를 표시하지 못했습니다. 실행 로그를 확인하세요.")
                self._append_log(f"[WARN] Result plot display failed: {path}: {exc}\n")
        else:
            note = ("그래프를 생성할 측정 데이터가 없습니다." if payload["success"] else
                    "표시할 그래프가 없습니다. 실행 로그를 확인하세요.")
            self.result_message.set(f"{label} | {state}\n{note}")
        self.result_tabs.select(self.result_tab)

    def _schedule_result(self):
        if self._result_job is not None:
            self.root.after_cancel(self._result_job)
        self._result_job = self.root.after(80, self._render_result)

    def _render_result(self):
        self._result_job = None
        canvas = self.result_canvas
        width, height = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
        canvas.delete("all")
        if self._result_image is None:
            canvas.create_text(24, 24, anchor="nw", width=max(100, width-48),
                               text=self.result_message.get(), fill="#60758a",
                               font=("TkDefaultFont", 11))
            return
        from PIL import ImageTk
        image = self._result_image
        scale = min(1.0, max(1, width-16)/image.width, max(1, height-16)/image.height)
        size = (max(1, int(image.width*scale)), max(1, int(image.height*scale)))
        try:
            # Hold a strong reference until replacement; fit without stretching
            # the aspect ratio or repeatedly resizing an already scaled image.
            self._result_photo = ImageTk.PhotoImage(image.resize(size), master=self.root)
            canvas.create_image(width/2, height/2, anchor="center", image=self._result_photo)
        except (OSError, tk.TclError) as exc:
            self._clear_result("그래프 표시 오류 · 실행 로그를 확인하세요.")
            self._append_log(f"[WARN] Result plot rendering failed: {exc}\n")

    def _create_entry_2col(self, parent, key, value, *, row, col_slot, path):
        super()._create_entry_2col(parent, key, value, row=row, col_slot=col_slot, path=path)
        tip = self._tooltips_by_path[path]
        label = tip.widget
        label.configure(text=f"{self.FIELD_LABELS.get(key, key)}:")
        tip.set_text(".".join(path) + "\n\n" + self._tooltip_text_for(path))
        label.grid_configure(padx=(4, 4), pady=2)
        # Fixed character widths keep numeric fields compact at any window size
        # while respecting the platform's font/DPI settings.
        widget = parent.grid_slaves(row=row, column=col_slot*2+1)[0]
        widget.grid_configure(padx=(4, 12), pady=2, sticky="w")
        entry = self.entries[path]
        if isinstance(entry, tk.BooleanVar):
            var = entry
        else:
            # StringVar also catches paste, dropdown and programmatic changes.
            var = tk.StringVar(master=entry, value=entry.get())
            # Six characters fit the usual numbers, including 115200 baud.
            # Keep ports, addresses and other text fields spacious.
            width = 6 if isinstance(value, (int, float)) else 10
            if key in ("command_name", "psu_driver"):
                width = 24
            elif key in self.WIDE_FIELDS:
                width = 38
            entry.configure(textvariable=var, width=width)
        self._variables.append(var)
        def changed(*_):
            if key in ("use_vcap_input", "command_name"):
                self._sync_update_inputs()
            self._schedule_preview()
        var.trace_add("write", changed)
        if parent not in self._field_layouts:
            self._configure_field_grid(parent)
        self._field_layouts[parent]["fields"].append((label, widget, key))

    def _schedule_schematic(self):
        if self._schematic_job is not None:
            self.root.after_cancel(self._schematic_job)
        self._schematic_job = self.root.after(80, self._render_schematic)

    def _render_schematic(self):
        self._schematic_job = None
        draw_schematic(self.schematic_canvas, self.schematic_canvas.winfo_width(),
                       self.schematic_canvas.winfo_height())

    def _schedule_psu_channels(self):
        if self._psu_channel_job is not None:
            self.root.after_cancel(self._psu_channel_job)
        self._psu_channel_job = self.root.after(80, self._render_psu_channels)

    def _render_psu_channels(self):
        self._psu_channel_job = None
        draw_psu_channels(self.psu_channel_canvas, self.psu_channel_canvas.winfo_width(),
                          self.psu_channel_canvas.winfo_height())

    def _raw_values(self):
        # Do not use collect_values(save=False): it changes data and type_map
        # when an in-progress numeric edit is temporarily empty/invalid.
        values = copy.deepcopy(self.data)
        for path, entry in self.entries.items():
            self._set_by_path(values, path, entry.get())
        return values

    def _typed_values(self):
        values = copy.deepcopy(self.data)
        raw_values = self._raw_values()
        vcap_mode = uses_vcap_input(raw_values)
        inactive = {"bltop_v", "blbtm_v"} if vcap_mode else {"vcap_v"}
        if str(raw_values.get("command_name", "")).strip().upper() not in UPDATE_COMMANDS:
            inactive = {"bltop_v", "blbtm_v", "vcap_v"}
        lookup = str(raw_values.get("command_name", "")).strip().upper() == LOOKUP_COMMAND
        inactive |= {"q", "cycles", "points", "post_us"} if lookup else {"lut_cycles"}
        for path, entry in self.entries.items():
            if len(path) == 1 and path[0] in inactive:
                # Keep valid manual edits for switching back/reopening. An empty
                # or unfinished inactive edit must not block the selected mode.
                try:
                    raw = entry.get()
                    value = float(raw)
                    if math.isfinite(value):
                        converter = self.type_map.get(path, float)
                        if converter is not int or value.is_integer():
                            # BaseInputGUI converters consume entry text, including
                            # _parse_int_flexible; do not pass the float probe.
                            self._set_by_path(values, path, converter(raw))
                except (ValueError, TypeError, OverflowError):
                    continue
                continue
            raw = entry.get()
            try:
                value = raw if isinstance(raw, bool) else self.type_map.get(path, str)(raw)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{'.'.join(path)} 값을 확인해 주세요: {raw!r}") from exc
            self._set_by_path(values, path, value)
        return values

    def _schedule_preview(self):
        if self._preview_job is not None:
            self.root.after_cancel(self._preview_job)
        self._preview_job = self.root.after(120, self._refresh_preview)

    def _refresh_preview(self):
        self._preview_job = None
        self._sync_update_inputs()
        width = max(1, self.preview_canvas.winfo_width())
        try:
            preview = build_preview(self._raw_values())
        except (ValueError, TypeError, OverflowError) as exc:
            self.preview_canvas.delete("all")
            self.preview_canvas.create_text(24, 28, anchor="nw", width=max(40, width-48),
                text=f"입력값을 확인해 주세요\n\n{exc}", fill="#a44427",
                font=("TkDefaultFont", 12))
            self.preview_canvas.configure(scrollregion=(0, 0, width, 250))
            if not (self._running or self._closing or self._fatal):
                self.status.set("입력 중 · 올바른 값을 입력하면 미리보기가 다시 표시됩니다.")
            self.run_button.configure(state="disabled")
            return
        if preview.command == "READ_REFERENCE":
            self.detail_mode.set("REFERENCE")
        elif not preview.pair:
            self.detail_mode.set("ACTUAL")
        for button in self.detail_buttons:
            button.configure(state="normal" if preview.pair else "disabled")
        viewport = PreviewViewport(self.preview_canvas, width, getattr(self, "_display_scale", 1.0))
        draw_preview(viewport, preview, viewport.logical_width, self.detail_mode.get())
        blocked = self._running or self._closing or self._fatal
        if not blocked:
            self.status.set(preview.errors[0] if preview.errors else
                            "설정 변경 → 미리보기 자동 갱신 · 실험 시작을 누르면 현재 값으로 실행합니다.")
        self.run_button.configure(state="disabled" if preview.errors or blocked else "normal")

    def _submit(self):
        if self._running or self._closing or self._fatal:
            return
        try:
            values = self._typed_values()
            preview = build_preview(values)
            if preview.errors:
                raise ValueError("\n".join(preview.errors))
            if self.validate is not None:
                self.validate(values)
            self.save_inputs(values)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            messagebox.showerror("설정 확인", str(exc), parent=self.root)
            return
        self.data = values
        if self.session.start(values):
            self._clear_result(f"{values['command_name']} 실행 중 · 완료 후 새 결과를 표시합니다.")
            self._submitted = True
            self._running = True
            self._set_inputs_enabled(False)
            self.run_button.configure(state="disabled")
            self.stop_button.configure(text="중단", state="normal")
            self.status.set("실험 실행 중 · 아래 실행 로그에서 진행 상황을 확인할 수 있습니다.")

    def _stop(self):
        if not self._running:
            return
        self.session.interrupt()
        self.stop_button.configure(text="중단 요청됨", state="disabled")
        self.status.set("중단 요청 · 진행 중인 통신이 반환되면 ABORT와 장비 정리를 수행합니다.")

    def _cancel(self):
        if self._session_closed:
            self._close_window()
            return
        if self._closing:
            return
        self._closing = True
        self._set_inputs_enabled(False)
        self.run_button.configure(state="disabled")
        self.close_button.configure(text="종료 대기 중", state="disabled")
        self.status.set("현재 실험 완료 후 연결을 정리하고 종료합니다." if self._running else
                        "연결을 정리하고 종료합니다.")
        self.session.close()

    def _close_window(self):
        if self._pane_layout_job is not None:
            self.root.after_cancel(self._pane_layout_job)
            self._pane_layout_job = None
        if self._preview_job is not None:
            self.root.after_cancel(self._preview_job)
            self._preview_job = None
        if self._poll_job is not None:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        if self._result_job is not None:
            self.root.after_cancel(self._result_job)
            self._result_job = None
        if self._schematic_job is not None:
            self.root.after_cancel(self._schematic_job)
            self._schematic_job = None
        if self._psu_channel_job is not None:
            self.root.after_cancel(self._psu_channel_job)
            self._psu_channel_job = None
        self._result_image = self._result_photo = None
        self.root.destroy()

    def _schedule_pane_layout(self, _event=None):
        # Window mapping/maximization and DPI changes are asynchronous. Debounce
        # actual geometry events instead of assuming the first 100 ms is enough.
        if self._pane_layout_job is not None:
            self.root.after_cancel(self._pane_layout_job)
        self._pane_layout_job = self.root.after(80, self._position_panes)

    def _remember_sash_drag(self, event):
        self._manual_sashes.add(event.widget)

    def _position_panes(self):
        self._pane_layout_job = None
        if not self.root.winfo_ismapped():
            return  # The next Map/Configure event will retry.
        width = self._main_panes.winfo_width()
        height = self._main_panes.winfo_height()
        if width <= 1 or height <= 1:
            return

        # The settings form adapts to its half; its requested width must not
        # push the experiment diagram/logs out of their half of the window.
        current = self._main_panes.sashpos(0)
        target = width // 2
        if current != target:
            self._main_panes.sashpos(0, target)

        output_height = self._output_panes.winfo_height()
        if output_height <= 1:
            # Moving the outer sash may just have made this child visible.
            # Let Tk lay it out before reading/setting its vertical sash.
            self._schedule_pane_layout()
            return
        preview_min = min(240, (output_height - 1) // 2)
        log_min = min(120, (output_height - 1) // 2)
        current = self._output_panes.sashpos(0)
        manual = self._pane_layout_initialized and self._output_panes in self._manual_sashes
        wanted = current if manual else int(output_height * 0.68)
        target = max(preview_min, min(wanted, output_height - log_min))
        if current != target:
            self._output_panes.sashpos(0, target)
        self._pane_layout_initialized = True

    def _maximize(self):
        # Native maximum keeps the titlebar/taskbar, as in the reference photo.
        try:
            self.root.state("zoomed")
        except tk.TclError:
            try:
                self.root.attributes("-zoomed", True)
            except tk.TclError:
                self.root.geometry(f"{self.root.winfo_screenwidth()}x{max(480, self.root.winfo_screenheight()-70)}+0+0")

    def _fullscreen(self, _event=None):
        self.root.attributes("-fullscreen", not bool(self.root.attributes("-fullscreen")))
        return "break"

    def _escape(self, _event=None):
        if self.root.attributes("-fullscreen"):
            self.root.attributes("-fullscreen", False)
        else:
            self._cancel()
        return "break"

    def _scroll_panel(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(frame, highlightthickness=0)
        bar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        content = ttk.Frame(canvas)
        item = canvas.create_window(0, 0, window=content, anchor="nw")
        content.bind("<Configure>", lambda _: canvas.configure(
            scrollregion=(0, 0, canvas.winfo_width(), content.winfo_reqheight())))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(item, width=max(1, e.width)))
        return canvas, content

    def _configure_field_grid(self, body, *, prefer_two_columns=False):
        self._field_layouts[body] = {"fields": [], "width": None,
                                    "prefer_two_columns": prefer_two_columns}
        body.bind("<Configure>", lambda _: self._fit_fields(body))

    def _fit_fields(self, body):
        """Reflow existing widgets, keeping current edits, focus and states."""
        layout = self._field_layouts[body]
        width = body.winfo_width()
        if width <= 1 or width == layout["width"]:
            return
        layout["width"] = width
        fields = layout["fields"]
        if not fields:
            return
        wide = any(key in self.WIDE_FIELDS for _label, _widget, key in fields)
        # Measure each group's labels independently. A long connection label
        # must not push short numeric controls onto a second line elsewhere.
        metrics = [(self._form_font.measure(label.cget("text"))+8, widget.winfo_reqwidth())
                   for label, widget, _key in fields]
        pair_widths = [sum(max(size[i] for size in metrics[slot::2]) for i in (0, 1))+20
                       for slot in range(min(2, len(fields)))]
        columns = 2 if not wide and len(fields) > 1 and width >= sum(pair_widths) else 1
        prefer_two = layout.get("prefer_two_columns", False) and not wide and len(fields) > 1
        value_width = max(size[1] for size in metrics)
        if prefer_two and width >= 2*(value_width+self._form_font.measure("0000")+28):
            columns = 2
        # Numeric values stay beside their labels, using one pair per row if
        # two do not fit. Only long text fields may put the input below its label.
        inline = not wide or width >= metrics[0][0]+self._entry_pixels+20
        label_widths = [max(size[0] for size in metrics[slot::columns])
                        for slot in range(columns)]
        if prefer_two and columns == 2:
            # UPDATE/READ retain two equal pairs, wrapping long labels within
            # each half instead of turning the entire group into one column.
            label_widths = [min(max(label_widths), width//2-value_width-20)]*2
        for col in range(4):
            body.columnconfigure(col, minsize=0, weight=0, uniform="")
        for slot in range(columns):
            if inline:
                value_width = (self._entry_pixels if wide else
                               max(size[1] for size in metrics[slot::columns]))
                label_widths[slot] = min(label_widths[slot], max(1, width-value_width-20))
                body.columnconfigure(slot*2, minsize=label_widths[slot])
                body.columnconfigure(slot*2+1, weight=1,
                                     uniform="values" if prefer_two and columns == 2 else "")
            else:
                body.columnconfigure(slot, weight=1, uniform="fields")
        for index, (label, widget, key) in enumerate(fields):
            row, slot = divmod(index, columns)
            if inline:
                label.configure(wraplength=label_widths[slot])
                label.grid_configure(row=row, column=slot*2, padx=(4, 4), pady=2, sticky="e")
                widget.grid_configure(row=row, column=slot*2+1, padx=(4, 8), pady=2,
                                      sticky="ew" if wide else "w")
            else:
                label.configure(wraplength=max(40, width//columns-16))
                label.grid_configure(row=row*2, column=slot, padx=(4, 8), pady=(4, 0), sticky="w")
                widget.grid_configure(row=row*2+1, column=slot, padx=(4, 8), pady=(0, 4),
                                      sticky="ew" if wide else "w")

    def _fit_settings(self, content, hardware, experiment):
        width = content.winfo_width()
        if width <= 1:
            return
        columns = 2 if width >= 2*max(280, self._entry_pixels*2+48)+20 else 1
        if columns == self._settings_columns:
            return
        self._settings_columns = columns
        content.columnconfigure(0, weight=1, uniform="settings" if columns == 2 else "")
        content.columnconfigure(1, weight=columns-1, uniform="settings" if columns == 2 else "")
        hardware.grid_configure(row=0, column=0, padx=(4, 6) if columns == 2 else (4, 4))
        experiment.grid_configure(row=0 if columns == 2 else 1, column=columns-1,
                                  padx=(6, 4) if columns == 2 else (4, 4))

    def _render_group(self, content, name, keys):
        group = ttk.LabelFrame(content, padding=(6, 4))
        heading = ttk.Label(group, text=self.GROUP_TITLES.get(name, name))
        group.configure(labelwidget=heading)
        group.bind("<Configure>", lambda e: heading.configure(wraplength=max(40, e.width-20)))
        group.pack(fill="x", pady=(0, 8), anchor="n")
        body = None
        for key in keys:
            if key in self.WIDE_FIELDS:
                # Keep long paths/addresses separate from the compact values.
                body = None
                wide_row = ttk.Frame(group)
                wide_row.pack(fill="x", anchor="nw")
                self._configure_field_grid(wide_row)
                self._create_entry_2col(wide_row, key, self.data[key], row=0, col_slot=0, path=(key,))
            else:
                if body is None:
                    body = ttk.Frame(group)
                    body.pack(fill="x", anchor="nw")
                    self._configure_field_grid(body, prefer_two_columns=name in (
                        "Update Parameters", "Read Parameters"))
                    cursor = _GridCursor(columns=2)
                self._render_field(body, key=key, value=self.data[key], cursor=cursor, prefix=())

    def _build_settings(self, content):
        # Both sections share the available half-window width. Narrow windows
        # stack them vertically instead of hiding fields off the right edge.
        content.columnconfigure(0, uniform="settings", weight=1)
        content.columnconfigure(1, uniform="settings", weight=1)
        hardware = ttk.Frame(content)
        experiment = ttk.Frame(content)
        hardware.grid(row=0, column=0, sticky="new", padx=(4, 6), pady=4)
        experiment.grid(row=0, column=1, sticky="new", padx=(6, 4), pady=4)
        self._settings_columns = None
        content.bind("<Configure>", lambda _: self._fit_settings(content, hardware, experiment), add="+")
        used = set()
        for name in ("Command", *self.EXPERIMENT_GROUPS):
            keys = (["command_name"] if name == "Command" else
                    [k for k in self.groups.get(name, ()) if k not in used])
            if keys:
                self._render_group(experiment, name, keys)
                used.update(keys)
        for name, group_keys in self.groups.items():
            keys = [k for k in group_keys if k not in used]
            if keys:
                self._render_group(hardware, name, keys)
                used.update(keys)
        keys = [k for k in self.data if k not in used]
        if keys:
            self._render_group(experiment, "Other", keys)

    def open_window(self):
        if self.session is None:
            raise RuntimeError("Attach an experiment session before opening the GUI.")
        self.refresh_from_disk()
        self.data.setdefault("command_name", "READ_ACTUAL")
        self.choices["command_name"] = list(self.COMMANDS)
        self._variables = []
        self._preview_job = None
        self._field_layouts = {}
        self.root = root = tk.Tk()
        from tkinter import font as tkfont
        font = tkfont.nametofont("TkDefaultFont", root=root)
        self._display_scale = max(0.5, root.winfo_fpixels("1i") / 96)
        self._form_font = font
        self._entry_pixels = font.measure("0"*10) + 20
        root.title("4T1C · Experiment Console")
        root.minsize(min(1050, root.winfo_screenwidth()), min(650, root.winfo_screenheight()))
        root.geometry("1280x820")
        root.protocol("WM_DELETE_WINDOW", self._cancel)
        root.bind("<Escape>", self._escape)
        root.bind("<F11>", self._fullscreen)
        root.bind("<Control-Return>", lambda _: self._submit())

        header = ttk.Frame(root, padding=(12, 6))
        header.pack(fill="x")
        header.columnconfigure(0, weight=1)
        title = ttk.Label(header, text="4T1C  |  QK single-cell experiment", font=("TkDefaultFont", 13, "bold"))
        title.grid(row=0, column=0, sticky="w")
        title.bind("<Configure>", lambda e: title.configure(wraplength=max(40, e.width)))
        ttk.Label(header, text="F11 전체화면 · Esc 복귀", foreground="#60758a").grid(row=0, column=1, sticky="e")

        panes = ttk.Panedwindow(root, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=10)
        controls = self.controls = ttk.Frame(panes)
        right = ttk.Panedwindow(panes, orient="vertical")
        preview_frame = ttk.Frame(right)
        log_frame = ttk.Frame(right)
        panes.add(controls, weight=1)
        panes.add(right, weight=1)
        right.add(preview_frame, weight=3)
        right.add(log_frame, weight=1)

        self.parameter_canvas, settings_content = self._scroll_panel(controls)
        self._build_settings(settings_content)
        self._main_panes = panes
        self._output_panes = right
        self._settings_content = settings_content
        self._pane_layout_initialized = False
        self._manual_sashes = set()

        self.result_tabs = ttk.Notebook(preview_frame)
        self.result_tabs.pack(fill="both", expand=True)
        scheme_tab = ttk.Frame(self.result_tabs)
        self.result_tab = ttk.Frame(self.result_tabs)
        schematic_tab = ttk.Frame(self.result_tabs)
        psu_channel_tab = ttk.Frame(self.result_tabs)
        self.result_tabs.add(scheme_tab, text="펄스 스킴")
        self.result_tabs.add(self.result_tab, text="실험 결과")
        self.result_tabs.add(schematic_tab, text="Schematic")
        self.result_tabs.add(psu_channel_tab, text="PSU CH")

        psu_note = ttk.Label(psu_channel_tab, text="첨부 기준 배선표 · 실제 PSU 제어는 왼쪽 채널 매핑을 따릅니다.",
                             foreground="#60758a", padding=(10, 6))
        psu_note.pack(fill="x")
        psu_note.bind("<Configure>", lambda e: psu_note.configure(wraplength=max(40, e.width-20)))
        self.psu_channel_canvas = tk.Canvas(psu_channel_tab, background="white", highlightthickness=0)
        self.psu_channel_canvas.pack(fill="both", expand=True)
        self.psu_channel_canvas.bind("<Configure>", lambda _: self._schedule_psu_channels())

        self.schematic_canvas = tk.Canvas(schematic_tab, background="white", highlightthickness=0)
        self.schematic_canvas.pack(fill="both", expand=True)
        self.schematic_canvas.bind("<Configure>", lambda _: self._schedule_schematic())

        preview_header = ttk.Frame(scheme_tab, padding=(10, 0, 6, 4))
        preview_header.pack(fill="x")
        ttk.Label(preview_header, text="펄스 스킴 미리보기", font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        detail_controls = ttk.Frame(preview_header)
        detail_controls.pack(anchor="w")
        self.detail_mode = tk.StringVar(master=root, value="ACTUAL")
        self.detail_buttons = []
        for mode in ("ACTUAL", "REFERENCE"):
            button = ttk.Radiobutton(detail_controls, text=mode, value=mode, variable=self.detail_mode,
                                      command=self._schedule_preview)
            button.pack(side="left", padx=(0, 12))
            self.detail_buttons.append(button)
        chart_frame = ttk.Frame(scheme_tab)
        chart_frame.pack(fill="both", expand=True)
        self.preview_canvas = tk.Canvas(chart_frame, background="white", highlightthickness=0)
        preview_scroll = ttk.Scrollbar(chart_frame, orient="vertical", command=self.preview_canvas.yview)
        self.preview_canvas.configure(yscrollcommand=preview_scroll.set)
        preview_scroll.pack(side="right", fill="y")
        self.preview_canvas.pack(fill="both", expand=True)
        self.preview_canvas.bind("<Configure>", lambda _: self._schedule_preview())

        self.result_message = tk.StringVar(master=root)
        result_header = ttk.Label(self.result_tab, textvariable=self.result_message,
                                  padding=(10, 6), foreground="#23445b")
        result_header.pack(fill="x")
        result_header.bind("<Configure>", lambda e: result_header.configure(wraplength=max(100, e.width-20)))
        self.result_canvas = tk.Canvas(self.result_tab, background="white", highlightthickness=0)
        self.result_canvas.pack(fill="both", expand=True)
        self.result_canvas.bind("<Configure>", lambda _: self._schedule_result())
        self._clear_result("실험을 시작하면 완료 후 이 탭에 결과 그래프가 표시됩니다.")

        log_header = ttk.Frame(log_frame, padding=(10, 6))
        log_header.pack(fill="x")
        ttk.Label(log_header, text="실행 로그 · Arduino / PSU", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        log_controls = ttk.Frame(log_header)
        log_controls.pack(anchor="e")
        self.follow_logs = tk.BooleanVar(master=root, value=True)
        ttk.Checkbutton(log_controls, text="자동 스크롤", variable=self.follow_logs).pack(side="right")
        ttk.Button(log_controls, text="화면 지우기", command=self._clear_log).pack(side="right", padx=8)
        console_frame = ttk.Frame(log_frame)
        console_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(console_frame, height=12, width=1, wrap="word", state="disabled",
                                background="#132234", foreground="#e4edf5", insertbackground="white",
                                font=("Consolas", 10), padx=10, pady=8)
        log_scroll = ttk.Scrollbar(console_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)

        # Route wheel events only to the pane under the pointer.
        def wheel(event):
            target = event.widget
            if target == self.preview_canvas:
                canvas = self.preview_canvas
            else:
                canvas = None
                while target is not None:
                    if target == controls:
                        canvas = self.parameter_canvas
                        break
                    target = getattr(target, "master", None)
            if canvas is not None:
                delta = (-1 if event.delta > 0 else 1) if event.delta else (-1 if event.num == 4 else 1)
                canvas.yview_scroll(delta*3, "units")
                return "break"
        root.bind_all("<MouseWheel>", wheel)
        root.bind_all("<Button-4>", wheel)
        root.bind_all("<Button-5>", wheel)

        footer = ttk.Frame(root, padding=(12, 6))
        footer.pack(fill="x")
        footer.columnconfigure(0, weight=1)
        self.status = tk.StringVar(master=root)
        status_label = ttk.Label(footer, textvariable=self.status, wraplength=700)
        status_label.grid(row=0, column=0, sticky="ew")
        status_label.bind("<Configure>", lambda e: status_label.configure(wraplength=max(40, e.width-12)))
        buttons = ttk.Frame(footer)
        buttons.grid(row=0, column=1, sticky="e")
        self.close_button = ttk.Button(buttons, text="종료", command=self._cancel)
        self.close_button.pack(side="right", padx=(10, 0))
        self.stop_button = ttk.Button(buttons, text="중단", command=self._stop, state="disabled")
        self.stop_button.pack(side="right", padx=(10, 0))
        self.run_button = ttk.Button(buttons, text="설정 저장 · 실험 시작", command=self._submit)
        self.run_button.pack(side="right")
        root.update_idletasks()
        self._maximize()
        panes.bind("<Map>", self._schedule_pane_layout)
        panes.bind("<Configure>", self._schedule_pane_layout)
        right.bind("<Configure>", self._schedule_pane_layout)
        right.bind("<B1-Motion>", self._remember_sash_drag, add="+")
        settings_content.bind("<Configure>", self._schedule_pane_layout, add="+")
        self._schedule_pane_layout()
        self._refresh_preview()
        self._poll_job = root.after(50, self._poll_session)
        root.mainloop()
        return self.data
