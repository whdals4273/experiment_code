from __future__ import annotations
__version__ = "0.0.0"

from abc import ABC, abstractmethod
from typing import Tuple, Iterable, Dict, Optional

import pyvisa as visa

from src.configs.measurement_device import MeasurementDevice


class BasePowerSupply(MeasurementDevice, ABC):
    """
    Abstract base class for programmable power supply instruments.

    This class defines a uniform interface for multi-channel DC power supplies,
    including low-level SCPI primitives and high-level batch helpers.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def __init__(
        self,
        resource_manager: visa.ResourceManager,
        resource_name: str,
        *,
        timeout: int = 1_000,
        delay_time: Optional[float] = None,
        debug_mode: bool = False,
    ) -> None:
        super().__init__(
            resource_manager=resource_manager,
            resource_name=resource_name,
            timeout=timeout,
            debug_mode=debug_mode,
        )

        # Minimum delay between write → read (seconds)
        self._settle_delay: float = 0.5 if delay_time is None else float(delay_time)

        # Verification tolerances (domain-specific)
        self.tol_v: float = 2e-3  # volts
        self.tol_i: float = 2e-3  # amps

    # ------------------------------------------------------------------
    # Abstract: Low-level SCPI control (per channel)
    # ------------------------------------------------------------------
    @abstractmethod
    def select_channel(self, chan: int) -> None:
        """Select the active output channel."""
        ...

    @abstractmethod
    def set_voltage(self, volts: float) -> None:
        """Program the voltage setpoint for the active channel."""
        ...

    @abstractmethod
    def set_current(self, amps: float) -> None:
        """Program the current compliance for the active channel."""
        ...

    @abstractmethod
    def read_setpoints(self) -> Tuple[float, float]:
        """
        Read programmed (voltage, current) setpoints of the active channel.
        """
        ...

    @abstractmethod
    def read_measpoints(self) -> Tuple[float, float]:
        """
        Read measured (voltage, current) values of the active channel.
        """
        ...

    @abstractmethod
    def output_enable(self, enable: bool = True) -> None:
        """Enable or disable the active channel output."""
        ...

    # ------------------------------------------------------------------
    # Abstract: High-level multi-channel helpers
    # ------------------------------------------------------------------
    @abstractmethod
    def enable_outputs(
        self,
        channels: Optional[Iterable[int]] = None,
    ) -> None:
        """Enable the specified channels (default: all)."""
        ...

    @abstractmethod
    def disable_outputs(
        self,
        channels: Optional[Iterable[int]] = None,
    ) -> None:
        """Disable the specified channels (default: all)."""
        ...

    @abstractmethod
    def zero_voltages(
        self,
        channels: Optional[Iterable[int]] = None,
    ) -> None:
        """Set output voltages to 0 V on the specified channels."""
        ...

    @abstractmethod
    def set_voltages(
        self,
        voltages: Dict[int, float],
        *,
        verify: bool = True,
    ) -> None:
        """Program voltages for multiple channels."""
        ...

    @abstractmethod
    def set_compliance_currents(
        self,
        currents: Dict[int, float],
        *,
        verify: bool = True,
    ) -> None:
        """Program current compliance limits for multiple channels."""
        ...

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------
    def _verify_channel(
        self,
        ch: int,
        v_expected: Optional[float],
        i_expected: Optional[float],
    ) -> None:
        """
        Verify measured channel values against expected setpoints.
        NOTE:
            Assumes the target channel has already been selected.
        Raises
        ------
        RuntimeError
            If the measured voltage or current deviates beyond tolerance.
        """
        v_read, i_read = self.read_measpoints()

        if v_expected is not None and abs(v_read - v_expected) > self.tol_v:
            msg = (
                f"CH{ch} voltage verify failed "
                f"(read {v_read:.6g} V, expected {v_expected:.6g} V)"
            )
            self.log(msg, level="error")
            raise RuntimeError(msg)

        if i_expected is not None and abs(i_read - i_expected) > self.tol_i:
            msg = (
                f"CH{ch} current verify failed "
                f"(read {i_read:.6g} A, expected {i_expected:.6g} A)"
            )
            self.log(msg, level="error")
            raise RuntimeError(msg)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------
    def __enter__(self) -> "BasePowerSupply":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()
