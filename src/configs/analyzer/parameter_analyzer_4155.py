from __future__ import annotations
__version__ = "2.12.0"

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Dict, Optional, Literal, Any, Mapping, List, Tuple, Union, Iterable, Callable
import time

from tqdm import tqdm
import pyvisa as visa

from src.configs.measurement_device import MeasurementDevice


# ======================================================================================
# Dataclasses
# ======================================================================================

ChannelKind = Literal["SMU", "VMU", "VSU"]
ChanMode = str
ChanFunc = str


@dataclass(frozen=True)
class Names:
    vname: str
    iname: str = ""


@dataclass(frozen=True)
class SweepSpec:
    var: Literal[1, 2]
    start: float
    step: float
    stop: Optional[float] = None
    points: Optional[int] = None
    compliance: float = 0.1
    mode: Literal["SINGle", "DOUBle"] = "SINGle"


@dataclass(frozen=True)
# TODO: Constant modes not supporting current mode as of now.
class ConstSpec:
    value: float
    compliance: float


@dataclass(frozen=True)
class SweepChannelConfig:
    mode: ChanMode
    func: Optional[ChanFunc] = None
    names: Optional[Names] = None
    const: Optional[ConstSpec] = None
    sweep: Optional[SweepSpec] = None


@dataclass(frozen=True)
class SamplingTiming:
    interval_s: float
    points: int
    integration: str
    mode: str


@dataclass(frozen=True)
class SamplingChannelConfig:
    mode: ChanMode
    func: Optional[ChanFunc] = None
    names: Optional[Names] = None
    const: Optional[ConstSpec] = None


@dataclass(frozen=True)
class StressChannelConfig:
    mode: ChanMode
    func: ChanFunc
    const: ConstSpec


SamplingTask = Callable[[], Any]


# ======================================================================================
# Driver
# ======================================================================================

