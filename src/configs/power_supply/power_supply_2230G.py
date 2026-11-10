from __future__ import annotations
__version__ = "0.0.0"

from typing import Iterable, Dict, Tuple, Optional
import time

import pyvisa as visa

from src.configs.power_supply.base_power_supply import BasePowerSupply


__all__ = ["PowerSupply2230G"]


class PowerSupply2230G(BasePowerSupply):
    """
    SCPI driver for the Keithley 2230G triple-output power supply.
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

        # Cache currently selected channel to avoid redundant INST:SEL
        self._current_channel: Optional[int] = None
        self._settle_delay: float = 0.5  # seconds

    # ------------------------------------------------------------------
    # Low-level SCPI interface
    # ------------------------------------------------------------------
    def select_channel(self, chan: int) -> None:
        """
        Select output channel if it differs from the current one.

        Redundant selections are silently ignored to reduce SCPI traffic
        and log verbosity.
        """
        if chan not in (1, 2, 3):
            raise ValueError("Channel must be 1, 2, or 3.")

        if self._current_channel == chan:
            return  # already selected → no-op

        self.write(f":INST:SEL CH{chan}")
        self._current_channel = chan

        if self.debug_mode:
            # Log only real transitions
            self.log(f"Selected channel CH{chan}", level="debug")

    def set_voltage(self, volts: float) -> None:
        v_str = self.format_scpi_number(volts)
        self.write(f":SOUR:VOLT {v_str}")
        if self.debug_mode:
            self.log(f"CH{self._current_channel} → Set voltage {v_str} V", level="debug")

    def set_current(self, amps: float) -> None:
        i_str = self.format_scpi_number(amps)
        self.write(f":SOUR:CURR {i_str}")
        if self.debug_mode:
            self.log(f"CH{self._current_channel} → Set current limit {i_str} A", level="debug")

    def read_setpoints(self) -> Tuple[float, float]:
        """Read programmed (voltage, current) setpoints of the active channel."""
        v_resp = self.query(":SOUR:VOLT?")
        i_resp = self.query(":SOUR:CURR?")

        if v_resp is None or i_resp is None:
            raise RuntimeError("Failed to read voltage/current setpoints")

        return float(v_resp), float(i_resp)

    def read_measpoints(self) -> Tuple[float, float]:
        """Read measured (voltage, current) values of the active channel."""
        v_resp = self.query(":MEAS:VOLT?")
        i_resp = self.query(":MEAS:CURR?")

        if v_resp is None or i_resp is None:
            raise RuntimeError("Failed to read measured voltage/current")

        return float(v_resp), float(i_resp)

    def output_enable(self, enable: bool = True) -> None:
        state = "ON" if enable else "OFF"
        self.write(f":OUTP {state}")
        if self.debug_mode:
            self.log(f"CH{self._current_channel} → "
                     f"Output {'ENABLED' if enable else 'DISABLED'}", level="debug")

    # ------------------------------------------------------------------
    # High-level helpers
    # ------------------------------------------------------------------
    def enable_outputs(self, channels: Optional[Iterable[int]] = None) -> None:
        for ch in channels or (1, 2, 3):
            self.select_channel(ch)
            self.output_enable(True)
        time.sleep(self._settle_delay)
        self.log("All selected channels enabled", level="info")

    def disable_outputs(self, channels: Optional[Iterable[int]] = None) -> None:
        for ch in channels or (1, 2, 3):
            self.select_channel(ch)
            self.output_enable(False)
        time.sleep(self._settle_delay)
        self.log("All selected channels disabled", level="info")

    def zero_voltages(self, channels: Optional[Iterable[int]] = None) -> None:
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
