from __future__ import annotations
__version__ = "0.0.0"

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

import pyvisa

from src.configs.measurement_device import MeasurementDevice


class BasePulseGenerator(MeasurementDevice, ABC):
    """
    Abstract base class for pulse / pattern generators
    (e.g., Keysight / Agilent 81110A, 81115A).

    This class defines a *channel-centric*, dictionary-based configuration
    interface suitable for both simple pulse and advanced pattern modes.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def __init__(
        self,
        resource_manager: pyvisa.ResourceManager,
        resource_name: str,
        *,
        timeout: int = 5_000,
        debug_mode: bool = False,
    ) -> None:
        """
        Initialize the pulse generator interface.

        Parameters
        ----------
        resource_manager : pyvisa.ResourceManager
            Shared VISA resource manager.
        resource_name : str
            VISA address string (e.g. "GPIB0::10::INSTR").
        timeout : int, optional
            VISA I/O timeout in milliseconds.
        debug_mode : bool, optional
            Enable verbose debug logging.
        """
        super().__init__(
            resource_manager=resource_manager,
            resource_name=resource_name,
            timeout=timeout,
            debug_mode=debug_mode,
        )

    # ------------------------------------------------------------------
    # Abstract: Per-channel programming
    # ------------------------------------------------------------------
    @abstractmethod
    def set_channel(
        self,
        channel: int,
        settings: Dict[str, Any],
        *,
        verify: bool = True,
    ) -> None:
        """
        Configure a pulse channel using a settings dictionary.

        Parameters
        ----------
        channel : int
            Logical channel index (typically 1 or 2).
        settings : dict
            Mapping of parameter names → values.

            Example::
                {
                    "voltage_high": 2.0,
                    "voltage_low":  0.0,
                    "width":        10e-9,
                    "period":       100e-9,
                    "delay":        5e-9,
                    "rise":         1e-9,
                    "fall":         1e-9,
                }

        verify : bool, optional
            If True, read back programmed values and verify correctness.

        Notes
        -----
        - Subclasses must validate supported keys.
        - Unsupported keys must raise ValueError.
        """
        ...

    @abstractmethod
    def read_setpoints(self, channel: int) -> Dict[str, float]:
        """
        Read programmed setpoints for the specified channel.

        Parameters
        ----------
        channel : int
            Channel to query.

        Returns
        -------
        dict[str, float]
            Mapping of parameter names → programmed values (SI units).
        """
        ...

    @abstractmethod
    def enable_channel(self, channel: int, enable: bool = True) -> None:
        """
        Enable or disable pulse output on the specified channel.

        Parameters
        ----------
        channel : int
            Channel index.
        enable : bool
            True to enable output, False to disable.
        """
        ...

    # ------------------------------------------------------------------
    # Abstract: Math / Combination mode
    # ------------------------------------------------------------------
    @abstractmethod
    def set_math_mode(self, enable: bool) -> None:
        """
        Enable or disable channel combination (math) mode.

        Parameters
        ----------
        enable : bool
            True  → enable channel combination (e.g. CH1 + CH2)
            False → disable combination mode

        Raises
        ------
        NotImplementedError
            If the instrument does not support math mode.
        """
        ...

    # ------------------------------------------------------------------
    # Abstract: Triggering and burst control
    # ------------------------------------------------------------------
    @abstractmethod
    def set_trigger(self, trigger_source: str) -> None:
        """
        Configure the trigger source.

        Parameters
        ----------
        trigger_source : str
            SCPI trigger source identifier (e.g. "BUS", "EXT", "INT").
        """
        ...

    @abstractmethod
    def set_burst(
        self,
        channel: int,
        burst: Optional[int],
    ) -> None:
        """
        Configure burst mode for a channel.

        Parameters
        ----------
        channel : int
            Channel index.
        burst : int or None
            - int  → number of pulses in burst
            - None → disable burst mode
        """
        ...

    @abstractmethod
    def trigger_pulse(self) -> None:
        """
        Issue a software trigger.

        Notes
        -----
        Typical SCPI commands include::

            *TRG
            TRIG:IMM
        """
        ...

    # ------------------------------------------------------------------
    # Abstract: Save / recall system
    # ------------------------------------------------------------------
    @abstractmethod
    def save_settings(self, name: str) -> None:
        """
        Save the current instrument configuration.

        Parameters
        ----------
        name : str
            Profile name or memory slot identifier.
        """
        ...

    @abstractmethod
    def recall_settings(self, name: str) -> None:
        """
        Recall a previously saved configuration.

        Parameters
        ----------
        name : str
            Profile name or memory slot identifier.
        """
        ...

    # ------------------------------------------------------------------
    # Verification helper
    # ------------------------------------------------------------------
    @abstractmethod
    def _verify_channel(
        self,
        channel: int,
        expected: Dict[str, Optional[float]],
    ) -> None:
        """
        Verify programmed channel parameters.

        Parameters
        ----------
        channel : int
            Channel index.
        expected : dict[str, float | None]
            Mapping of parameter names → expected values.

        Behavior
        --------
        - Missing keys are ignored.
        - Values of None are ignored.
        - Out-of-tolerance values must raise RuntimeError.
        """
        ...
