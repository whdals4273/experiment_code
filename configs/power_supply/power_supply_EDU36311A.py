from __future__ import annotations
__version__ = "0.0.0"

from typing import Iterable, Dict, Tuple, Optional
import time

import pyvisa as visa

from src.configs.power_supply.base_power_supply import BasePowerSupply

__all__ = ["PowerSupplyEDU36311A"]


class PowerSupplyEDU36311A(BasePowerSupply):
    """
    SCPI driver for the Keysight EDU36311A triple-output DC power supply.

    This instrument uses channel-list–based SCPI commands (e.g. "(@1)"),
    so we emulate a selected-channel model in software.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def __init__(
        self,
        rm: visa.ResourceManager,
        resource: str,
        *,
        timeout: int = 1_000,
        delay_time: Optional[float] = None,
        debug_mode: bool = False,
    ) -> None:
        super().__init__(
            resource_manager=rm,
            resource_name=resource,
            timeout=timeout,
            delay_time=delay_time,
            debug_mode=debug_mode,
        )

        # Cache currently selected channel (software-side only)
        self._current_channel: Optional[int] = None
        self._settle_delay: float = 1.0  # seconds

        if self.debug_mode:
            self.log("EDU36311A driver initialized", level="debug")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _require_channel(self) -> int:
        if self._current_channel is None:
            raise RuntimeError(
                "No channel selected. Call select_channel(chan) first."
            )
        return self._current_channel

    def _query_float(self, commands: Iterable[str], *, label: str) -> float:
        last_error: Exception | None = None
        for command in commands:
            resp = self.query(command)
            if resp is None:
                continue
            try:
                return float(str(resp).strip())
            except (TypeError, ValueError) as exc:
                last_error = exc
                self.log(
                    f"Ignoring non-numeric {label} response to {command!r}: {resp!r}",
                    level="warning",
                )

        detail = f": {last_error}" if last_error is not None else ""
        raise RuntimeError(f"Failed to read {label}{detail}")

    def connect(self) -> None:
        super().connect()
        if self.device is None:
            return

        for attr, value in (
            ("write_termination", "\n"),
            ("read_termination", "\n"),
            ("query_delay", 0.05),
            ("timeout", max(int(self.timeout), 5000)),
        ):
            try:
                setattr(self.device, attr, value)
            except Exception as exc:
                if self.debug_mode:
                    self.log(
                        f"Could not set VISA {attr}={value!r}: {exc}",
                        level="debug",
                    )
        self.timeout = max(int(self.timeout), 5000)

    def query(self, command: str) -> Optional[str]:
        if self.device is None:
            self.log("Query attempted while device not connected", level="error")
            return None

        try:
            response = self.device.query(command)
            if self.debug_mode:
                self.log(f"QUERY -> {command}", level="debug")
                self.log(f"RESPONSE -> {response.strip()}", level="debug")
            return response
        except Exception as exc:
            self.log(f"Query failed -> {command}: {exc}", level="error")
            return None

    # ------------------------------------------------------------------
    # Low-level SCPI interface
    # ------------------------------------------------------------------
    def select_channel(self, chan: int) -> None:
        """
        Select output channel in software.

        EDU commands below include explicit channel lists, for example
        "VOLT 1.2, (@1)" and "MEAS:VOLT? (@1)".
        """
        if chan not in (1, 2, 3):
            raise ValueError("Channel must be 1, 2, or 3 for EDU36311A.")

        self._current_channel = chan

        if self.debug_mode:
            self.log(f"Selected channel CH{chan}", level="debug")

    def set_voltage(self, volts: float) -> None:
        ch = self._require_channel()
        v_str = self.format_scpi_number(volts)
        self.write(f"VOLT {v_str}, (@{ch})")

        if self.debug_mode:
            self.log(f"CH{ch} → Set voltage {v_str} V", level="debug")

    def set_current(self, amps: float) -> None:
        ch = self._require_channel()
        i_str = self.format_scpi_number(amps)
        self.write(f"CURR {i_str}, (@{ch})")

        if self.debug_mode:
            self.log(f"CH{ch} → Set current limit {i_str} A", level="debug")

    def read_setpoints(self) -> Tuple[float, float]:
        ch = self._require_channel()

        v_read = self._query_float(
            (f"VOLT? (@{ch})", f"SOUR:VOLT? (@{ch})", f"VOLT? CH{ch}", "VOLT?"),
            label=f"voltage setpoint on CH{ch}",
        )
        i_read = self._query_float(
            (f"CURR? (@{ch})", f"SOUR:CURR? (@{ch})", f"CURR? CH{ch}", "CURR?"),
            label=f"current setpoint on CH{ch}",
        )
        return v_read, i_read

    def read_measpoints(self) -> Tuple[float, float]:
        ch = self._require_channel()

        v_read = self._query_float(
            (
                f"MEAS:VOLT? (@{ch})",
                f"MEAS:VOLT? CH{ch}",
                f"MEAS:SCAL:VOLT:DC? (@{ch})",
                "MEAS:VOLT?",
            ),
            label=f"measured voltage on CH{ch}",
        )
        i_read = self._query_float(
            (
                f"MEAS:CURR? (@{ch})",
                f"MEAS:CURR? CH{ch}",
                f"MEAS:SCAL:CURR:DC? (@{ch})",
                "MEAS:CURR?",
            ),
            label=f"measured current on CH{ch}",
        )
        return v_read, i_read

    def output_enable(self, enable: bool = True) -> None:
        ch = self._require_channel()
        state = "ON" if enable else "OFF"
        self.write(f"OUTP {state}, (@{ch})")

        self.log(
            f"CH{ch} → Output {'ENABLED' if enable else 'DISABLED'}",
            level="info",
        )

    # ------------------------------------------------------------------
    # High-level helpers
    # ------------------------------------------------------------------
    def enable_outputs(
        self,
        channels: Optional[Iterable[int]] = None,
    ) -> None:
        for ch in channels or (1, 2, 3):
            self.select_channel(ch)
            self.output_enable(True)
        time.sleep(self._settle_delay)
        self.log("All selected channels enabled", level="info")

    def disable_outputs(
        self,
        channels: Optional[Iterable[int]] = None,
    ) -> None:
        for ch in channels or (1, 2, 3):
            self.select_channel(ch)
            self.output_enable(False)
        time.sleep(self._settle_delay)
        self.log("All selected channels disabled", level="info")

    def zero_voltages(
        self,
        channels: Optional[Iterable[int]] = None,
    ) -> None:
        for ch in channels or (1, 2, 3):
            self.select_channel(ch)
            self.set_voltage(0.0)
        time.sleep(self._settle_delay)
        self.log("All selected channels set to 0 V", level="info")

    def set_voltages(
        self,
        voltages: Dict[int, float],
        *,
        verify: bool = True,
    ) -> None:
        for ch, volts in voltages.items():
            self.select_channel(ch)
            self.set_voltage(volts)
            time.sleep(self._settle_delay)

            if verify:
                self._verify_channel(ch, volts, None)

        self.log(f"Voltages programmed: {voltages}", level="info")

    def set_compliance_currents(
        self,
        currents: Dict[int, float],
        *,
        verify: bool = True,
    ) -> None:
        for ch, amps in currents.items():
            self.select_channel(ch)
            self.set_current(amps)
            time.sleep(self._settle_delay)

            if verify:
                self._verify_channel(ch, None, amps)

        self.log(f"Compliance currents programmed: {currents}", level="info")