class ParameterAnalyzer4155(MeasurementDevice):
    MAX_TRACE_HEADERS = 8

    def __init__(
        self,
        resource_manager: visa.ResourceManager,
        resource_name: str,
        *,
        timeout: int = 20_000,
        debug_mode: bool = False,
    ) -> None:
        super().__init__(resource_manager, resource_name, timeout=timeout, debug_mode=debug_mode)
        self._active_vars: Dict[int, str] = {}
        self.max_trace_headers: int = int(self.MAX_TRACE_HEADERS)

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        self.log("Initializing 4155 parameter analyzer", level="info")
        self.connect()

        self.write("*RST")
        if self.debug_mode:
            self.log("Instrument reset issued", level="debug")

        for smu in range(1, 5):
            self.write(f":PAGE:CHAN:SMU{smu}:FUNC CONS")

        self._active_vars.clear()
        self.log("Safe-start: all SMUs set to CONST, VAR map cleared", level="info")

        self.write("*CLS")
        self.write("*ESE 1")

        if self.device is not None:
            self.device.enable_event(
                visa.constants.EventType.service_request,
                visa.constants.EventMechanism.queue,
            )
            self.write("*SRE 128")

        self.log("Initialization complete", level="info")

    # ==================================================================================
    # Public APIs
    # ==================================================================================

    def set_sweep(
        self,
        channel_cfg: Mapping[str, Union[SweepChannelConfig, Dict[str, Any]]],
        *,
        integration: str = "MED",
        display_list: Optional[List[str]] = None,
    ) -> None:
        self.log(
            f"Configuring sweep mode for {len(channel_cfg)} channel(s)...",
            level="info",
        )

        var_assignments = self._collect_var_assignments(channel_cfg)
        clear_cmds = self._build_var_clear_commands(var_assignments)
        for var, ch in var_assignments.items():
            prev = self._active_vars.get(var)
            if prev and prev != ch:
                self._active_vars.pop(var, None)
                if self.debug_mode:
                    self.log(f"VAR{var} reassigned from {prev} to {ch}", level="debug")

        cmds = clear_cmds + self._build_sweep_commands(channel_cfg, integration)

        disp_headers = (
            self._collect_display_names_from_sweep_cfg(channel_cfg)
            if display_list is None
            else self._sanitize_display_names(display_list)
        )
        disp_headers = self._limit_trace_headers(disp_headers, context="Sweep display list")
        if disp_headers:
            cmds.append(self._build_display_list_command(disp_headers))

        for c in cmds:
            if self.debug_mode:
                self.log(f"SEND: {c}", level="debug")
            self.write(c)

        self.log("Sweep configuration completed", level="info")

    def set_sampling(
        self,
        timing: Union[SamplingTiming, Dict[str, Any]],
        channel_cfg: Mapping[str, Union[SamplingChannelConfig, Dict[str, Any]]],
        *,
        display_list: Optional[List[str]] = None,
    ) -> None:
        self.log(
            f"Configuring sampling mode for {len(channel_cfg)} channel(s)...",
            level="info",
        )

        cmds = self._build_sampling_commands(timing, channel_cfg)

        disp_headers = (
            self._collect_display_names_from_sampling_cfg(channel_cfg)
            if display_list is None
            else self._sanitize_display_names(display_list)
        )
        disp_headers = self._limit_trace_headers(disp_headers, context="Sampling display list")
        if disp_headers:
            cmds.append(self._build_display_list_command(disp_headers))

        for c in cmds:
            if self.debug_mode:
                self.log(f"SEND: {c}", level="debug")
            self.write(c)

        self.log("Sampling configuration completed", level="info")

    def set_stress(
        self,
        channel_cfg: Mapping[str, Union[StressChannelConfig, Dict[str, Any]]],
    ) -> None:
        self.log(
            f"Configuring stress channels ({len(channel_cfg)} channel(s))...",
            level="info",
        )

        cmds = self._build_stress_commands(channel_cfg)

        for c in cmds:
            if self.debug_mode:
                self.log(f"SEND: {c}", level="debug")
            self.write(c)

        self.log("Stress configuration completed", level="info")

    def set_qscv(
        self,
        *,
        load_slot: Optional[int] = None,
        load_mode: str = "MES",
        setup_commands: Optional[List[str]] = None,
        ensure_qscv_mode: bool = False,
    ) -> None:
        """
        Configure quasi-static C-V (QSCV) measurement mode.

        Compliant with the 4156 QSCV subsystem: the measurement-page mode is
        selected with ``:PAGE:CHAN:MODE QSCV`` (4156 SCPI reference, MODE
        command). Detailed QSCV parameters (CINTeg/IINTeg/DELay/HTIMe/VAR1:*,
        etc.) are expected to come from a saved analyzer slot or explicit
        ``setup_commands``; this driver does not compute capacitance — the
        instrument reports it.

        load_slot        optional memory slot holding a saved QSCV (MES) setup.
        load_mode        scenario memory type used with load_slot (default MES).
        setup_commands   optional list of already-validated QSCV SCPI strings,
                         sent in order after any slot load.
        ensure_qscv_mode when True, explicitly issue ``:PAGE:CHAN:MODE QSCV``
                         before the setup commands. A loaded slot already
                         restores its own page mode, so this is mainly for the
                         manual setup_commands path.
        """
        self.log("Configuring QSCV measurement mode...", level="info")

        if load_slot is not None:
            self.load_scenario(int(load_slot), str(load_mode or "MES"))

        cmds: List[str] = []
        if ensure_qscv_mode:
            cmds.append(":PAGE:CHAN:MODE QSCV")
        cmds.extend(str(c) for c in (setup_commands or []) if str(c).strip())

        for c in cmds:
            if self.debug_mode:
                self.log(f"SEND: {c}", level="debug")
            self.write(c)

        self.log("QSCV configuration completed", level="info")

    # ==================================================================================
    # Compatibility helpers (legacy-style APIs)
    # ==================================================================================

    def set_sweep_common(self, smu: int, vname: str, iname: str) -> None:
        self._warn_deprecated("set_sweep_common", "set_sweep")
        self.set_sweep(
            {
                f"SMU{smu}": SweepChannelConfig(
                    mode="COMM",
                    func="CONS",
                    names=Names(vname=vname, iname=iname),
                )
            }
        )

    def set_sweep_const_voltage(
        self,
        smu: int,
        value: float,
        vname: str,
        iname: str,
        compliance: float = 0.1,
    ) -> None:
        self._warn_deprecated("set_sweep_const_voltage", "set_sweep")
        self.set_sweep(
            {
                f"SMU{smu}": SweepChannelConfig(
                    mode="V",
                    func="CONS",
                    names=Names(vname=vname, iname=iname),
                    const=ConstSpec(value=value, compliance=compliance),
                )
            }
        )

    def set_sweep_var_voltage(
        self,
        smu: int,
        sweep_var: int,
        vstart: float,
        vstop: float,
        vstep: float,
        vname: str,
        iname: str,
        compliance: float = 0.1,
        integration: str = "SHORT",
        sweep_mode: str = "SINGle",
    ) -> None:
        self._warn_deprecated("set_sweep_var_voltage", "set_sweep")
        var = sweep_var if sweep_var in (1, 2) else 1
        self.set_sweep(
            {
                f"SMU{smu}": SweepChannelConfig(
                    mode="V",
                    func=f"VAR{var}",
                    names=Names(vname=vname, iname=iname),
                    sweep=SweepSpec(
                        var=var,
                        start=vstart,
                        step=vstep,
                        stop=vstop,
                        compliance=compliance,
                        mode=self._normalize_sweep_mode(sweep_mode),
                    ),
                )
            },
            integration=integration,
        )

    def set_sampling_timing(
        self,
        interval: float,
        points: int = 1,
        integration: str = "MED",
        mode: str = "LIN",
    ) -> None:
        self._warn_deprecated("set_sampling_timing", "set_sampling")
        cmds = self._build_sampling_timing_commands(
            SamplingTiming(
                interval_s=interval,
                points=points,
                integration=integration,
                mode=mode,
            )
        )
        for c in cmds:
            if self.debug_mode:
                self.log(f"SEND: {c}", level="debug")
            self.write(c)

    def set_sampling_mode(
        self,
        smu: int,
        voltage: float,
        mode: str = "V",
        compliance: float = 0.1,
        vname: str = "VG",
        iname: str = "IG",
    ) -> None:
        self._warn_deprecated("set_sampling_mode", "set_sampling")
        cmds = [":PAGE:CHAN:MODE SAMP"]
        cmds.extend(
            self._build_sampling_channel_commands(
                {
                    f"SMU{smu}": SamplingChannelConfig(
                        mode=mode,
                        func="CONS",
                        names=Names(vname=vname, iname=iname),
                        const=ConstSpec(value=voltage, compliance=compliance),
                    )
                }
            )
        )
        for c in cmds:
            if self.debug_mode:
                self.log(f"SEND: {c}", level="debug")
            self.write(c)

    def set_sampling_vmu(
        self,
        vmu: int,
        *,
        vname: str = "VOUT",
    ) -> None:
        self._warn_deprecated("set_sampling_vmu", "set_sampling")
        cmds = [":PAGE:CHAN:MODE SAMP"]
        cmds.extend(
            self._build_sampling_channel_commands(
                {
                    f"VMU{vmu}": SamplingChannelConfig(
                        mode="V",
                        func=None,
                        names=Names(vname=vname),
                        const=None,
                    )
                }
            )
        )
        for c in cmds:
            if self.debug_mode:
                self.log(f"SEND: {c}", level="debug")
            self.write(c)

    def set_stress_voltage(self, smu: int, voltage: float, compliance: float = 0.1) -> None:
        self._warn_deprecated("set_stress_voltage", "set_stress")
        self.set_stress(
            {
                f"SMU{smu}": StressChannelConfig(
                    mode="V",
                    func="SYNC",
                    const=ConstSpec(value=voltage, compliance=compliance),
                )
            }
        )

    def save_scenario(self, slot: int, mode: str) -> None:
        self.write(f":MMEM:STOR:STAT 0, 'MEM{slot}.{mode}', 'MEMORY'")
        self.log(f"Saved scenario slot {slot} mode {mode}", level="info")

    def load_scenario(self, slot: int, mode: str) -> None:
        self.write(f":MMEM:LOAD:STAT 0, 'MEM{slot}.{mode}', 'MEMORY'")
        self.log(f"Loaded scenario slot {slot} mode {mode}", level="info")

    def set_display_list(self, names: List[str]) -> List[str]:
        """
        Update the DISPLAY LIST used by :TRAC? retrieval.
        Returns the sanitized list actually sent to the instrument.
        """
        clean = self._sanitize_display_names(names)
        clean = self._limit_trace_headers(clean, context="Display list")
        if not clean:
            raise ValueError("Display list is empty.")

        cmd = self._build_display_list_command(clean)
        if self.debug_mode:
            self.log(f"SEND: {cmd}", level="debug")
        self.write(cmd)
        self.log(f"Display list set: {clean}", level="info")
        return clean

    def set_display(self, enabled: bool, *, verify: bool = True) -> bool:
        """
        Control front-panel display rendering.
        :DISP ON enables display updates, :DISP OFF disables them for higher throughput.
        Returns True when the requested state is applied.
        """
        target = 1 if bool(enabled) else 0
        cmd = ":DISP ON" if target == 1 else ":DISP OFF"

        if not verify:
            self.write(cmd)
            self.log(f"Display set command sent ({'ON' if target else 'OFF'})", level="info")
            return True

        ok = self.write_and_verify(cmd, ":DISP?", target)
        if ok:
            self.log(f"Display {'enabled' if target else 'disabled'}", level="info")
            return True

        state = self.get_display_state()
        self.log(
            f"Display set verify failed (requested={target}, actual={state})",
            level="warning",
        )
        return False

    def get_display_state(self) -> Optional[int]:
        """
        Query display state via :DISP?.
        Returns:
            1 -> ON
            0 -> OFF
            None -> query/parse failure
        """
        raw = self._query_text(":DISP?")
        if raw is None:
            return None

        token = raw.strip().upper()
        if token in {"1", "ON"}:
            return 1
        if token in {"0", "OFF"}:
            return 0

        try:
            return 1 if int(float(token)) != 0 else 0
        except ValueError:
            self.log(f"Unexpected :DISP? response '{raw}'", level="warning")
            return None

    def display_on(self, *, verify: bool = True) -> bool:
        return self.set_display(True, verify=verify)

    def display_off(self, *, verify: bool = True) -> bool:
        return self.set_display(False, verify=verify)

    def get_channel_config(self, channel: str) -> Dict[str, Any]:
        """
        Query and return best-effort channel settings for one channel.

        Notes:
        - Avoids probing unsupported query paths that can stall some 4155 firmware.
        - Constant/compliance queries are selected by current page mode (SWE/SAMP),
          and only queried when channel FUNC is constant-source.
        """
        ch, kind, _idx = self._parse_channel_key(channel)
        cfg: Dict[str, Any] = {"channel": ch}

        scalar_queries: Dict[str, str] = {
            "mode": f":PAGE:CHAN:{ch}:MODE?",
            "vname": f":PAGE:CHAN:{ch}:VNAM?",
        }
        if kind in {"SMU", "VSU"}:
            scalar_queries["func"] = f":PAGE:CHAN:{ch}:FUNC?"
        if kind == "SMU":
            scalar_queries["iname"] = f":PAGE:CHAN:{ch}:INAM?"

        for key, cmd in scalar_queries.items():
            text = self._query_text(cmd)
            if text is not None:
                cfg[key] = text

        if kind != "VMU":
            mode_text = str(cfg.get("mode", "")).strip().upper()
            func_text = str(cfg.get("func", "")).strip().upper()

            should_query_const = (
                self._supports_const(kind)
                and self._is_constant_function(func_text)
                and not self._is_common_channel_mode(mode_text)
            )
            if not should_query_const:
                if self.debug_mode:
                    self.log(
                        f"Skipping const/compliance query for {ch} (mode={mode_text or '-'}, func={func_text or '-'})",
                        level="debug",
                    )
                return cfg

            page_mode = (self._query_text(":PAGE:CHAN:MODE?") or "").strip().upper()

            if page_mode == "SAMP":
                vdc_cmd = f":PAGE:MEAS:SAMP:CONS:{ch}?"
                comp_cmd = f":PAGE:MEAS:SAMP:CONS:{ch}:COMP?"
            elif page_mode == "SWE":
                vdc_cmd = f":PAGE:MEAS:CONS:{ch}?"
                comp_cmd = f":PAGE:MEAS:CONS:{ch}:COMP?"
            else:
                vdc_cmd = ""
                comp_cmd = ""

            if vdc_cmd:
                text = self._query_text(vdc_cmd)
                if text is not None:
                    try:
                        cfg["vdc"] = float(text)
                    except ValueError:
                        cfg["vdc"] = text

            if comp_cmd:
                text = self._query_text(comp_cmd)
                if text is not None:
                    try:
                        cfg["compliance"] = float(text)
                    except ValueError:
                        cfg["compliance"] = text

        return cfg

    def set_fast_sweep_profile(
        self,
        *,
        wait_s: float = 0.0,
        integration: str = "SHORT",
        aperture: str = "MIN",
    ) -> None:
        """
        Apply short settling/integration profile for faster sweep execution.
        """
        wait_s = max(float(wait_s), 0.0)
        integration_u = str(integration).strip().upper() or "SHORT"
        aperture_u = str(aperture).strip().upper()

        self.write(f":PAGE:MEAS:MSET:WTIM {wait_s:.6E}")
        if aperture_u:
            self.write(f":PAGE:MEAS:MSET:ITIM {integration_u} {aperture_u}")
        else:
            self.write(f":PAGE:MEAS:MSET:ITIM {integration_u}")

        self.log(
            f"Applied fast sweep profile: WTIM={wait_s:.6E}, ITIM={integration_u}{(' ' + aperture_u) if aperture_u else ''}",
            level="info",
        )

    def set_measurement_range_mode(
        self,
        channel: str,
        mode: str,
        *,
        verify: bool = True,
    ) -> bool:
        ch, kind, _idx = self._parse_channel_key(channel)
        if kind not in {"SMU", "VMU"}:
            raise ValueError("Measurement range mode is only supported for SMU and VMU channels.")

        token = str(mode or "AUTO").strip().upper()
        alias_map = {
            "AUTO": ("AUTO", "AUTO"),
            "FIX": ("FIXed", "FIX"),
            "FIXED": ("FIXed", "FIX"),
            "LIMITED": ("LIMited", "LIM"),
            "LIM": ("LIMited", "LIM"),
        }
        resolved = alias_map.get(token)
        if resolved is None:
            raise ValueError("Measurement range mode must be AUTO, FIXed, or LIMited.")

        scpi_mode, expected = resolved
        cmd = f":PAGE:MEAS:MSET:{ch}:RANG:MODE {scpi_mode}"
        qry = f":PAGE:MEAS:MSET:{ch}:RANG:MODE?"

        if not verify:
            self.write(cmd)
            return True
        return self.write_and_verify(cmd, qry, expected)

    def set_measurement_range(
        self,
        channel: str,
        range_value: float,
        *,
        verify: bool = True,
    ) -> bool:
        ch, kind, _idx = self._parse_channel_key(channel)
        if kind not in {"SMU", "VMU"}:
            raise ValueError("Measurement range is only supported for SMU and VMU channels.")

        value = float(range_value)
        cmd = f":PAGE:MEAS:MSET:{ch}:RANG {value:.6E}"
        qry = f":PAGE:MEAS:MSET:{ch}:RANG?"

        if not verify:
            self.write(cmd)
            return True
        return self.write_and_verify(cmd, qry, f"{value:.6E}")

    def set_sweep_variable(
        self,
        var: int,
        *,
        start: Optional[float] = None,
        stop: Optional[float] = None,
        step: Optional[float] = None,
        compliance: Optional[float] = None,
        points: Optional[int] = None,
    ) -> bool:
        """
        Update VAR sweep parameters with write-and-verify semantics.
        """
        if var not in (1, 2):
            raise ValueError(f"Unsupported sweep variable VAR{var}. Only VAR1/VAR2 are supported.")

        all_ok = True

        # 4155/4156 VAR2 uses POIN rather than STOP in documented sweep command set.
        resolved_points = points
        if var == 2 and stop is not None and resolved_points is None and start is not None and step not in (None, 0.0):
            calc = int(round((float(stop) - float(start)) / float(step))) + 1
            resolved_points = max(calc, 1)

        float_params: List[Tuple[str, Optional[float]]] = [("STAR", start), ("STEP", step), ("COMP", compliance)]
        if var == 1:
            float_params.insert(1, ("STOP", stop))

        for key, value in float_params:
            if value is None:
                continue
            val = float(value)
            cmd = f":PAGE:MEAS:VAR{var}:{key} {val:.6E}"
            qry = f":PAGE:MEAS:VAR{var}:{key}?"
            ok = self.write_and_verify(cmd, qry, f"{val:.6E}")
            all_ok = all_ok and ok
            if not ok:
                self.log(f"Failed to set VAR{var}:{key} to {val:.6E}", level="warning")

        if resolved_points is not None:
            if var != 2:
                raise ValueError("POIN is only valid for VAR2.")
            p = int(resolved_points)
            cmd = f":PAGE:MEAS:VAR2:POINTS {p}"
            qry = ":PAGE:MEAS:VAR2:POINTS?"
            ok = self.write_and_verify(cmd, qry, str(p))
            all_ok = all_ok and ok
            if not ok:
                self.log(f"Failed to set VAR2:POINTS to {p}", level="warning")

        return all_ok

    def get_sweep_variable(self, var: int, fields: Optional[Iterable[str]] = None) -> Dict[str, Optional[str]]:
        """
        Read back selected VAR parameters as normalized text.
        """
        if var not in (1, 2):
            raise ValueError(f"Unsupported sweep variable VAR{var}. Only VAR1/VAR2 are supported.")

        wanted = list(fields) if fields is not None else (["STAR", "STOP", "STEP", "COMP"] if var == 1 else ["STAR", "STEP", "POINTS", "COMP"])
        out: Dict[str, Optional[str]] = {}
        for key in wanted:
            k = str(key).strip().upper()
            if not k:
                continue
            if k == "POIN":
                k = "POINTS"
            out[k] = self._query_text(f":PAGE:MEAS:VAR{var}:{k}?")
        return out

    def disable_channel(self, channel: str) -> str:
        """
        Disable one channel (SMU/VMU/VSU) with :PAGE:CHAN:<CH>:DIS.
        Returns the normalized channel key sent to the instrument.
        """
        ch, _kind, _idx = self._parse_channel_key(channel)
        cmd = self._build_disable_channel_command(ch)
        if self.debug_mode:
            self.log(f"SEND: {cmd}", level="debug")
        self.write(cmd)
        self._clear_vars_for_channel(ch)
        self.log(f"{ch} disabled", level="info")
        return ch

    def disable_channels(self, channels: Iterable[str]) -> List[str]:
        """
        Disable multiple channels, deduplicated in input order.
        Returns normalized channel keys that were sent.
        """
        normalized: List[str] = []
        seen: set[str] = set()
        for channel in channels:
            ch, _kind, _idx = self._parse_channel_key(channel)
            if ch in seen:
                continue
            seen.add(ch)
            normalized.append(ch)

        for ch in normalized:
            cmd = self._build_disable_channel_command(ch)
            if self.debug_mode:
                self.log(f"SEND: {cmd}", level="debug")
            self.write(cmd)
            self._clear_vars_for_channel(ch)

        if normalized:
            self.log(f"Disabled channel(s): {', '.join(normalized)}", level="info")
        return normalized

    def delete_channel(self, channel: str) -> str:
        self._warn_deprecated("delete_channel", "disable_channel")
        return self.disable_channel(channel)

    def delete_channels(self, channels: Iterable[str]) -> List[str]:
        self._warn_deprecated("delete_channels", "disable_channels")
        return self.disable_channels(channels)

    # ==================================================================================
    # Measurement execution APIs
    # ==================================================================================

    @staticmethod
    def _coerce_sampling_execution_queue(
        execution_queue: Optional[Union[SamplingTask, Iterable[SamplingTask]]],
    ) -> List[SamplingTask]:
        if execution_queue is None:
            return []

        if callable(execution_queue):
            return [execution_queue]

        out: List[SamplingTask] = []
        for idx, task in enumerate(execution_queue, start=1):
            if not callable(task):
                raise TypeError(
                    f"execution_queue item {idx} must be callable, got {type(task).__name__}"
                )
            out.append(task)
        return out

    def sweep_single(self, slot: Optional[int] = None) -> bool:
        """
        Execute a single sweep using the current sweep configuration.
        """
        self.log("Starting single sweep execution", level="info")
        if slot is not None:
            self.load_scenario(slot, "MES")
        self.write(":PAGE:SCON:SING")
        ok = self.wait_for_completion()
        self.log(
            "Single sweep completed successfully" if ok else "Single sweep failed",
            level="info" if ok else "warning",
        )
        return ok

    def sweep_append(self, slot: Optional[int] = None) -> bool:
        """
        Append a sweep to the existing sweep data.
        """
        self.log("Starting sweep append execution", level="info")
        if slot is not None:
            self.load_scenario(slot, "MES")
        self.write(":PAGE:SCON:APP")
        ok = self.wait_for_completion()
        self.log(
            "Sweep append completed successfully" if ok else "Sweep append failed",
            level="info" if ok else "warning",
        )
        return ok

    def sampling_single(self, *, on_started: Optional[Callable[[], None]] = None) -> bool:
        """
        Execute a sampling measurement using the current sampling configuration.
        Progress is estimated from sampling interval and points.
        """
        self.log("Starting sampling measurement", level="info")

        interval_resp = self.query(":PAGE:MEAS:SAMP:IINT?")
        points_resp = self.query(":PAGE:MEAS:SAMP:POIN?")

        try:
            interval = float(interval_resp) if interval_resp else 0.0
            points = int(float(points_resp)) if points_resp else 0
        except ValueError:
            interval, points = 0.0, 0

        duration = max(interval * max(points - 1, 0), 0.0)

        if self.debug_mode:
            self.log(
                f"Sampling execution parameters: interval={interval}, points={points}, duration≈{duration}s",
                level="debug",
            )

        progress = tqdm(
            total=duration,
            unit="s",
            desc="Sampling measurement progress",
            disable=(duration <= 0),
        )

        self.write(":PAGE:SCON:SING")
        if on_started is not None:
            on_started()

        start = time.time()
        while duration > 0:
            elapsed = time.time() - start
            progress.n = min(elapsed, duration)
            progress.refresh()
            if elapsed >= duration:
                break
            time.sleep(0.5)

        progress.close()
        ok = self.wait_for_completion()

        self.log(
            "Sampling measurement completed successfully" if ok else "Sampling measurement failed",
            level="info" if ok else "warning",
        )
        return ok

    def qscv_single(self) -> bool:
        """
        Execute a single quasi-static C-V (QSCV) measurement using the current
        QSCV configuration.

        Uses the common single-measurement trigger ``:PAGE:SCON:SING`` (the
        4156 has no QSCV-specific trigger; the state-control subsystem applies
        to whichever measurement mode is active), then waits for completion.
        """
        self.log("Starting single QSCV measurement", level="info")
        self.write(":PAGE:SCON:SING")
        ok = self.wait_for_completion()
        self.log(
            "QSCV measurement completed successfully" if ok else "QSCV measurement failed",
            level="info" if ok else "warning",
        )
        return ok

    def sampling_single_multitask(
        self,
        execution_queue: Optional[Union[SamplingTask, Iterable[SamplingTask]]] = None,
        *,
        on_started: Optional[Callable[[], None]] = None,
        progress_poll_s: float = 0.5,
    ) -> bool:
        """
        Execute a sampling measurement while a sequential background task queue runs.

        This method is intended for side work that can overlap with analyzer-owned sampling
        time, such as pulse-generator triggering, external waits, scope acquisition, or
        bookkeeping on other instruments. Queued tasks must not access this analyzer object
        while sampling is active.
        """
        tasks = self._coerce_sampling_execution_queue(execution_queue)
        if progress_poll_s <= 0:
            raise ValueError("progress_poll_s must be > 0")

        self.log(
            f"Starting multitask sampling measurement (queue tasks={len(tasks)})",
            level="info",
        )

        interval_resp = self.query(":PAGE:MEAS:SAMP:IINT?")
        points_resp = self.query(":PAGE:MEAS:SAMP:POIN?")

        try:
            interval = float(interval_resp) if interval_resp else 0.0
            points = int(float(points_resp)) if points_resp else 0
        except ValueError:
            interval, points = 0.0, 0

        duration = max(interval * max(points - 1, 0), 0.0)

        if self.debug_mode:
            self.log(
                f"Sampling multitask parameters: interval={interval}, points={points}, duration≈{duration}s",
                level="debug",
            )

        progress = tqdm(
            total=duration,
            unit="s",
            desc="Sampling measurement progress",
            disable=(duration <= 0),
        )

        def _run_execution_queue() -> None:
            for idx, task in enumerate(tasks, start=1):
                self.log(
                    f"Sampling multitask queue: task {idx}/{len(tasks)} started",
                    level="info",
                )
                task()
                self.log(
                    f"Sampling multitask queue: task {idx}/{len(tasks)} completed",
                    level="info",
                )

        executor: Optional[ThreadPoolExecutor] = None
        queue_future = None
        queue_exc: Optional[BaseException] = None

        self.write(":PAGE:SCON:SING")
        if on_started is not None:
            on_started()

        try:
            if tasks:
                executor = ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix="pa4155_sampling_multitask",
                )
                queue_future = executor.submit(_run_execution_queue)

            start = time.time()
            while True:
                elapsed = time.time() - start

                if duration > 0:
                    progress.n = min(elapsed, duration)
                    progress.refresh()

                if queue_future is None:
                    if elapsed >= duration:
                        break
                elif queue_future.done():
                    break

                time.sleep(progress_poll_s)

            if duration > 0:
                progress.n = min(time.time() - start, duration)
                progress.refresh()
        finally:
            progress.close()

        if queue_future is not None:
            try:
                queue_future.result()
            except BaseException as exc:
                queue_exc = exc
                self.log(
                    f"Sampling multitask queue failed: {exc}",
                    level="error",
                )
            finally:
                executor.shutdown(wait=True)

        ok = self.wait_for_completion()

        if queue_exc is not None:
            raise RuntimeError("Sampling multitask queue failed during analyzer sampling") from queue_exc

        self.log(
            "Multitask sampling measurement completed successfully" if ok else "Multitask sampling measurement failed",
            level="info" if ok else "warning",
        )
        return ok

    def stress_single(
        self,
        *,
        duration_s: float = 0.0,
        slot: Optional[int] = None,
        show_progress: bool = True,
    ) -> bool:
        """
        Execute stress using previously configured stress channels.
        """
        self.log(
            f"Starting stress execution (duration={duration_s}s)",
            level="info",
        )

        if slot is not None:
            self.load_scenario(slot, "STR")

        if duration_s <= 0:
            self.log("Stress duration <= 0; skipping execution", level="warning")
            return True

        self.write_and_verify(
            f":PAGE:STR:SET:DUR {duration_s:.6E}",
            f":PAGE:STR:SET:DUR?",
            f"{duration_s:.6E}",
        )

        # 4155 stress execution is started with STR:STAR.
        self.write(":PAGE:SCON:STR:STAR")

        if show_progress:
            progress = tqdm(
                total=duration_s,
                unit="s",
                desc="Stress phase progress",
            )
            start = time.time()
            while True:
                elapsed = time.time() - start
                progress.n = min(elapsed, duration_s)
                progress.refresh()
                if elapsed >= duration_s:
                    break
                time.sleep(0.5)
            progress.close()

        ok = self.wait_for_completion()

        self.log(
            "Stress execution completed successfully" if ok else "Stress execution failed",
            level="info" if ok else "warning",
        )
        return ok

    def start_sampling_measurement(self, *, on_started: Optional[Callable[[], None]] = None) -> bool:
        return self.sampling_single(on_started=on_started)

    # ==================================================================================
    # Data retrieval
    # ==================================================================================

    def retrieve_data(self, var_name_list: List[str]) -> List[List[float]]:
        """
        Retrieve trace arrays for each variable in var_name_list.
        Returns list-of-lists aligned with input ordering.
        """
        if not var_name_list:
            return []

        traces: List[Optional[List[float]]] = []
        target_len = 0

        for raw_name in var_name_list:
            var_name = str(raw_name).strip()
            values: Optional[List[float]] = None
            used_name: Optional[str] = None

            candidates = self._build_trace_query_candidates(var_name)
            for candidate in candidates:
                resp = self.query(f":TRAC? '{candidate}'")
                if resp is None:
                    continue

                resp = resp.strip()
                if not resp:
                    continue

                try:
                    values = [float(val_str) for val_str in resp.split(",")]
                except ValueError:
                    continue

                used_name = candidate
                break

            if values is None:
                self.log(f"No trace data found for variable '{var_name}'", level="warning")
                traces.append(None)
                continue

            if used_name is not None and used_name != var_name:
                self.log(
                    f"Trace alias used for '{var_name}' -> '{used_name}'",
                    level="warning",
                )

            traces.append(values)
            if len(values) > target_len:
                target_len = len(values)

        if target_len <= 0:
            return [[] for _ in var_name_list]

        out: List[List[float]] = []
        for idx, values in enumerate(traces):
            if values is None:
                out.append([float("nan")] * target_len)
                continue

            if len(values) < target_len:
                padded = list(values) + [float("nan")] * (target_len - len(values))
                out.append(padded)
                self.log(
                    f"Trace length mismatch for '{var_name_list[idx]}': "
                    f"{len(values)} vs {target_len}; padding with NaN.",
                    level="warning",
                )
                continue

            if len(values) > target_len:
                trimmed = list(values[:target_len])
                out.append(trimmed)
                self.log(
                    f"Trace length mismatch for '{var_name_list[idx]}': "
                    f"{len(values)} vs {target_len}; truncating.",
                    level="warning",
                )
                continue

            out.append(values)

        return out

    @staticmethod
    def _build_trace_query_candidates(var_name: str) -> List[str]:
        base = str(var_name).strip()
        if not base:
            return []

        out: List[str] = []
        seen: set[str] = set()

        def _add(name: str) -> None:
            text = str(name).strip()
            if not text:
                return
            key = text.upper()
            if key in seen:
                return
            seen.add(key)
            out.append(text)

        is_time_label = base.upper() in {"TIME", "@TIME"}
        if is_time_label:
            # Use instrument-native time trace label first.
            _add("@TIME")
            _add("TIME")
            return out

        _add(base)
        _add(base.upper())

        trim_source = base
        if base.startswith("@"):
            trim_source = base[1:]
            _add(trim_source)
            _add(trim_source.upper())

        # Some firmware revisions may normalize/trim display variable names.
        for n in (8, 6):
            if len(trim_source) > n:
                _add(trim_source[:n])
                _add(trim_source[:n].upper())

        return out

    # ==================================================================================
    # Context manager
    # ==================================================================================

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()

    # ==================================================================================
    # Private command builders
    # ==================================================================================

    def _build_sampling_timing_commands(
        self,
        timing: Union[SamplingTiming, Dict[str, Any]],
    ) -> List[str]:
        t = timing if isinstance(timing, SamplingTiming) else SamplingTiming(**timing)

        points = max(min(t.points, 10_001), 1)
        interval = min(max(t.interval_s, 60e-6), 65.535)

        if self.debug_mode:
            self.log(
                f"Sampling timing resolved: interval={interval}, points={points}, "
                f"integration={t.integration}, mode={t.mode}",
                level="debug",
            )

        if points != t.points or interval != t.interval_s:
            self.log(
                "Sampling timing clamped to instrument limits",
                level="warning",
            )

        return [
            ":PAGE:CHAN:MODE SAMP",
            f":PAGE:MEAS:MSET:ITIM {t.integration}",
            f":PAGE:MEAS:SAMP:IINT {interval:.6E}",
            f":PAGE:MEAS:SAMP:POIN {points}",
            f":PAGE:MEAS:SAMP:MODE {t.mode}",
        ]

    def _build_sampling_channel_commands(
        self,
        channel_cfg: Mapping[str, Union[SamplingChannelConfig, Dict[str, Any]]],
    ) -> List[str]:
        cmds: List[str] = []

        for key, raw in channel_cfg.items():
            ch, kind, _ = self._parse_channel_key(key)
            cfg = self._coerce_sampling_cfg(raw)
            if self._is_off_mode(cfg.mode) or self._is_off_mode(cfg.func):
                cmds.append(self._build_disable_channel_command(ch))
                self._clear_vars_for_channel(ch)
                continue

            if self.debug_mode:
                self.log(f"Sampling channel resolved: {ch} → {cfg}", level="debug")

            cmds.append(f":PAGE:CHAN:{ch}:MODE {cfg.mode}")

            if cfg.func:
                if self._supports_func(kind):
                    cmds.append(f":PAGE:CHAN:{ch}:FUNC {cfg.func}")
                else:
                    self._warn_ignored(ch, "FUNC")

            if cfg.names:
                cmds.append(f":PAGE:CHAN:{ch}:VNAM '{cfg.names.vname}'")
                if cfg.names.iname:
                    if self._supports_iname(kind):
                        cmds.append(f":PAGE:CHAN:{ch}:INAM '{cfg.names.iname}'")
                    else:
                        self._warn_ignored(ch, "INAM")

            if cfg.const:
                if self._supports_const(kind):
                    cmds.append(f":PAGE:MEAS:SAMP:CONS:{ch} {cfg.const.value:.6E}")
                    cmds.append(f":PAGE:MEAS:SAMP:CONS:{ch}:COMP {cfg.const.compliance:.6E}")
                else:
                    self._warn_ignored(ch, "CONST")

        return cmds

    def _build_sweep_commands(
        self,
        channel_cfg: Mapping[str, Union[SweepChannelConfig, Dict[str, Any]]],
        integration: str,
    ) -> List[str]:

        cmds: List[str] = [
            ":PAGE:CHAN:MODE SWE",
            f":PAGE:MEAS:MSET:ITIM {integration}",
        ]

        if self.debug_mode:
            self.log(f"Sweep integration set to {integration}", level="debug")
        for key, raw in channel_cfg.items():
            ch, kind, _ = self._parse_channel_key(key)
            cfg = self._coerce_sweep_cfg(raw)
            if self._is_off_mode(cfg.mode) or self._is_off_mode(cfg.func):
                cmds.append(self._build_disable_channel_command(ch))
                self._clear_vars_for_channel(ch)
                continue

            if self.debug_mode:
                self.log(f"Sweep channel resolved: {ch} → {cfg}", level="debug")

            cmds.append(f":PAGE:CHAN:{ch}:MODE {cfg.mode}")

            var_tag: Optional[str] = None
            if cfg.sweep:
                if not self._supports_sweep(kind):
                    self._warn_ignored(ch, "SWEEP")
                else:
                    self._register_var(cfg.sweep.var, ch)
                    var_tag = f"VAR{cfg.sweep.var}"
                    if self.debug_mode:
                        self.log(f"{ch} assigned to {var_tag}", level="debug")
                    cmds.append(f":PAGE:CHAN:{ch}:FUNC {var_tag}")
                    cmds.extend(self._build_var_sweep_commands(cfg.sweep))

            if cfg.func:
                if self._supports_func(kind):
                    if var_tag is None or cfg.func != var_tag:
                        cmds.append(f":PAGE:CHAN:{ch}:FUNC {cfg.func}")
                else:
                    self._warn_ignored(ch, "FUNC")

            if cfg.names:
                cmds.append(f":PAGE:CHAN:{ch}:VNAM '{cfg.names.vname}'")
                if cfg.names.iname:
                    if self._supports_iname(kind):
                        cmds.append(f":PAGE:CHAN:{ch}:INAM '{cfg.names.iname}'")
                    else:
                        self._warn_ignored(ch, "INAM")

            if cfg.const:
                if self._supports_const(kind):
                    cmds.append(f":PAGE:MEAS:CONS:{ch} {cfg.const.value:.6E}")
                    cmds.append(f":PAGE:MEAS:CONS:{ch}:COMP {cfg.const.compliance:.6E}")
                else:
                    self._warn_ignored(ch, "CONST")

        return cmds

    def _build_sampling_commands(
        self,
        timing: Union[SamplingTiming, Dict[str, Any]],
        channel_cfg: Mapping[str, Union[SamplingChannelConfig, Dict[str, Any]]],
    ) -> List[str]:
        cmds = self._build_sampling_timing_commands(timing)
        cmds.extend(self._build_sampling_channel_commands(channel_cfg))
        return cmds

    def _build_stress_commands(
        self,
        channel_cfg: Mapping[str, Union[StressChannelConfig, Dict[str, Any]]],
    ) -> List[str]:

        cmds: List[str] = []

        for key, raw in channel_cfg.items():
            ch, kind, _ = self._parse_channel_key(key)
            cfg = self._coerce_stress_cfg(raw)

            if self.debug_mode:
                self.log(f"Stress channel resolved: {ch} → {cfg}", level="debug")

            if kind == "VMU":
                self._warn_ignored(ch, "STRESS")
                continue

            cmds.extend([
                f":PAGE:STR:{ch}:MODE {cfg.mode}",
                f":PAGE:STR:{ch}:FUNC {cfg.func}",
                f":PAGE:STR:SET:CONS:{ch} {cfg.const.value:.6E}",
                f":PAGE:STR:SET:CONS:{ch}:COMP {cfg.const.compliance:.6E}",
            ])

        return cmds

    # ==================================================================================
    # VAR tracking
    # ==================================================================================

    def _register_var(self, var: int, ch: str) -> None:
        if var in self._active_vars and self._active_vars[var] != ch:
            raise RuntimeError(
                f"VAR{var} already assigned to {self._active_vars[var]}, cannot reassign to {ch}"
            )
        self._active_vars[var] = ch

    def _clear_vars_for_channel(self, ch: str) -> None:
        active = [var for var, owner in self._active_vars.items() if owner == ch]
        for var in active:
            self._active_vars.pop(var, None)
        if active and self.debug_mode:
            self.log(f"Cleared VAR map for {ch}: {active}", level="debug")

    # ==================================================================================
    # Utilities
    # ==================================================================================

    def _supports_func(self, kind: ChannelKind) -> bool:
        return kind != "VMU"

    def _supports_iname(self, kind: ChannelKind) -> bool:
        return kind == "SMU"

    def _supports_const(self, kind: ChannelKind) -> bool:
        return kind != "VMU"

    def _supports_sweep(self, kind: ChannelKind) -> bool:
        return kind == "SMU"

    @staticmethod
    def _is_constant_function(func: str) -> bool:
        return str(func).strip().upper() in {"CONS", "CONST", "CONSTANT"}

    @staticmethod
    def _is_common_channel_mode(mode: str) -> bool:
        return str(mode).strip().upper() in {"COMM", "COMMON", "COM"}

    @staticmethod
    def _normalize_query_text(raw: Any) -> Optional[str]:
        if raw is None:
            return None
        text = str(raw).strip()
        if not text:
            return None
        return text.strip("\"'")

    def _query_text(self, command: str) -> Optional[str]:
        return self._normalize_query_text(self.query(command))

    def _is_off_mode(self, mode: Any) -> bool:
        return str(mode).strip().upper() in {"OFF", "DIS", "DISABLE", "DISABLED"}

    def _build_disable_channel_command(self, ch: str) -> str:
        return f":PAGE:CHAN:{ch}:DIS"

    def _warn_ignored(self, ch: str, feature: str) -> None:
        self.log(f"{ch} does not support {feature}; ignoring", level="warning")

    def _parse_channel_key(self, key: str) -> Tuple[str, ChannelKind, int]:
        k = key.upper()
        if k.startswith("SMU"):
            return k, "SMU", int(k[3:])
        if k.startswith("VMU"):
            return k, "VMU", int(k[3:])
        if k.startswith("VSU"):
            return k, "VSU", int(k[3:])
        raise ValueError(f"Invalid channel key: {key}")

    def _coerce_sweep_cfg(self, obj: Any) -> SweepChannelConfig:
        return obj if isinstance(obj, SweepChannelConfig) else SweepChannelConfig(
            mode=obj["mode"],
            func=obj.get("func"),
            names=Names(**obj["names"]) if "names" in obj else None,
            const=ConstSpec(**obj["const"]) if "const" in obj else None,
            sweep=SweepSpec(**obj["sweep"]) if "sweep" in obj else None,
        )

    def _coerce_sampling_cfg(self, obj: Any) -> SamplingChannelConfig:
        return obj if isinstance(obj, SamplingChannelConfig) else SamplingChannelConfig(
            mode=obj["mode"],
            func=obj.get("func"),
            names=Names(**obj["names"]) if "names" in obj else None,
            const=ConstSpec(**obj["const"]) if "const" in obj else None,
        )

    def _coerce_stress_cfg(self, obj: Any) -> StressChannelConfig:
        return obj if isinstance(obj, StressChannelConfig) else StressChannelConfig(
            mode=obj["mode"],
            func=obj["func"],
            const=ConstSpec(**obj["const"]),
        )

    def _collect_var_assignments(
        self,
        channel_cfg: Mapping[str, Union[SweepChannelConfig, Dict[str, Any]]],
    ) -> Dict[int, str]:
        assignments: Dict[int, str] = {}
        for key, raw in channel_cfg.items():
            ch, kind, _ = self._parse_channel_key(key)
            cfg = self._coerce_sweep_cfg(raw)
            if not cfg.sweep or not self._supports_sweep(kind):
                continue
            var = cfg.sweep.var
            existing = assignments.get(var)
            if existing and existing != ch:
                raise ValueError(f"VAR{var} assigned to multiple channels: {existing}, {ch}")
            assignments[var] = ch
        return assignments

    def _build_var_clear_commands(self, var_assignments: Mapping[int, str]) -> List[str]:
        cmds: List[str] = []
        for var, new_ch in var_assignments.items():
            prev = self._active_vars.get(var)
            if prev and prev != new_ch:
                cmds.append(f":PAGE:CHAN:{prev}:FUNC CONS")
        return cmds

    def _build_var_sweep_commands(self, sweep: SweepSpec) -> List[str]:
        sweep_mode = self._normalize_sweep_mode(sweep.mode)
        cmds = [
            f":PAGE:MEAS:VAR{sweep.var}:STAR {sweep.start:.6E}",
            f":PAGE:MEAS:VAR{sweep.var}:STEP {sweep.step:.6E}",
            f":PAGE:MEAS:VAR{sweep.var}:COMP {sweep.compliance:.6E}",
        ]

        if sweep.var == 1:
            if sweep.stop is None:
                raise ValueError("VAR1 sweep requires 'stop'")
            cmds.append(f":PAGE:MEAS:VAR1:MODE {'DOUB' if sweep_mode == 'DOUBle' else 'SING'}")
            cmds.append(f":PAGE:MEAS:VAR1:STOP {sweep.stop:.6E}")
        else:
            if sweep.points is not None:
                cmds.append(f":PAGE:MEAS:VAR2:POINTS {sweep.points}")
            elif sweep.stop is not None:
                if sweep.step == 0:
                    raise ValueError("VAR2 sweep cannot derive points when step is 0.")
                points = int(round((sweep.stop - sweep.start) / sweep.step)) + 1
                cmds.append(f":PAGE:MEAS:VAR2:POINTS {max(points, 1)}")
            else:
                raise ValueError("VAR2 sweep requires 'points' or 'stop'")

        return cmds

    @staticmethod
    def _normalize_sweep_mode(mode: Any) -> Literal["SINGle", "DOUBle"]:
        token = str(mode or "SINGle").strip().upper()
        if token in {"SING", "SINGLE"}:
            return "SINGle"
        if token in {"DOUB", "DOUBLE"}:
            return "DOUBle"
        raise ValueError("Sweep mode must be SINGle or DOUBle")

    def _sanitize_display_names(self, names: List[str]) -> List[str]:
        clean: List[str] = []
        seen = set()
        for n in names:
            label = str(n).strip()
            if not label or label in seen:
                continue
            seen.add(label)
            clean.append(label)
        return clean

    def _limit_trace_headers(self, names: List[str], *, context: str) -> List[str]:
        limit = int(getattr(self, "max_trace_headers", 0) or 0)
        if limit <= 0 or len(names) <= limit:
            return names

        kept = list(names[:limit])
        dropped = list(names[limit:])
        self.log(
            f"{context}: requested {len(names)} headers, limited to first {limit}. "
            f"Dropped: {dropped}",
            level="warning",
        )
        return kept

    def _build_display_list_command(self, names: List[str]) -> str:
        escaped = [f"'{name.replace(chr(39), chr(39) * 2)}'" for name in names]
        return ":PAGE:DISP:LIST " + ", ".join(escaped)

    def _collect_display_names_from_sweep_cfg(
        self,
        channel_cfg: Mapping[str, Union[SweepChannelConfig, Dict[str, Any]]],
    ) -> List[str]:
        # Collect vnames before inames so that when _limit_trace_headers truncates
        # to 8, VMU voltage channels (e.g. VOUT) are kept and SMU current names
        # are dropped instead.  The HP 4155 TRAC buffer is allocated based on the
        # display list at first set_sweep() time, so VOUT must be present here.
        vnames: List[str] = []
        inames: List[str] = []
        for key, raw in channel_cfg.items():
            _ch, kind, _idx = self._parse_channel_key(key)
            cfg = self._coerce_sweep_cfg(raw)
            if not cfg.names:
                continue
            if cfg.names.vname:
                vnames.append(cfg.names.vname)
            if cfg.names.iname and self._supports_iname(kind):
                inames.append(cfg.names.iname)
        return self._sanitize_display_names(vnames + inames)

    def _collect_display_names_from_sampling_cfg(
        self,
        channel_cfg: Mapping[str, Union[SamplingChannelConfig, Dict[str, Any]]],
    ) -> List[str]:
        vnames: List[str] = []
        inames: List[str] = []
        for key, raw in channel_cfg.items():
            _ch, kind, _idx = self._parse_channel_key(key)
            cfg = self._coerce_sampling_cfg(raw)
            if not cfg.names:
                continue
            if cfg.names.vname:
                vnames.append(cfg.names.vname)
            if cfg.names.iname and self._supports_iname(kind):
                inames.append(cfg.names.iname)
        return self._sanitize_display_names(vnames + inames)
