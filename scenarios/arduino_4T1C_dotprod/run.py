from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import matplotlib
matplotlib.use("Agg")  # Result PNGs are saved by the GUI's worker, without Tk calls.
import matplotlib.pyplot as plt
import pyvisa
import serial
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.configs.power_supply.power_supply_2230G import PowerSupply2230G
from src.configs.power_supply.power_supply_EDU36311A import PowerSupplyEDU36311A
from scenarios.arduino_4T1C_dotprod.adc_verify import (
    validate_adc_verify, evaluate_adc_rows, summarize_adc_verification,
)
from scenarios.arduino_4T1C_dotprod.pulse_preview import MAX_READ_SAMPLES, build_preview
from scenarios.arduino_4T1C_dotprod.update_bias import UPDATE_COMMANDS, resolve_update_voltages, uses_vcap_input
from scenarios.arduino_4T1C_dotprod.lookup_table import (
    LOOKUP_COMMAND, lookup_settings, point_config, summarize_point,
    lookup_matrix, plot_lookup_table,
)

SCENARIO_DIR    = Path(__file__).resolve().parent
DEFAULT_CONFIG  = SCENARIO_DIR / "exp_config.yml"
DEFAULT_RESULT  = SCENARIO_DIR / "results"

DRIVER_MAP = {
    "2230G":     PowerSupply2230G,
    "EDU36311A": PowerSupplyEDU36311A,
}

REQ_PSU_SET_PREFIX = "REQ PSU_SET"
OPC_PSU_SET_TOKEN  = "OPC PSU_SET"
ABORT_CMD          = "CMD ABORT"
OK_PREFIX          = "OK "
ERR_PREFIX         = "ERR "

_UPDATE_DEFAULTS: dict[str, Any] = {
    "settle_after_opc_us": 50,
    "repeat": 1,
    "opc_timeout_ms": 5000,
    "update_gate_settle_us": 50,
}
_READ_DEFAULTS: dict[str, Any] = {
    "samples": 1,
    "warmup": 0,
    "settle_us": 250,
    "inter_us": 50,
    "avg": 1,
    "read_delay": 0,
    "write_bl_settle_after_opc_us": 50,
    "write_bl_opc_timeout_ms": 5000,
    "write_bl_gate_settle_us": 50,
}

# Logical channel label → (supply_key, channel_key)
# All four bitline MUX inputs are independent PSU channels. Firmware sets
# their phase voltages via REQ/OPC. Compound read loops prepare fixed read
# rails once and restore all four to 0 V when the loop finishes.
# All four bitline inputs are normal-wired: UPDATE writes BLTOP - BLBTM.
# BLBTM_OFF supplies positive read bias through the LOW bottom MUX selection
# (pin 34); the unused BLBTM ON rail remains zero during reads/parking.
_LABEL_KEYS = [
    ("BLTOP", "bltop_supply", "bltop_channel"),
    ("BLBTM", "blbtm_supply", "blbtm_channel"),
    ("BLTOP_OFF", "bltop_off_supply", "bltop_off_channel"),
    ("BLBTM_OFF", "blbtm_off_supply", "blbtm_off_channel"),
    ("VDD",   "vdd_supply",   "vdd_channel"),
    ("VREAD", "vread_supply", "vread_channel"),
]

_BITLINE_LABELS = ("BLTOP", "BLBTM", "BLTOP_OFF", "BLBTM_OFF")
_MEASUREMENT_COMMANDS = {
    "UPDATE", "READ_ACTUAL", "READ_REFERENCE", "UPDATE_AND_READ",
    "RETENTION_TEST", "READ_DISTURB_TEST", "DEBUG_FIRE", "ADC_VERIFY",
}


def validate_config(cfg: dict[str, Any], command_name: str | None = None) -> dict[str, tuple[str, int]]:
    """Validate the four-rail wiring contract before any hardware is enabled."""
    name = str(command_name or cfg.get("command_name", "READ_ACTUAL")).strip().upper()
    if name == LOOKUP_COMMAND:
        settings = lookup_settings(cfg)
        for q in settings.queries:
            for cap in settings.caps:
                child = point_config(cfg, cap, q, settings.cycles)
                mapping = validate_config(child)
                preview = build_preview(child)
                if preview.errors:
                    raise ValueError(preview.errors[0])
                if len(build_command(child).encode("ascii")) >= 192:
                    raise ValueError("LOOK_UP_TABLE_TEST: a generated command exceeds the firmware's 191-byte limit.")
        return mapping
    if name in ("READ_ACTUAL", "READ_REFERENCE"):
        samples = float(cfg.get("samples", 1))
        if not math.isfinite(samples) or not samples.is_integer() or not 1 <= samples <= MAX_READ_SAMPLES:
            raise ValueError(f"{name}: samples must be an integer from 1 to {MAX_READ_SAMPLES}.")
    measurement = name in _MEASUREMENT_COMMANDS
    if measurement and not cfg.get("enable_psu_control", True):
        raise ValueError(f"{name} requires enable_psu_control=true for four-bitline REQ/OPC control.")

    mapping: dict[str, tuple[str, int]] = {}
    if cfg.get("enable_psu_control", True):
        for label, supply_key, channel_key in _LABEL_KEYS:
            supply, channel = cfg.get(supply_key, 0), cfg.get(channel_key, 0)
            if not supply and not channel:
                continue
            if not supply or not channel:
                raise ValueError(f"{label}: set both {supply_key} and {channel_key}.")
            if int(supply) != supply or int(supply) < 1 or int(channel) != channel or int(channel) not in (1, 2, 3):
                raise ValueError(f"{label}: supply must be a positive integer and PSU channel must be 1, 2, or 3.")
            resource = str(cfg.get(f"psu_resource_{int(supply)}", "")).strip()
            if not resource:
                raise ValueError(f"psu_resource_{int(supply)} is empty — check {label} mapping.")
            mapping[label] = (resource, int(channel))

    if not measurement:
        return mapping

    missing = [label for label in _BITLINE_LABELS if label not in mapping]
    if missing:
        raise ValueError(
            f"{name} requires four independently mapped BL channels; missing: {', '.join(missing)}. "
            "Fill in their *_supply/*_channel fields using the actual PSU wiring."
        )
    for label in _BITLINE_LABELS:
        aliases = [other for other, target in mapping.items() if other != label and target == mapping[label]]
        if aliases:
            raise ValueError(f"{label} must use an independent PSU channel; shared with {', '.join(aliases)}.")
        key = f"{label.lower()}_init_v"
        if float(cfg.get(key, 0.0)) != 0.0:
            raise ValueError(f"{key} must be 0.0; firmware controls phase voltages and the 0 V rest state.")

    voltage_keys = []
    if name in UPDATE_COMMANDS:
        resolve_update_voltages(cfg)
    if name == "ADC_VERIFY":
        validate_adc_verify(cfg)
        vread = mapping.get("VREAD")
        if vread is None:
            raise ValueError("ADC_VERIFY requires a mapped VREAD channel")
        if any(pair == vread for label, pair in mapping.items() if label != "VREAD"):
            raise ValueError("ADC_VERIFY requires an independent VREAD channel")
    if name not in ("UPDATE", "ADC_VERIFY") and (name != "DEBUG_FIRE" or str(cfg.get("debug_target", "S1")) not in ("S1", "S2")):
        voltage_keys.append("write_bl_topbtm_v")
    for key in voltage_keys:
        voltage = float(cfg[key])
        if not math.isfinite(voltage) or voltage < 0:
            raise ValueError(f"{key} must be a finite nonnegative PSU magnitude; wiring determines the cell voltage sign.")
    return mapping


