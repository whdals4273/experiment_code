from __future__ import annotations
__version__ = "0.2.0"

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import pyvisa as visa

from src.configs.measurement_device import MeasurementDevice


@dataclass
class CVMeasurementSetup:
    """
    Common C-V setup fields shared by concrete LCR meter drivers.
    """

    function_code: str = "CPD"
    frequency_hz: float = 100_000.0
    ac_level_v: float = 0.05
    bias_enabled: bool = True
    bias_level_v: float = 0.0
    aperture_mode: str = "MED"
    aperture_averages: int = 1
    averaging_count: int = 1          # AVER:COUN (1–256); independent of aperture mode
    trigger_source: str = "BUS"
    auto_range: bool = True
    impedance_range_ohm: str | float | None = None
    settle_delay_s: float = 0.05


@dataclass
class CVRecord:
    """
    One C-V measurement point captured from the instrument.
    """

    bias_v: float
    frequency_hz: float
    primary: float
    secondary: float
    status: Optional[float] = None
    status_raw: str = ""
    raw_response: str = ""
    timestamp_s: float = 0.0


@dataclass
class CVSweepResult:
    """
    Result container for one sweep run.
    """

    mode: str
    setup: CVMeasurementSetup
    points: List[CVRecord] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_rows(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for p in self.points:
            rows.append(
                {
                    "mode": self.mode,
                    "function_code": self.setup.function_code,
                    "frequency_hz": p.frequency_hz,
                    "bias_v": p.bias_v,
                    "primary": p.primary,
                    "secondary": p.secondary,
                    "status": p.status,
                    "status_raw": p.status_raw,
                    "timestamp_s": p.timestamp_s,
                    "raw_response": p.raw_response,
                }
            )
        return rows

    def to_dataframe(self):
        import pandas as pd

        return pd.DataFrame(self.to_rows())


class BaseLCRMeter(MeasurementDevice, ABC):
    """
    Abstract base class for LCR-meter style instruments.
    """

    def __init__(
        self,
        resource_manager: visa.ResourceManager,
        resource_name: str,
        *,
        timeout: int = 10_000,
        debug_mode: bool = False,
    ) -> None:
        super().__init__(
            resource_manager=resource_manager,
            resource_name=resource_name,
            timeout=timeout,
            debug_mode=debug_mode,
        )
        self.model_id: Optional[str] = None
        self.cv_setup = CVMeasurementSetup()
        self.sweep_points: List[float] = []

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    def connect(
        self,
        *,
        timeout: Optional[int] = None,
        read_termination: str = "\n",
        write_termination: str = "\n",
    ) -> None:
        super().connect()
        if self.device is None:
            raise RuntimeError("Device connection failed.")

        if timeout is not None:
            self.device.timeout = int(timeout)
        self.device.read_termination = read_termination
        self.device.write_termination = write_termination

        try:
            self.device.send_end = True
        except Exception:
            # Not all VISA backends expose send_end.
            pass

        self.model_id = self.idn_query()

    def disconnect(self, kill_rm: bool = False) -> None:
        super().disconnect(kill_rm=kill_rm)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    def _require_connected(self):
        if self.device is None:
            raise ConnectionError("Device is not connected.")
        return self.device

    def _query_required(self, command: str) -> str:
        resp = self.query(command)
        if resp is None:
            raise RuntimeError(f"Query failed for command: {command}")
        return resp.strip()

    def set_sweep_range(self, start_v: float, stop_v: float, step_v: float) -> List[float]:
        self.sweep_points = self.build_linear_sweep(start_v, stop_v, step_v)
        return self.sweep_points

    @staticmethod
    def build_linear_sweep(start_v: float, stop_v: float, step_v: float) -> List[float]:
        start = float(start_v)
        stop = float(stop_v)
        step = float(step_v)

        if step == 0.0:
            raise ValueError("step_v cannot be 0.")

        direction = 1.0 if stop >= start else -1.0
        if step * direction <= 0:
            raise ValueError(
                "step_v sign must move from start_v toward stop_v "
                "(positive for ascending, negative for descending)."
            )

        eps = abs(step) * 1e-9 + 1e-15
        out: List[float] = []
        value = start

        for _ in range(1_000_000):
            out.append(float(value))
            next_value = value + step

            if direction > 0 and next_value > stop + eps:
                break
            if direction < 0 and next_value < stop - eps:
                break

            value = next_value
        else:
            raise RuntimeError("Sweep construction exceeded safety limit (1,000,000 points).")

        if not out:
            out.append(start)
        if abs(out[-1] - stop) > eps:
            out.append(stop)

        return out

    # ------------------------------------------------------------------
    # Abstract API contract
    # ------------------------------------------------------------------
    @abstractmethod
    def set_measurement_function(
        self,
        function_code: str,
        *,
        auto_range: bool = True,
        impedance_range_ohm: str | float | None = None,
    ) -> None:
        ...

    @abstractmethod
    def configure_cv_measurement(
        self,
        *,
        function_code: str,
        frequency_hz: float,
        ac_level_v: float,
        bias_level_v: float = 0.0,
        bias_enabled: bool = True,
        aperture_mode: str = "MED",
        aperture_averages: int = 1,
        trigger_source: str = "BUS",
        auto_range: bool = True,
        impedance_range_ohm: str | float | None = None,
        settle_delay_s: Optional[float] = None,
    ) -> None:
        ...

    @abstractmethod
    def fetch_measurement(self) -> CVRecord:
        ...

    @abstractmethod
    def run_single_sweep(
        self,
        *,
        sweep_points: Optional[Sequence[float]] = None,
        dwell_s: Optional[float] = None,
    ) -> CVSweepResult:
        ...

    @abstractmethod
    def run_double_sweep(
        self,
        *,
        sweep_points: Optional[Sequence[float]] = None,
        dwell_s: Optional[float] = None,
    ) -> CVSweepResult:
        ...

    @abstractmethod
    def run_frequency_split_sweep(
        self,
        frequencies_hz: Sequence[float],
        *,
        sweep_points: Optional[Sequence[float]] = None,
        dwell_s: Optional[float] = None,
    ) -> List[CVSweepResult]:
        ...

    def extract_dos_from_cv(self, *_args, **_kwargs):
        """
        Placeholder for DOS extraction from C-V data.

        This is intentionally left unimplemented because the exact extraction
        model depends on process stack details (oxide thickness, area, series
        resistance treatment, temperature model, etc.).
        """
        raise NotImplementedError(
            "DOS extraction is not implemented in the generic LCR driver. "
            "Please add a process-specific extraction routine."
        )

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------
    def __enter__(self) -> "BaseLCRMeter":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()
