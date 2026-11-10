from __future__ import annotations
__version__ = "0.2.0"

from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Tuple
import time
import warnings

import pyvisa as visa

from src.configs.lcr_meter.base_lcr_meter import (
    BaseLCRMeter,
    CVMeasurementSetup,
    CVRecord,
    CVSweepResult,
)

__all__ = ["LCRMeter4284A", "LCR4284A"]


class LCRMeter4284A(BaseLCRMeter):
    """
    Agilent / Keysight 4284A SCPI driver (C-V oriented subset).

    Command families implemented from the local programmer's guide and
    4284A_CV_Reference document:
    - FUNC:IMP:TYPE, FUNC:IMP:RANG, FUNC:IMP:RANG:AUTO
    - FREQ, VOLT:LEV, BIAS:VOLT:LEV, BIAS:CURR, BIAS:STAT
    - APER, AVER:COUN
    - CORR:OPEN, CORR:OPEN:STAT, CORR:SHOR, CORR:SHOR:STAT
    - TRIG:SOUR, TRIG:DEL, TRIG:IMM, *TRG, INIT:IMM
    - FETC?, FETC:SMON:VAC?, FETC:SMON:IAC?
    - LIST:FREQ, LIST:BIAS:VOLT, LIST:MODE (setup helper only)
    - *RST, *CLS

    Static post-processing methods (no instrument I/O):
    - extract_rs_from_accumulation(Cm_acc, Gm_acc, omega)
    - apply_rs_correction(Cm, Gm, Rs, omega)
    - compute_gp_over_omega(Cm, Gm, Cox, omega)
    - extract_dit_hilo(CLF, CHF, Cox, q, A)
    - extract_dit_conductance(Gp_over_omega_max, q, A)
    """

    MODEL_MARKERS = ("4284A",)
    ALLOWED_TRIGGER_SOURCES = {"INT", "EXT", "BUS", "HOLD"}
    ALLOWED_APERTURE_MODES = {"SHOR", "MED", "LONG"}

    def __init__(
        self,
        resource_manager: visa.ResourceManager,
        resource_name: str,
        *,
        timeout: int = 10_000,
        debug_mode: bool = False,
        settle_delay_s: float = 0.05,
    ) -> None:
        super().__init__(
            resource_manager=resource_manager,
            resource_name=resource_name,
            timeout=timeout,
            debug_mode=debug_mode,
        )
        self.cv_setup = CVMeasurementSetup(settle_delay_s=float(settle_delay_s))
        self._last_bias_v: float = self.cv_setup.bias_level_v
        self._last_frequency_hz: float = self.cv_setup.frequency_hz

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
        super().connect(
            timeout=timeout,
            read_termination=read_termination,
            write_termination=write_termination,
        )

        if self.model_id:
            idn_upper = self.model_id.upper()
            if not any(marker in idn_upper for marker in self.MODEL_MARKERS):
                warnings.warn(
                    f"Connected device does not look like 4284A: {self.model_id}",
                    RuntimeWarning,
                )

    # ------------------------------------------------------------------
    # Instrument initialization
    # ------------------------------------------------------------------
    def reset(self) -> None:
        """Send *RST and *CLS to restore factory defaults and clear the error queue."""
        self._require_connected()
        self.write("*RST")
        self.write("*CLS")
        self.log("4284A reset (*RST; *CLS).", level="info")

    # ------------------------------------------------------------------
    # Low-level configuration APIs
    # ------------------------------------------------------------------
    @staticmethod
    def _fmt_number(value: float | int) -> str:
        # Use broad precision to preserve user-provided ranges.
        return f"{float(value):.9g}"

    def set_measurement_function(
        self,
        function_code: str,
        *,
        auto_range: bool = True,
        impedance_range_ohm: str | float | None = None,
    ) -> None:
        self._require_connected()

        func = str(function_code).strip().upper()
        if not func:
            raise ValueError("function_code cannot be empty.")

        self.write(f"FUNC:IMP:TYPE {func}")
        self.write(f"FUNC:IMP:RANG:AUTO {1 if auto_range else 0}")

        if not auto_range and impedance_range_ohm is not None:
            self.set_impedance_range(impedance_range_ohm)

        self.cv_setup.function_code = func
        self.cv_setup.auto_range = bool(auto_range)
        self.cv_setup.impedance_range_ohm = impedance_range_ohm

    def set_impedance_range(self, impedance_range_ohm: str | float) -> None:
        self._require_connected()

        if isinstance(impedance_range_ohm, str):
            range_text = impedance_range_ohm.strip().upper()
        else:
            range_text = f"{self._fmt_number(float(impedance_range_ohm))}OHM"

        if not range_text:
            raise ValueError("impedance_range_ohm is empty.")

        self.write(f"FUNC:IMP:RANG {range_text}")
        self.cv_setup.impedance_range_ohm = impedance_range_ohm

    def set_frequency(self, frequency_hz: float) -> None:
        self._require_connected()
        freq = float(frequency_hz)
        if freq <= 0:
            raise ValueError("frequency_hz must be > 0.")
        self.write(f"FREQ {self._fmt_number(freq)}")
        self.cv_setup.frequency_hz = freq
        self._last_frequency_hz = freq

    def set_ac_level(self, ac_level_v: float) -> None:
        """Set the AC test signal amplitude (VOLT:LEV). Recommended ≤ 30 mV for thin oxides."""
        self._require_connected()
        level = float(ac_level_v)
        if level <= 0:
            raise ValueError("ac_level_v must be > 0.")
        self.write(f"VOLT:LEV {self._fmt_number(level)}")
        self.cv_setup.ac_level_v = level

    def set_dc_bias(
        self,
        *,
        bias_level_v: Optional[float] = None,
        bias_current_a: Optional[float] = None,
        enabled: bool = True,
    ) -> None:
        self._require_connected()
        if bias_level_v is not None:
            self.write(f"BIAS:VOLT:LEV {self._fmt_number(float(bias_level_v))}")
            self.cv_setup.bias_level_v = float(bias_level_v)
            self._last_bias_v = float(bias_level_v)

        if bias_current_a is not None:
            self.write(f"BIAS:CURR {self._fmt_number(float(bias_current_a))}")

        self.write(f"BIAS:STAT {1 if enabled else 0}")
        self.cv_setup.bias_enabled = bool(enabled)

    def set_aperture(self, mode: str = "MED", averages: int = 1) -> None:
        self._require_connected()
        mode_u = str(mode).strip().upper()
        if mode_u not in self.ALLOWED_APERTURE_MODES:
            allowed = ", ".join(sorted(self.ALLOWED_APERTURE_MODES))
            raise ValueError(f"Invalid aperture mode '{mode}'. Allowed: {allowed}")

        avg = int(averages)
        if avg < 1:
            raise ValueError("aperture averages must be >= 1.")

        self.write(f"APER {mode_u},{avg}")
        self.cv_setup.aperture_mode = mode_u
        self.cv_setup.aperture_averages = avg

    def set_averaging_count(self, count: int) -> None:
        """
        Set the number of measurement averages (AVER:COUN, range 1–256).

        This is independent of the aperture integration time set by set_aperture().
        Increase for low-noise measurements; higher counts reduce sweep speed.
        """
        self._require_connected()
        n = int(count)
        if not (1 <= n <= 256):
            raise ValueError(f"averaging_count must be in range [1, 256], got {n}.")
        self.write(f"AVER:COUN {n}")
        self.cv_setup.averaging_count = n

    def set_trigger_source(self, source: str = "BUS") -> None:
        self._require_connected()
        src = str(source).strip().upper()
        if src not in self.ALLOWED_TRIGGER_SOURCES:
            allowed = ", ".join(sorted(self.ALLOWED_TRIGGER_SOURCES))
            raise ValueError(f"Invalid trigger source '{source}'. Allowed: {allowed}")

        self.write(f"TRIG:SOUR {src}")
        self.cv_setup.trigger_source = src

    def set_trigger_delay(self, delay_s: float) -> None:
        self._require_connected()
        delay = float(delay_s)
        if delay < 0:
            raise ValueError("delay_s must be >= 0.")
        self.write(f"TRIG:DEL {self._fmt_number(delay)}S")

    def set_high_power_output(self, enabled: bool) -> None:
        self._require_connected()
        self.write(f"OUTP:HPOW {1 if enabled else 0}")

    def set_dc_isolation(self, enabled: bool) -> None:
        self._require_connected()
        self.write(f"OUTP:DC:ISOL {1 if enabled else 0}")

    # ------------------------------------------------------------------
    # Open / Short compensation
    # ------------------------------------------------------------------
    def run_open_correction(self, wait_s: float = 10.0) -> None:
        """
        Execute open-circuit compensation (probes raised, no DUT).

        Sends CORR:OPEN and waits for the instrument to complete the measurement.
        Follow with set_correction_open_state(True) to activate the correction.

        Args:
            wait_s: Seconds to wait for the compensation to finish (default 10 s).
        """
        self._require_connected()
        self.write("CORR:OPEN")
        if wait_s > 0:
            time.sleep(wait_s)
        self.log("Open correction executed.", level="info")

    def run_short_correction(self, wait_s: float = 10.0) -> None:
        """
        Execute short-circuit compensation (probes shorted to chuck).

        Sends CORR:SHOR and waits for the instrument to complete the measurement.
        Follow with set_correction_short_state(True) to activate the correction.

        Args:
            wait_s: Seconds to wait for the compensation to finish (default 10 s).
        """
        self._require_connected()
        self.write("CORR:SHOR")
        if wait_s > 0:
            time.sleep(wait_s)
        self.log("Short correction executed.", level="info")

    def set_correction_open_state(self, enabled: bool) -> None:
        """Enable or disable the open-circuit correction (CORR:OPEN:STAT)."""
        self._require_connected()
        self.write(f"CORR:OPEN:STAT {1 if enabled else 0}")

    def set_correction_short_state(self, enabled: bool) -> None:
        """Enable or disable the short-circuit correction (CORR:SHOR:STAT)."""
        self._require_connected()
        self.write(f"CORR:SHOR:STAT {1 if enabled else 0}")

    # ------------------------------------------------------------------
    # High-level C-V setup
    # ------------------------------------------------------------------
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
        averaging_count: int = 1,
        trigger_source: str = "BUS",
        auto_range: bool = True,
        impedance_range_ohm: str | float | None = None,
        settle_delay_s: Optional[float] = None,
    ) -> None:
        self._require_connected()

        self.set_measurement_function(
            function_code=function_code,
            auto_range=auto_range,
            impedance_range_ohm=impedance_range_ohm,
        )
        self.set_frequency(frequency_hz)
        self.set_ac_level(ac_level_v)
        self.set_aperture(aperture_mode, aperture_averages)
        self.set_averaging_count(averaging_count)
        self.set_trigger_source(trigger_source)
        self.set_dc_bias(bias_level_v=bias_level_v, enabled=bias_enabled)

        if settle_delay_s is not None:
            self.cv_setup.settle_delay_s = float(settle_delay_s)

        self.log(
            (
                "Configured 4284A C-V setup "
                f"(FUNC={self.cv_setup.function_code}, FREQ={self.cv_setup.frequency_hz:g} Hz, "
                f"VOLT:LEV={self.cv_setup.ac_level_v:g} V, BIAS={self.cv_setup.bias_level_v:g} V, "
                f"APER={self.cv_setup.aperture_mode},{self.cv_setup.aperture_averages}, "
                f"AVER:COUN={self.cv_setup.averaging_count})"
            ),
            level="info",
        )

    # ------------------------------------------------------------------
    # List setup helper (optional)
    # ------------------------------------------------------------------
    def setup_list_sweep(
        self,
        *,
        list_mode: str,
        frequencies_hz: Optional[Sequence[float]] = None,
        bias_levels_v: Optional[Sequence[float]] = None,
    ) -> None:
        """
        Program LIST subsystem tables. Execution still uses controller loops.
        """
        self._require_connected()

        mode = str(list_mode).strip().upper()
        if mode not in {"SEQ", "STEP"}:
            raise ValueError("list_mode must be 'SEQ' or 'STEP'.")

        self.write(f"LIST:MODE {mode}")

        if frequencies_hz:
            freq_payload = ",".join(self._fmt_number(float(v)) for v in frequencies_hz)
            self.write(f"LIST:FREQ {freq_payload}")

        if bias_levels_v:
            bias_payload = ",".join(self._fmt_number(float(v)) for v in bias_levels_v)
            self.write(f"LIST:BIAS:VOLT {bias_payload}")

    # ------------------------------------------------------------------
    # Measurement execution
    # ------------------------------------------------------------------
    def _trigger_measurement(self) -> None:
        # BUS trigger is standard for fully controller-driven loops.
        if self.cv_setup.trigger_source == "BUS":
            self.write("*TRG")
            return

        # For non-BUS modes, keep a software fallback for explicit runs.
        self.write("TRIG:IMM")

    def fetch_measurement(self) -> CVRecord:
        self._require_connected()
        response = self._query_required("FETC?")

        values = [x.strip() for x in response.replace(";", ",").split(",") if x.strip()]
        if len(values) < 2:
            raise RuntimeError(
                "Unexpected FETC? response format. "
                f"Expected at least 2 values, got: '{response}'"
            )

        try:
            primary = float(values[0])
            secondary = float(values[1])
        except ValueError as exc:
            raise RuntimeError(f"Failed to parse FETC? response: '{response}'") from exc

        status_raw = values[2] if len(values) >= 3 else ""
        status_val: Optional[float] = None
        if status_raw:
            try:
                status_val = float(status_raw)
            except ValueError:
                status_val = None

        return CVRecord(
            bias_v=self._last_bias_v,
            frequency_hz=self._last_frequency_hz,
            primary=primary,
            secondary=secondary,
            status=status_val,
            status_raw=status_raw,
            raw_response=response,
            timestamp_s=time.time(),
        )

    def fetch_source_monitor(self) -> Dict[str, float]:
        """
        Read source monitor voltage/current from 4284A.
        """
        vac_raw = self._query_required("FETC:SMON:VAC?")
        iac_raw = self._query_required("FETC:SMON:IAC?")
        return {
            "vac_v": float(vac_raw),
            "iac_a": float(iac_raw),
        }

    def _set_bias_for_sweep(self, bias_v: float) -> None:
        self._last_bias_v = float(bias_v)
        self.write(f"BIAS:VOLT:LEV {self._fmt_number(self._last_bias_v)}")
        self.write(f"BIAS:STAT {1 if self.cv_setup.bias_enabled else 0}")

    def _apply_presoak(self, presoak_v: float, hold_time_s: float) -> None:
        """
        Apply a pre-soak (hold) bias before sweeping.

        Sets the instrument to presoak_v, enables bias, and waits hold_time_s
        to allow minority carrier equilibration per the CV Reference protocol.
        """
        self._last_bias_v = float(presoak_v)
        self.write(f"BIAS:VOLT:LEV {self._fmt_number(self._last_bias_v)}")
        self.write("BIAS:STAT 1")
        if hold_time_s > 0:
            self.log(
                f"Pre-soak at {presoak_v:g} V for {hold_time_s:g} s ...",
                level="info",
            )
            time.sleep(hold_time_s)

    def _run_points(
        self,
        points: Sequence[float],
        *,
        dwell_s: float,
    ) -> List[CVRecord]:
        out: List[CVRecord] = []
        for bias_v in points:
            self._set_bias_for_sweep(float(bias_v))
            if dwell_s > 0:
                time.sleep(dwell_s)

            self._trigger_measurement()
            out.append(self.fetch_measurement())
        return out

    def _build_result(
        self,
        mode: str,
        points: List[CVRecord],
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CVSweepResult:
        return CVSweepResult(
            mode=mode,
            setup=replace(self.cv_setup),
            points=points,
            metadata=metadata or {},
        )

    def run_single_sweep(
        self,
        *,
        sweep_points: Optional[Sequence[float]] = None,
        dwell_s: Optional[float] = None,
        presoak_v: Optional[float] = None,
        hold_time_s: float = 0.0,
    ) -> CVSweepResult:
        """
        Run one forward C-V sweep.

        Args:
            sweep_points: Voltage points to step through. Falls back to set_sweep_range().
            dwell_s: Step settle time in seconds. Falls back to cv_setup.settle_delay_s.
            presoak_v: Optional pre-soak voltage applied for hold_time_s before sweep.
                       Use for minority-carrier equilibration (e.g., strong inversion
                       for p-type substrates). If None, no pre-soak is applied.
            hold_time_s: Duration of the pre-soak in seconds (default 0 = no wait).
        """
        self._require_connected()

        points = list(sweep_points) if sweep_points is not None else list(self.sweep_points)
        if not points:
            raise ValueError("Sweep points are empty. Call set_sweep_range(...) first.")

        if presoak_v is not None:
            self._apply_presoak(presoak_v, hold_time_s)

        dwell = self.cv_setup.settle_delay_s if dwell_s is None else float(dwell_s)
        records = self._run_points(points, dwell_s=dwell)
        return self._build_result(
            "single",
            records,
            metadata={
                "sweep_points": points,
                "presoak_v": presoak_v,
                "hold_time_s": hold_time_s,
            },
        )

    def run_double_sweep(
        self,
        *,
        sweep_points: Optional[Sequence[float]] = None,
        dwell_s: Optional[float] = None,
        presoak_v: Optional[float] = None,
        hold_time_s: float = 0.0,
    ) -> CVSweepResult:
        """
        Run a forward + reverse C-V sweep (hysteresis measurement).

        The midpoint (turning voltage) is not repeated. Use presoak_v / hold_time_s
        to equilibrate the device before the forward direction.
        """
        self._require_connected()

        forward = list(sweep_points) if sweep_points is not None else list(self.sweep_points)
        if not forward:
            raise ValueError("Sweep points are empty. Call set_sweep_range(...) first.")

        reverse = list(reversed(forward))
        if len(forward) > 1 and reverse and reverse[0] == forward[-1]:
            reverse = reverse[1:]

        if presoak_v is not None:
            self._apply_presoak(presoak_v, hold_time_s)

        dwell = self.cv_setup.settle_delay_s if dwell_s is None else float(dwell_s)
        records = self._run_points(forward + reverse, dwell_s=dwell)
        return self._build_result(
            "double",
            records,
            metadata={
                "forward_points": forward,
                "reverse_points": reverse,
                "presoak_v": presoak_v,
                "hold_time_s": hold_time_s,
            },
        )

    def run_frequency_split_sweep(
        self,
        frequencies_hz: Sequence[float],
        *,
        sweep_points: Optional[Sequence[float]] = None,
        dwell_s: Optional[float] = None,
        presoak_v: Optional[float] = None,
        hold_time_s: float = 0.0,
    ) -> List[CVSweepResult]:
        """
        Run one sweep at each of the given frequencies (frequency dispersion analysis).

        A fresh pre-soak is applied once before the first frequency sweep if presoak_v
        is given. The original frequency is restored on completion.
        """
        self._require_connected()

        freq_list = [float(f) for f in frequencies_hz]
        if not freq_list:
            raise ValueError("frequencies_hz cannot be empty.")

        points = list(sweep_points) if sweep_points is not None else list(self.sweep_points)
        if not points:
            raise ValueError("Sweep points are empty. Call set_sweep_range(...) first.")

        original_freq = self.cv_setup.frequency_hz
        dwell = self.cv_setup.settle_delay_s if dwell_s is None else float(dwell_s)
        results: List[CVSweepResult] = []

        if presoak_v is not None:
            self._apply_presoak(presoak_v, hold_time_s)

        try:
            for freq in freq_list:
                self.set_frequency(freq)
                records = self._run_points(points, dwell_s=dwell)
                results.append(
                    self._build_result(
                        mode="frequency_split",
                        points=records,
                        metadata={
                            "frequency_hz": freq,
                            "sweep_points": points,
                        },
                    )
                )
        finally:
            # Restore the initial frequency for next operations.
            self.set_frequency(original_freq)

        return results

    # ------------------------------------------------------------------
    # Post-processing (no instrument I/O)
    # ------------------------------------------------------------------
    @staticmethod
    def extract_rs_from_accumulation(
        Cm_acc: float,
        Gm_acc: float,
        omega: float,
    ) -> float:
        """
        Extract series resistance Rs from a Cp-Gp measurement in strong accumulation.

        Perform this measurement at the characterisation frequency (e.g., 1 MHz)
        with the device biased well into accumulation (Vg = Vfb - 2 V or stronger).

        Formula (from CV Reference §2.2.2):
            Rs = Gm / (Gm² + (ω·Cm)²)

        Args:
            Cm_acc: Measured parallel capacitance in accumulation (F).
            Gm_acc: Measured parallel conductance in accumulation (S).
            omega:  Angular frequency = 2π·f (rad/s).

        Returns:
            Rs in ohms.
        """
        denom = Gm_acc ** 2 + (omega * Cm_acc) ** 2
        if denom == 0.0:
            raise ValueError("Gm_acc and Cm_acc cannot both be zero.")
        return Gm_acc / denom

    @staticmethod
    def apply_rs_correction(
        Cm: float,
        Gm: float,
        Rs: float,
        omega: float,
    ) -> Tuple[float, float]:
        """
        Apply series-resistance correction to a Cp-Gp data point.

        Use the Rs value extracted via extract_rs_from_accumulation() and apply
        this correction to every point in the C-V sweep (series resistance distorts
        both capacitance and conductance at high frequencies).

        Formulae (from CV Reference §2.2.2):
            a      = Gm - (Gm² + ω²·Cm²)·Rs
            C_corr = [(Gm² + ω²·Cm²)·Cm] / [a² + ω²·Cm²]
            G_corr = [(Gm² + ω²·Cm²)·a]  / [a² + ω²·Cm²]

        Args:
            Cm:    Measured parallel capacitance (F).
            Gm:    Measured parallel conductance (S).
            Rs:    Series resistance in ohms (from extract_rs_from_accumulation).
            omega: Angular frequency = 2π·f (rad/s).

        Returns:
            Tuple (C_corr, G_corr) — corrected capacitance (F) and conductance (S).
        """
        denom_sq = Gm ** 2 + (omega * Cm) ** 2
        a = Gm - denom_sq * Rs
        inner = a ** 2 + (omega * Cm) ** 2
        if inner == 0.0:
            raise ValueError("Corrected denominator is zero; check Rs value.")
        C_corr = denom_sq * Cm / inner
        G_corr = denom_sq * a / inner
        return C_corr, G_corr

    @staticmethod
    def compute_gp_over_omega(
        Cm: float,
        Gm: float,
        Cox: float,
        omega: float,
    ) -> float:
        """
        Compute the normalised parallel conductance Gp/ω for the conductance method.

        This quantity peaks at the interface trap characteristic frequency ω_peak.
        Sweep ω across ~1 kHz–1 MHz at each gate bias to locate the peak.

        Formula (Nicollian-Goetzberger, CV Reference §4.4):
            Gp/ω = ω·Cox²·Gm / [Gm² + ω²·(Cox - Cm)²]

        Note: Apply Rs correction to Cm and Gm before calling this method.

        Args:
            Cm:    Rs-corrected parallel capacitance at the measurement frequency (F).
            Gm:    Rs-corrected parallel conductance at the measurement frequency (S).
            Cox:   Oxide capacitance measured in strong accumulation (F).
            omega: Angular frequency = 2π·f (rad/s).

        Returns:
            Gp/ω in S/rad (siemens per radian per second).
        """
        numerator = omega * Cox ** 2 * Gm
        denominator = Gm ** 2 + omega ** 2 * (Cox - Cm) ** 2
        if denominator == 0.0:
            raise ValueError("Conductance denominator is zero; check input values.")
        return numerator / denominator

    @staticmethod
    def extract_dit_hilo(
        CLF: float,
        CHF: float,
        Cox: float,
        q: float = 1.602e-19,
        A: float = 1.0,
    ) -> float:
        """
        Extract interface trap density Dit using the High-Low frequency (Castagne-Vapaille) method.

        Requires simultaneous or sequential measurement at a quasi-static / low
        frequency (CLF; traps respond fully, typically < 100 Hz) and a high
        frequency (CHF; traps do not respond, typically 1 MHz) at the same gate bias.

        Formula (CV Reference §4.3):
            Dit = (Cox/q²) × A⁻¹ × [(CLF/Cox)/(1 - CLF/Cox) - (CHF/Cox)/(1 - CHF/Cox)]

        Args:
            CLF: Low-frequency measured capacitance at one gate bias point (F).
            CHF: High-frequency measured capacitance at the same gate bias point (F).
            Cox: Oxide capacitance (strong accumulation value) (F).
            q:   Elementary charge (C). Default 1.602e-19 C.
            A:   Gate area (cm²). Default 1.0 (returns Dit per cm²·eV if A = 1 cm²).

        Returns:
            Dit in cm⁻²·eV⁻¹.
        """
        def _term(C: float) -> float:
            ratio = C / Cox
            denom = 1.0 - ratio
            if abs(denom) < 1e-30:
                raise ValueError("CLF or CHF equals Cox; check measurement at correct bias point.")
            return ratio / denom

        return (Cox / (q ** 2 * A)) * (_term(CLF) - _term(CHF))

    @staticmethod
    def extract_dit_conductance(
        Gp_over_omega_max: float,
        q: float = 1.602e-19,
        A: float = 1.0,
    ) -> float:
        """
        Extract interface trap density Dit from the peak Gp/ω value (conductance method).

        Use the maximum Gp/ω value over the frequency sweep at a fixed gate bias.
        This method is the most accurate for energies near midgap.

        Formula (Nicollian-Goetzberger, CV Reference §4.4, continuous trap distribution):
            Dit = 2.5 / (q·A) × (Gp/ω)_max

        Args:
            Gp_over_omega_max: Peak value of Gp/ω from compute_gp_over_omega() (S·s/rad).
            q:  Elementary charge (C). Default 1.602e-19 C.
            A:  Gate area (cm²). Default 1.0.

        Returns:
            Dit in cm⁻²·eV⁻¹.
        """
        if q <= 0 or A <= 0:
            raise ValueError("q and A must be positive.")
        return 2.5 * Gp_over_omega_max / (q * A)


# Backward-friendly alias
LCR4284A = LCRMeter4284A