# ---------------------------------------------------------------------------
# Command builder
# ---------------------------------------------------------------------------

_REQUIRED = object()  # sentinel: field has no firmware default, always send it


def _add_kv(parts: list[str], key: str, value: Any, default: Any = _REQUIRED) -> None:
    """Append key=value, unless value matches the firmware's own default.

    The firmware line buffer is 192 bytes (see MAX_LINE_LENGTH in
    unitcell_4T1C_qk_dotproduct.ino); omitting fields that already match the
    firmware default keeps commands well under that limit.
    """
    if default is _REQUIRED or value != default:
        parts.append(f"{key}={value}")


def build_command(cfg: dict[str, Any]) -> str:
    """Build the full CMD … key=value line from a config dict."""
    name = str(cfg.get("command_name", "READ_ACTUAL")).strip().upper()

    if name in ("PING", "CMD ABORT", "ABORT"):
        return name if name != "ABORT" else "CMD ABORT"

    parts = [f"CMD {name}"]

    def add_update() -> None:
        # BLTOP and BLBTM are always both required (not "ch"/"v" choosing one) --
        # see unitcell_4T1C_qk_dotproduct.ino's UpdateParams docs: VCAP must always
        # charge from a fully-specified state, never a leftover voltage on one line.
        top, bottom = resolve_update_voltages(cfg)
        _add_kv(parts, "bltop_v", top)
        _add_kv(parts, "blbtm_v", bottom)
        _add_kv(parts, "pw_us", int(cfg["pw_us"]))
        _add_kv(parts, "settle_after_opc_us", int(cfg.get("settle_after_opc_us", _UPDATE_DEFAULTS["settle_after_opc_us"])), _UPDATE_DEFAULTS["settle_after_opc_us"])
        _add_kv(parts, "repeat", int(cfg.get("repeat", _UPDATE_DEFAULTS["repeat"])), _UPDATE_DEFAULTS["repeat"])
        _add_kv(parts, "opc_timeout_ms", int(cfg.get("opc_timeout_ms", _UPDATE_DEFAULTS["opc_timeout_ms"])), _UPDATE_DEFAULTS["opc_timeout_ms"])
        _add_kv(parts, "update_gate_settle_us", int(cfg.get("update_gate_settle_us", _UPDATE_DEFAULTS["update_gate_settle_us"])), _UPDATE_DEFAULTS["update_gate_settle_us"])

    def add_read() -> None:
        _add_kv(parts, "q", float(cfg["q"]))
        _add_kv(parts, "precision", int(cfg["precision"]))
        _add_kv(parts, "step_us", int(cfg["step_us"]))
        # Both reads use normally wired ON supplies (pin 46 or 34 HIGH).
        # The OFF supplies remain zero while S1/S2 are closed or bias is prepared.
        _add_kv(parts, "write_bl_topbtm_v", float(cfg["write_bl_topbtm_v"]))
        _add_kv(parts, "samples", int(cfg.get("samples", _READ_DEFAULTS["samples"])), _READ_DEFAULTS["samples"])
        _add_kv(parts, "warmup", int(cfg.get("warmup", _READ_DEFAULTS["warmup"])), _READ_DEFAULTS["warmup"])
        _add_kv(parts, "settle_us", int(cfg.get("settle_us", _READ_DEFAULTS["settle_us"])), _READ_DEFAULTS["settle_us"])
        _add_kv(parts, "inter_us", int(cfg.get("inter_us", _READ_DEFAULTS["inter_us"])), _READ_DEFAULTS["inter_us"])
        _add_kv(parts, "avg", int(cfg.get("avg", _READ_DEFAULTS["avg"])), _READ_DEFAULTS["avg"])
        _add_kv(parts, "read_delay", int(cfg.get("read_delay", _READ_DEFAULTS["read_delay"])), _READ_DEFAULTS["read_delay"])
        _add_kv(parts, "write_bl_settle_after_opc_us", int(cfg.get("write_bl_settle_after_opc_us", _READ_DEFAULTS["write_bl_settle_after_opc_us"])), _READ_DEFAULTS["write_bl_settle_after_opc_us"])
        _add_kv(parts, "write_bl_opc_timeout_ms", int(cfg.get("write_bl_opc_timeout_ms", _READ_DEFAULTS["write_bl_opc_timeout_ms"])), _READ_DEFAULTS["write_bl_opc_timeout_ms"])
        _add_kv(parts, "write_bl_gate_settle_us", int(cfg.get("write_bl_gate_settle_us", _READ_DEFAULTS["write_bl_gate_settle_us"])), _READ_DEFAULTS["write_bl_gate_settle_us"])

    if name == "UPDATE":
        add_update()

    elif name == "ADC_VERIFY":
        settings = validate_adc_verify(cfg)
        for wire_key, config_key in (("channel", "adc_channel"), ("pulse_us", "adc_pulse_us"),
                                     ("repeats", "adc_repeats"), ("samples", "adc_samples"),
                                     ("inter_us", "adc_inter_us"), ("settle_us", "adc_settle_us")):
            parts.append(f"{wire_key}={settings[config_key]}")

    elif name in ("READ_ACTUAL", "READ_REFERENCE"):
        add_read()

    elif name == "UPDATE_AND_READ":
        add_update()
        _add_kv(parts, "post_us", int(cfg.get("post_us", 0)), 0)
        add_read()

    elif name == "RETENTION_TEST":
        add_update()
        _add_kv(parts, "points", int(cfg.get("points", 10)), 10)
        _add_kv(parts, "initial_wait_ms", int(cfg.get("initial_wait_ms", 0)), 0)
        _add_kv(parts, "interval_ms", int(cfg.get("interval_ms", 1000)), 1000)
        add_read()

    elif name == "READ_DISTURB_TEST":
        add_update()
        _add_kv(parts, "cycles", int(cfg.get("cycles", 10)), 10)
        _add_kv(parts, "initial_wait_ms", int(cfg.get("initial_wait_ms", 0)), 0)
        _add_kv(parts, "interval_ms", int(cfg.get("interval_ms", 1000)), 1000)
        add_read()

    elif name == "DEBUG_FIRE":
        target = str(cfg.get("debug_target", "S1"))
        _add_kv(parts, "target", target)
        if target in ("S1", "S2"):
            _add_kv(parts, "pw_us", int(cfg["pw_us"]))
        else:
            add_read()

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------

class Experiment4T1C:
    """Keep serial open for a session; prepare and release PSUs per command."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.arduino: serial.Serial | None = None
        self.rm: pyvisa.ResourceManager | None = None
        self.supplies: dict[str, Any] = {}              # resource_str → psu object
        self.channel_map: dict[str, tuple[str, int]] = {}  # label → (resource_str, ch_num)
        self.command_failed = False
        self._force_safe_shutdown = False
        self.output_lines: list[str] = []
        self.command_sent_at: str | None = None
        self.cleanup_failed = False
        self.cancel_requested = None  # Optional worker-owned cooperative stop predicate.
        self.result: dict[str, Any] | None = None  # Latest command's saved artifacts only.

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    def _check_cancelled(self) -> None:
        if self.cancel_requested is None or not self.cancel_requested():
            return
        self.command_failed = True
        self._force_safe_shutdown = True
        if self.command_sent_at is not None and self.arduino is not None and self.arduino.is_open:
            # Do not wait indefinitely for ACK from a silent/disconnected board.
            # The host still zeroes/disables mapped PSU outputs in cleanup.
            try:
                self.arduino.write((ABORT_CMD + "\n").encode())
                self.arduino.flush()
                print("TX -> CMD ABORT (stop requested; acknowledgement not awaited)")
            except Exception as exc:
                print(f"[WARN] Could not send ABORT: {exc}")
        raise KeyboardInterrupt("Experiment stop requested")

    def connect(self) -> None:
        """Open/reuse serial, then prepare PSUs using this command's config."""
        cfg = self.cfg
        mapping = validate_config(cfg)
        self._check_cancelled()
        # Until a command completes successfully, even safe_shutdown=False
        # must not preserve outputs after partial initialization or failure.
        self._force_safe_shutdown = True
        port = str(cfg["arduino_port"])
        baud = int(cfg.get("arduino_baud", 115200))
        timeout = float(cfg.get("serial_timeout_s", 0.5))

        if self.arduino is None:
            self.arduino = serial.Serial(
                port=port,
                baudrate=baud,
                timeout=timeout,
                dsrdtr=False,
                rtscts=False,
            )
            print(f"Serial: opened {port} @ {baud}")
            time.sleep(float(cfg.get("arduino_boot_wait_s", 2.0)))
            self._check_cancelled()
            self.arduino.reset_input_buffer()
        else:
            if not self.arduino.is_open:
                raise RuntimeError("Serial connection closed unexpectedly; restart the runner.")
            if self.arduino.port != port or self.arduino.baudrate != baud:
                raise ValueError(
                    "Arduino port/baud changed during the session; restart the runner "
                    "to use the new connection settings."
                )
            # Avoid reconfiguring an unchanged port (including DTR) between runs.
            if self.arduino.timeout != timeout:
                self.arduino.timeout = timeout
            print(f"Serial: reusing {port} @ {baud}")

        self._check_cancelled()
        if not cfg.get("enable_psu_control", True):
            return

        driver_name = str(cfg.get("psu_driver", "2230G")).strip().upper()
        if driver_name not in DRIVER_MAP:
            raise ValueError(f"Unsupported psu_driver: {driver_name!r}. Valid: {list(DRIVER_MAP)}")
        driver_cls = DRIVER_MAP[driver_name]

        visa_backend = str(cfg.get("visa_backend", "")).strip()
        self.rm = (
            pyvisa.ResourceManager(visa_backend) if visa_backend
            else pyvisa.ResourceManager()
        )

        for label, (resource_str, ch_num) in mapping.items():
            self._check_cancelled()
            if resource_str not in self.supplies:
                psu = driver_cls(
                    self.rm, resource_str,
                    timeout=int(cfg.get("psu_timeout_ms", 1000)),
                    debug_mode=bool(cfg.get("debug", False)),
                )
                self.supplies[resource_str] = psu
                psu.connect()

            self.channel_map[label] = (resource_str, int(ch_num))
            self._check_cancelled()

        command_name = str(cfg.get("command_name", "READ_ACTUAL")).strip().upper()
        update_only = command_name == "UPDATE"
        adc_settings = validate_adc_verify(cfg) if command_name == "ADC_VERIFY" else None
        disabled_vread = self.channel_map.get("VREAD") if update_only else None
        if disabled_vread is not None:
            shared_labels = [
                label for label, target in self.channel_map.items()
                if label != "VREAD" and target == disabled_vread
            ]
            if shared_labels:
                raise ValueError(
                    "UPDATE requires a separate VREAD PSU channel so it can stay OFF; "
                    f"the mapped channel is also used by {', '.join(shared_labels)}."
                )
            resource_str, ch = disabled_vread
            # Also turn off a VREAD output left on by an earlier command with
            # safe_shutdown=False, before enabling any update supplies.
            self.supplies[resource_str].disable_outputs(channels=[ch])
            self._check_cancelled()
            print("PSU init: VREAD output OFF for UPDATE")

        for resource_str, psu in self.supplies.items():
            self._check_cancelled()
            channels = sorted(ch for rs, ch in self.channel_map.values() if rs == resource_str)
            psu.zero_voltages(channels=channels)
            self._check_cancelled()
            enabled_channels = [ch for ch in channels if (resource_str, ch) != disabled_vread]
            # The PSU drivers interpret [] as all channels, not none.
            if enabled_channels:
                psu.enable_outputs(channels=enabled_channels)
                self._check_cancelled()

        for label, init_key in [
            ("BLTOP", "bltop_init_v"),
            ("BLBTM", "blbtm_init_v"),
            ("BLTOP_OFF", "bltop_off_init_v"),
            ("BLBTM_OFF", "blbtm_off_init_v"),
            ("VDD",   "vdd_init_v"),
            ("VREAD", "vread_init_v"),
        ]:
            if label == "VREAD" and update_only:
                continue
            v = float(cfg.get(init_key, 0.0))
            if label == "VREAD" and adc_settings is not None:
                v = adc_settings["adc_vread_v"]
            if v != 0.0 and label in self.channel_map:
                self._set_psu_channel(label, v)
                print(f"PSU init: {label} = {v:.3f} V")

    def close(self) -> None:
        """Release remaining resources when the runner session exits."""
        try:
            self.close_supplies()
        finally:
            if self.arduino is not None:
                try:
                    if self.arduino.is_open:
                        self.arduino.close()
                        print("Serial: closed")
                finally:
                    self.arduino = None

    def close_supplies(self, *, force_safe: bool = False) -> None:
        """Release PSUs; failure/abort always zeroes and disables mapped outputs."""
        errors: list[Exception] = []

        def cleanup(action: Any, **kwargs: Any) -> None:
            try:
                action(**kwargs)
            except Exception as exc:
                errors.append(exc)
                print(f"[WARN] PSU cleanup failed: {exc}")

        safe = force_safe or self._force_safe_shutdown or bool(self.cfg.get("safe_shutdown", True))
        for resource_str, psu in list(self.supplies.items()):
            channels = sorted(ch for rs, ch in self.channel_map.values() if rs == resource_str)
            # A failed connect may precede channel mapping. These drivers treat
            # an empty channel list as "all channels", so never pass it here.
            if safe and channels:
                cleanup(psu.zero_voltages, channels=channels)
                cleanup(psu.disable_outputs, channels=channels)
            cleanup(psu.disconnect)
        self.supplies.clear()
        self.channel_map.clear()

        if self.rm is not None:
            cleanup(self.rm.close)
            self.rm = None

        if errors:
            self.cleanup_failed = True
            raise RuntimeError("PSU cleanup failed; ending the runner session.") from errors[0]

    # ------------------------------------------------------------------
    # PSU helpers
    # ------------------------------------------------------------------

    def _set_psu_channel(self, label: str, voltage: float) -> None:
        self._check_cancelled()
        resource_str, ch = self.channel_map[label]
        self.supplies[resource_str].set_voltages({ch: voltage}, verify=False)
        time.sleep(float(self.cfg.get("psu_settle_s", 0.2)))
        self._check_cancelled()

    def _handle_req_psu_set(self, text: str) -> None:
        assert self.arduino is not None

        channel: str | None = None
        voltage: float | None = None
        for token in text.split():
            if token.startswith("ch="):
                channel = token[3:]
            elif token.startswith("v="):
                try:
                    voltage = float(token[2:])
                except ValueError:
                    pass

        def _abort(reason: str) -> None:
            self.command_failed = True
            self._force_safe_shutdown = True
            print(f"[ABORT] {reason}")
            self.arduino.write((ABORT_CMD + "\n").encode())  # type: ignore[union-attr]
            self.arduino.flush()                             # type: ignore[union-attr]

        if channel is None or voltage is None:
            _abort(f"PSU request parse failed: {text!r}")
            return

        if channel not in self.channel_map:
            _abort(f"Unknown channel label: {channel!r}. Mapped: {list(self.channel_map)}")
            return

        try:
            self._set_psu_channel(channel, voltage)
        except Exception as exc:
            _abort(f"PSU programming failed: {exc}")
            return

        opc = f"{OPC_PSU_SET_TOKEN} ch={channel} v={voltage:.6f}"
        self._check_cancelled()
        self.arduino.write((opc + "\n").encode())
        self.arduino.flush()
        print(f"PC -> {opc}")

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    def run(self, command: str) -> list[str]:
        """Send one command and collect all output lines until OK / ERR."""
        assert self.arduino is not None
        # Retain the failure policy if validation or sending/receiving raises.
        self.command_failed = True
        self._force_safe_shutdown = True
        self.output_lines = []
        self.command_sent_at = None
        tokens = command.split()
        name = tokens[1] if tokens[0] == "CMD" else tokens[0]
        validate_config(self.cfg, name)

        self._check_cancelled()
        self.arduino.reset_input_buffer()
        self._check_cancelled()
        self.command_sent_at = datetime.now().astimezone().isoformat(timespec="microseconds")
        self.arduino.write((command + "\n").encode())
        self.arduino.flush()
        print(f"TX -> {command}")

        psu_enabled = bool(self.cfg.get("enable_psu_control", True))
        lines = self.output_lines

        while True:
            self._check_cancelled()
            raw = self.arduino.readline()
            self._check_cancelled()
            if not raw:
                continue

            text = raw.decode("utf-8", errors="ignore").strip()
            if not text:
                continue

            print(f"ARDUINO <- {text}")
            lines.append(text)

            if text.startswith(OK_PREFIX) or text == "OK":
                aborted = text.startswith("OK ABORT")
                self.command_failed = aborted and name != "ABORT"
                self._force_safe_shutdown = aborted
                break
            if text.startswith(ERR_PREFIX) or text == "ERR":
                print("[WARN] Firmware returned an error — check output above.")
                break
            if psu_enabled and text.startswith(REQ_PSU_SET_PREFIX):
                self._handle_req_psu_set(text)

        if name == "ADC_VERIFY":
            records = evaluate_adc_rows(_parse_data_lines(lines), self.cfg)
            summary = summarize_adc_verification(records, self.cfg, lines[-1])
            if not summary["complete"]:
                self.command_failed = True
                self._force_safe_shutdown = True
                print("[WARN] ADC_VERIFY did not return all requested repeats and ADC_VERIFY_DONE.")
        return lines


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------

def _parse_data_lines(lines: list[str]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for line in lines:
        if not line.startswith("DATA "):
            continue
        tokens = line.split()
        row: dict[str, str] = {"_type": tokens[1] if len(tokens) > 1 else ""}
        for tok in tokens[2:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                row[k] = v
        if row["_type"] in ("RETENTION", "DISTURB"):
            # These are firmware measurement times. Host receive time cannot
            # establish read age because transmission and PSU work may lag.
            row.update(elapsed_s="", actual_elapsed_s="", reference_elapsed_s="",
                       observed_interval_s="", lateness_s="")
            normalized = {"timing_schema": "unavailable"}
            try:
                if "t_us" in row:
                    normalized["timing_schema"] = "firmware_read_start_us"
                    normalized["elapsed_s"] = normalized["actual_elapsed_s"] = f"{int(row['t_us']) / 1_000_000:.6f}"
                    if "ref_us" in row:
                        normalized["reference_elapsed_s"] = f"{int(row['ref_us']) / 1_000_000:.6f}"
                    if "dt_us" in row:
                        normalized["observed_interval_s"] = f"{int(row['dt_us']) / 1_000_000:.6f}"
                    if "late_us" in row:
                        normalized["lateness_s"] = f"{int(row['late_us']) / 1_000_000:.6f}"
                elif "t_ms" in row:
                    normalized["timing_schema"] = "legacy_completion_ms"
                    normalized["elapsed_s"] = f"{int(row['t_ms']) / 1000:.6f}"
            except (ValueError, OverflowError):
                # A disconnected serial line may end halfway through a field.
                # Keep its raw fields and all prior rows; do not invent age.
                normalized = {"timing_schema": "invalid"}
            row.update(normalized)
        records.append(row)
    return records


def _save_csv(records: list[dict[str, str]], path: Path) -> None:
    if not records:
        return
    fieldnames = list(dict.fromkeys(k for r in records for k in r))
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
    print(f"CSV saved: {path}")


def _save_raw_log(lines: list[str], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Log saved: {path}")


def _read_sample_values(row: dict[str, Any]) -> list[int] | None:
    """Decode real firmware samples; legacy summary rows have no raw values."""
    if "values" not in row:
        return None
    values = [int(value) for value in row["values"].split(",")]
    if (not 1 <= len(values) <= MAX_READ_SAMPLES or len(values) != int(row["samples"])
            or any(value < 0 or value > 1023 for value in values)):
        raise ValueError("ADC sample count/range mismatch")
    return values


def _add_read_context(records: list[dict[str, Any]], cfg: dict[str, Any], name: str) -> list[str]:
    """Attach the submitted settings, never the GUI's later edited values."""
    read_records = [row for row in records if row.get("_type") == name]
    if not read_records:
        return []
    try:
        timing = build_preview({"command_name": name, **{
            key: cfg[key] for key in ("q", "precision", "step_us") if key in cfg
        }})
    except ValueError as exc:
        for row in read_records:
            row["sample_values_status"] = "invalid"
        return [f"Invalid read settings: {exc}"]
    errors = []
    for row in read_records:
        row.update(q=timing.q, precision=timing.precision, step_us=timing.step_us,
                   expected_read_us=timing.pulse_us, read_frame_us=timing.frame_us)
        try:
            values = _read_sample_values(row)
            if values is not None and len(values) != int(cfg.get("samples", 1)):
                raise ValueError("ADC samples do not match the requested count")
            row["sample_values_status"] = "available" if values is not None else "unavailable"
        except (KeyError, TypeError, ValueError) as exc:
            row["sample_values_status"] = "invalid"
            errors.append(str(exc))
    return errors


def _annotate_plot_values(ax: Any, x: Any, values: Any, *,
                          offset: tuple[int, int] = (0, 8), color: str = "#334155",
                          fixed_adc_range: bool = True) -> None:
    """Show the plotted count, retaining signed differences and fractional means."""
    for position, value in zip(x, values):
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        dx, dy = offset
        # Keep labels inside the fixed ADC range at the upper/lower endpoints.
        if fixed_adc_range and value >= 1000 and dy > 0:
            dy = -13
        elif fixed_adc_range and value == 0 and dy < 0:
            dy = 8
        ax.annotate(text, (position, value), xytext=(dx, dy),
                    textcoords="offset points", ha="center",
                    va="bottom" if dy >= 0 else "top", fontsize=9, color=color,
                    bbox=dict(facecolor="white", edgecolor="none", alpha=0.8, pad=1))


def _plot_read_samples(ax: Any, records: list[dict[str, Any]], cmd_name: str) -> None:
    """One pulse, several ADC acquisitions of its held integrator output."""
    max_samples = 1
    legacy = False
    for index, row in enumerate(records):
        values = _read_sample_values(row)
        suffix = f" (read {index + 1})" if len(records) > 1 else ""
        if values is None:
            legacy = True
            average = float(row.get("avg", row.get("adc", 0)))
            ax.scatter([1], [average], label="Reported average" + suffix)
            _annotate_plot_values(ax, [1], [average])
            continue
        indices = list(range(1, len(values) + 1))
        max_samples = max(max_samples, len(values))
        ax.plot(indices, values, marker="o", label="ADC samples" + suffix)
        mean = sum(values) / len(values)
        ax.axhline(mean, linestyle="--", color="#c47a24", label=f"Mean = {mean:.3f}" + suffix)
        _annotate_plot_values(ax, indices, values)
    row = records[0]
    title = cmd_name.replace("_", " ")
    if "expected_read_us" in row:
        def duration(us: Any) -> str:
            return f"{float(us) / 1000:g} ms" if float(us) >= 1000 else f"{float(us):g} us"
        title += (f"\nq = {row['q']:g}  |  precision = {row['precision']}  |  step_us = {row['step_us']} us"
                  f"\nExpected VREAD ON: {duration(row['expected_read_us'])}"
                  f"  |  Full read frame: {duration(row['read_frame_us'])}")
    ax.set_title(title, fontsize=11, pad=12)
    ax.set_xlabel("ADC sample index (one read pulse)")
    ax.set_ylabel("ADC (counts)")
    ax.set_xlim(0.5, max_samples + 0.5)
    if max_samples <= 32:
        ax.set_xticks(list(range(1, max_samples + 1)))
    ax.grid(axis="y", alpha=0.25)
    ax.legend(loc="best")
    if legacy:
        ax.text(0.5, 0.04, "Individual samples unavailable: upload firmware 2.4.3 or newer.",
                transform=ax.transAxes, ha="center", fontsize=9, color="#a34a14")


def _plot_and_save(records: list[dict[str, str]], cmd_name: str, path: Path, *,
                   requested_cycles: int | None = None) -> Path | None:
    records = [r for r in records if r.get("timing_schema") != "invalid"]
    standalone_read = cmd_name in ("READ_ACTUAL", "READ_REFERENCE")
    if standalone_read:
        records = [r for r in records if r.get("_type") == cmd_name
                   and r.get("sample_values_status") != "invalid"]
    if cmd_name == "READ_DISTURB_TEST":
        # A disconnected line may end inside idx=. Keep it in raw/CSV, but
        # do not let the progress display or plot replace valid prior data.
        records = [r for r in records if str(r.get("idx", "")).isdecimal()]
    if not records:
        return

    fig, ax = plt.subplots(figsize=(8, 5) if standalone_read else (8, 4))

    if cmd_name == "ADC_VERIFY":
        records = [r for r in records if r.get("_type") == "ADC_VERIFY"
                   and r.get("verification_status") != "INVALID_DATA"]
        if not records:
            plt.close(fig)
            return
        idx = [float(r["repeat"]) for r in records]
        expected = [float(r["expected_delta_adc"]) for r in records]
        measured = [float(r["measured_delta_adc"]) for r in records]
        ax.plot(idx, expected, marker="o", label="Expected delta")
        ax.plot(idx, measured, marker="s", label="Measured delta")
        _annotate_plot_values(ax, idx, expected, offset=(-9, -13), color="#1f77b4")
        _annotate_plot_values(ax, idx, measured, offset=(9, 8), color="#ff7f0e")
        ax.set_xlabel("Repeat")
        ax.set_ylabel("ADC - baseline (counts)")
        ax.set_title(f"ADC verify: channel {records[0]['channel']}")
        ax.legend()

    elif cmd_name == "RETENTION_TEST":
        if all(r.get("actual_elapsed_s") for r in records):
            t = [float(r["actual_elapsed_s"]) for r in records]
            xlabel = "Read age since last update pulse (s)"
        elif all(r.get("timing_schema") == "legacy_completion_ms" for r in records):
            t = [float(r["elapsed_s"]) for r in records]
            xlabel = "Legacy cycle completion elapsed time (s)"
        else:
            t = [int(r.get("idx", i)) for i, r in enumerate(records)]
            xlabel = "Point index (measurement timing unavailable)"
        adc = [float(r.get("avg", r.get("adc", 0))) for r in records]
        ax.plot(t, adc, marker="o")
        _annotate_plot_values(ax, t, adc, color="#1f77b4")
        ax.set_xlabel(xlabel)
        ax.set_ylabel("ADC avg")
        ax.set_title("Retention")

    elif cmd_name == "READ_DISTURB_TEST":
        idx = [int(r.get("idx", i)) for i, r in enumerate(records)]
        if all(r.get("actual_elapsed_s") for r in records):
            # A cycle is one ACTUAL/REFERENCE pair. Place all three values at
            # that pair's ACTUAL start age, keeping corresponding labels aligned.
            x = [float(r["actual_elapsed_s"]) for r in records]
            xlabel = "Read age since last update pulse (s)"
        elif all(r.get("timing_schema") == "legacy_completion_ms" for r in records):
            x = [float(r["elapsed_s"]) for r in records]
            xlabel = "Legacy cycle completion elapsed time (s)"
        else:
            x = idx
            xlabel = "Cycle index (measurement timing unavailable)"
        act = [float(r.get("actual_avg",    r.get("actual",    0))) for r in records]
        ref = [float(r.get("reference_avg", r.get("reference", 0))) for r in records]
        dlt = [float(r.get("delta", a - b)) for r, a, b in zip(records, act, ref)]
        ax.plot(x, act, marker="o", label="Actual")
        ax.plot(x, ref, marker="s", label="Reference")
        ax.plot(x, dlt, marker="^", linestyle="--", label="Delta")
        # Disturb uses a signed, automatic range. Keep the three label rows
        # separate even when ACTUAL == REFERENCE == 1023 (or all are zero).
        _annotate_plot_values(ax, x, act, offset=(0, 8), color="#1f77b4", fixed_adc_range=False)
        _annotate_plot_values(ax, x, ref, offset=(0, -13), color="#ff7f0e", fixed_adc_range=False)
        _annotate_plot_values(ax, x, dlt, offset=(0, -31), color="#2ca02c", fixed_adc_range=False)
        received = len(set(idx))
        total = requested_cycles if requested_cycles is not None else received
        if xlabel.startswith("Cycle index"):
            extent = max(total, max(idx) + 1)
            stride = max(1, (extent + 9) // 10)
            ticks = list(range(0, extent, stride))
            if ticks[-1] != extent - 1:
                ticks.append(extent - 1)
            ax.set_xlim(-0.5, extent - 0.5)
            ax.set_xticks(ticks)
        ax.margins(y=0.25)
        ax.grid(axis="x", alpha=0.15)
        ax.set_xlabel(xlabel)
        ax.set_ylabel("ADC")
        ax.set_title(f"Read Disturb ({received}/{total} cycles received)")
        ax.legend()

    elif standalone_read:
        _plot_read_samples(ax, records, cmd_name)

    elif cmd_name == "UPDATE_AND_READ":
        adc = [float(r.get("avg", r.get("adc", 0))) for r in records]
        ax.bar(range(len(adc)), adc)
        _annotate_plot_values(ax, range(len(adc)), adc, color="#1f77b4")
        ax.set_ylabel("ADC avg")
        ax.set_title(cmd_name.replace("_", " "))

    else:
        plt.close(fig)
        return

    # Use the full 10-bit reporting range consistently in the GUI and PNGs.
    # Disturb also plots signed actual-reference differences on this axis.
    if cmd_name != "READ_DISTURB_TEST":
        ax.set_ylim(0, 1023)
        ax.set_yticks([0, 256, 512, 768, 1023])
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"Plot saved: {path}")
    return path


def _save_results(lines: list[str], cfg: dict[str, Any], command: str,
                  result_dir: Path, stem: str, started_at: str,
                  command_sent_at: str | None, error: str | None) -> dict[str, Any]:
    """Preserve partial output and execution context, including failed attempts."""
    _save_raw_log(lines, result_dir / f"{stem}_raw.txt")
    with (result_dir / f"{stem}_config.yml").open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    terminal = next((line for line in reversed(lines)
                     if line == "OK" or line.startswith((OK_PREFIX, ERR_PREFIX)) or line == "ERR"), None)
    metadata = {
        "execution_started_at": started_at,
        "command_sent_at": command_sent_at,
        "execution_finished_at": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "command": command,
        "config_snapshot": f"{stem}_config.yml",
        "terminal_response": terminal,
        "error": error,
        "status": ("exception" if error else "firmware_error" if terminal and terminal.startswith("ERR")
                   else "aborted" if terminal and terminal.startswith("OK ABORT")
                   else "completed" if terminal else "incomplete"),
        "timing_definitions": {
            "firmware_read_start_us": "t_us/ref_us: ACTUAL/REFERENCE first DFF slot since the last UPDATE pulse ended; dt_us: successive ACTUAL start interval (first=0); late_us: ACTUAL scheduling lateness.",
            "legacy_completion_ms": "t_ms: cycle completion after read cleanup, relative to UPDATE cleanup completion; not measurement age.",
            "unavailable": "No firmware measurement timestamp. Host timestamps are execution context only.",
            "invalid": "Malformed firmware timing retained in raw fields; normalized timing is blank.",
        },
    }
    records = _parse_data_lines(lines)
    cmd_name = str(cfg.get("command_name", "READ_ACTUAL")).strip().upper()
    if cmd_name in UPDATE_COMMANDS:
        top, bottom = resolve_update_voltages(cfg)
        metadata["update_bias"] = {
            "mode": "vcap" if uses_vcap_input(cfg) else "manual",
            "requested_vcap_v": float(cfg["vcap_v"]) if uses_vcap_input(cfg) else None,
            "bltop_v": top, "blbtm_v": bottom, "ideal_vcap_v": top - bottom,
        }
    if cmd_name in ("READ_ACTUAL", "READ_REFERENCE"):
        sample_errors = _add_read_context(records, cfg, cmd_name)
        if sample_errors:
            metadata["read_sample_errors"] = sample_errors
            if metadata["status"] == "completed":
                metadata["status"] = "incomplete"
            print("[WARN] Invalid ADC sample payload; see raw log and metadata.")
        if any(r.get("sample_values_status") == "unavailable" for r in records):
            print("[WARN] Individual ADC samples unavailable; upload unitcell firmware 2.4.3 or newer.")
    if cmd_name == "ADC_VERIFY":
        records = evaluate_adc_rows(records, cfg)
        summary = summarize_adc_verification(records, cfg, terminal, error)
        metadata["adc_verification"] = summary
        for row in records:
            if row.get("_type") == "ADC_VERIFY":
                print(f"ADC_VERIFY channel={row.get('channel', '?')} repeat={row.get('repeat', '?')} "
                      f"expected_delta={row.get('expected_delta_adc', '?')} "
                      f"measured_delta={row.get('measured_delta_adc', '?')} "
                      f"status={row['verification_status']}")
        print(f"ADC_VERIFY result: {summary['status']} "
              f"({summary['received_repeats']}/{summary['requested_repeats']} repeats)")
        if not summary["complete"] and metadata["status"] == "completed":
            metadata["status"] = "incomplete"
    disturb_cycles = None
    if cmd_name == "READ_DISTURB_TEST":
        received = {int(row["idx"]) for row in records
                    if row.get("_type") == "DISTURB" and row.get("timing_schema") != "invalid"
                    and str(row.get("idx", "")).isdecimal()}
        disturb_cycles = {"received": len(received), "requested": int(cfg.get("cycles", 10))}
        metadata["disturb_cycles"] = disturb_cycles
        print(f"READ_DISTURB_TEST cycles received: {disturb_cycles['received']}/{disturb_cycles['requested']}")
        if received != set(range(disturb_cycles["requested"])) and metadata["status"] == "completed":
            metadata["status"] = "incomplete"
    with (result_dir / f"{stem}_metadata.json").open("w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
        f.write("\n")
    plot_path = None
    if records:
        _save_csv(records, result_dir / f"{stem}_data.csv")
        plot_options = {"requested_cycles": disturb_cycles["requested"]} if disturb_cycles else {}
        plot_path = _plot_and_save(records, cmd_name, result_dir / f"{stem}_plot.png", **plot_options)
    result = {"plot_path": str(Path(plot_path).resolve()) if plot_path is not None else None,
              "status": metadata["status"]}
    if disturb_cycles is not None:
        result["disturb_cycles"] = disturb_cycles
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def execute_command(exp: Experiment4T1C, cfg: dict[str, Any]) -> bool:
    """Execute one snapshot, release PSUs, and preserve complete/partial results.

    The caller owns the serial session. This function never opens a GUI, and
    exceptions retain their original type for the CLI and GUI error reporting.
    """
    if str(cfg.get("command_name", "")).strip().upper() == LOOKUP_COMMAND:
        return _execute_lookup_table(exp, cfg)
    exp.cfg = cfg
    exp.output_lines = []
    exp.command_sent_at = None
    exp.cleanup_failed = False
    exp.result = None
    cmd_name = str(cfg.get("command_name", "READ_ACTUAL")).strip().upper()
    command = build_command(cfg)
    print(f"Command: {command}")
    result_dir = Path(str(cfg.get("result_dir", str(DEFAULT_RESULT)))).expanduser()
    result_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now().astimezone()
    stem = f"{started.strftime('%Y%m%d_%H%M%S_%f%z')}_{cmd_name}"
    error = None
    try:
        try:
            exp.connect()
            exp.run(command)
        except BaseException:
            exp.close_supplies(force_safe=True)
            raise
        finally:
            exp.close_supplies()
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        exp.result = _save_results(exp.output_lines, cfg, command, result_dir, stem,
                                   started.isoformat(timespec="microseconds"), exp.command_sent_at, error)
    if exp.command_failed:
        print("Firmware error — ending the session. See the saved log.")
    return not exp.command_failed


def _execute_lookup_table(exp: Experiment4T1C, cfg: dict[str, Any]) -> bool:
    """Run a fresh disturb experiment for every K/q pair using one serial session."""
    exp.cfg, exp.result, exp.output_lines = cfg, None, []
    exp.command_sent_at = None
    exp.cleanup_failed = False
    validate_config(cfg)  # Validate the entire grid before touching instruments.
    settings = lookup_settings(cfg)
    started = datetime.now().astimezone()
    stem = f"{started.strftime('%Y%m%d_%H%M%S_%f%z')}_{LOOKUP_COMMAND}"
    directory = Path(str(cfg.get("result_dir", str(DEFAULT_RESULT)))).expanduser() / stem
    directory.mkdir(parents=True, exist_ok=True)
    points, records, logs = [], [], []
    status, error, first_sent_at = "incomplete", None, None
    print(f"{LOOKUP_COMMAND}: {len(settings.caps)} K × {len(settings.queries)} q = "
          f"{settings.point_count} combinations, {settings.cycles} pair(s) each")
    try:
        for qi, q in enumerate(settings.queries):
            for ki, cap in enumerate(settings.caps):
                exp.command_sent_at = None
                exp.output_lines = []
                exp._check_cancelled()
                number = len(points) + 1
                label = f"[{LOOKUP_COMMAND} {number}/{settings.point_count}] K={cap:g} V, q={q:g}"
                print(label)
                logs.append(label)
                child = point_config(cfg, cap, q, settings.cycles)
                point_dir = directory / "points" / f"point_{number:04d}"
                child["result_dir"] = str(point_dir)
                command = build_command(child)
                child_ok = False
                try:
                    child_ok = execute_command(exp, child)
                finally:
                    first_sent_at = first_sent_at or exp.command_sent_at
                    logs.append(f"Command: {command}")
                    logs.extend(exp.output_lines)
                    rows = [row for row in _parse_data_lines(exp.output_lines) if row.get("_type") == "DISTURB"]
                    tag = {"k_index": ki, "q_index": qi, "vcap_v": cap, "q": q}
                    records.extend(dict(row, **tag) for row in rows)
                    terminal_ok = any(line.startswith("OK READ_DISTURB_DONE") for line in exp.output_lines)
                    summary = summarize_point(rows, settings.cycles,
                                              child_ok and terminal_ok and (exp.result or {}).get("status") == "completed")
                    preview = build_preview(child)
                    points.append(dict(tag, **summary, pulse_us=preview.pulse_us,
                                       frame_us=preview.frame_us,
                                       artifacts=str(point_dir.relative_to(directory))))
                if summary["status"] != "completed":
                    exp.command_failed = True
                    exp._force_safe_shutdown = True
                    status = (exp.result or {}).get("status", "incomplete")
                    if status == "completed":
                        status = "incomplete"
                    error = f"K={cap:g}, q={q:g}: received {summary['received_cycles']}/{settings.cycles} valid cycles; sweep stopped."
                    print(f"[WARN] {error}")
                    return False
                print(f"{label} → delta={summary['delta_mean']:.3f} count")
        status = "completed"
        return True
    except BaseException as exc:
        status = "aborted" if isinstance(exc, KeyboardInterrupt) else "exception"
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        exp.cfg = cfg
        _save_raw_log(logs, directory / f"{stem}_raw.txt")
        with (directory / f"{stem}_config.yml").open("w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
        if records:
            _save_csv(records, directory / f"{stem}_cycles.csv")
        if points:
            _save_csv(points, directory / f"{stem}_summary.csv")
        table_path = directory / f"{stem}_table.csv"
        with table_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["q / K (VCAP V)"] + list(settings.caps))
            for q, row in zip(settings.queries, lookup_matrix(settings, points)):
                writer.writerow([q] + ["" if value is None else value for value in row])
        complete = sum(point["status"] == "completed" for point in points)
        metadata = {
            "command": LOOKUP_COMMAND, "status": status, "error": error,
            "execution_started_at": started.isoformat(timespec="microseconds"),
            "command_sent_at": first_sent_at,
            "execution_finished_at": datetime.now().astimezone().isoformat(timespec="microseconds"),
            "vcap_values_v": settings.caps, "q_values": settings.queries,
            "cycles_per_combination": settings.cycles,
            "aggregation": "mean of per-cycle (actual_avg - reference_avg); each combination rewrites K",
            "lookup_points": {"received": complete, "requested": settings.point_count},
            "points": points,
        }
        with (directory / f"{stem}_metadata.json").open("w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
            f.write("\n")
        # Publish the aggregate result even if PNG generation fails; never leave
        # the last child experiment masquerading as the sweep result in the GUI.
        exp.result = {"plot_path": None, "status": status, "lookup_points": metadata["lookup_points"]}
        plot_path = plot_lookup_table(plt, settings, points, directory / f"{stem}_plot.png", cfg)
        exp.result["plot_path"] = str(Path(plot_path).resolve()) if plot_path is not None else None
        print(f"Table saved: {table_path}")
        print(f"{LOOKUP_COMMAND}: {complete}/{settings.point_count} combinations completed")


def create_gui(config_path):
    # CLI runs and offline protocol tests do not need a Tk installation.
    from scenarios.arduino_4T1C_dotprod.experiment_gui import ExperimentGUI
    return ExperimentGUI(config_path, validate=validate_config)


def main() -> int:
    parser = argparse.ArgumentParser(description="4T1C QK dot-product experiment runner")
    parser.add_argument("--port",   default="",  help="Override arduino_port in YAML")
    parser.add_argument("--baud",   type=int, default=0, help="Override arduino_baud in YAML")
    parser.add_argument("--yaml",   default=str(DEFAULT_CONFIG), help="Path to experiment YAML config")
    parser.add_argument("--no-gui", action="store_true", help="Skip GUI, use YAML values directly")
    args = parser.parse_args()

    config_path = Path(args.yaml).expanduser()
    if not config_path.exists():
        print(f"Config not found: {config_path}")
        return 1

    if not args.no_gui:
        from scenarios.arduino_4T1C_dotprod.gui_session import ExperimentSession
        # One window and one worker own the entire session. Previewing values
        # still opens no serial or VISA connection.
        session = ExperimentSession(SimpleNamespace(Experiment4T1C=Experiment4T1C,
                                   execute_command=execute_command), port=args.port, baud=args.baud)
        try:
            gui = create_gui(config_path)
            gui.attach_session(session)
            gui.open_window()
        except KeyboardInterrupt:
            session.interrupt()
            print("\nInterrupted — requesting ABORT and waiting for cleanup.")
            return 130
        finally:
            session.close()
            while True:
                try:
                    session.join()
                    break
                except KeyboardInterrupt:
                    session.interrupt()
                    print("Waiting for the worker to finish hardware cleanup.")
        return session.exit_code

    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cfg: dict[str, Any] = {k: v for k, v in raw.items() if not k.startswith("__")}
    if args.port:
        cfg["arduino_port"] = args.port
    if args.baud:
        cfg["arduino_baud"] = args.baud
    exp = Experiment4T1C(cfg)
    try:
        return 0 if execute_command(exp, cfg) else 1
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    finally:
        exp.close()


if __name__ == "__main__":
    raise SystemExit(main())
