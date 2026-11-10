from __future__ import annotations
__version__ = "0.0.2"

import time
from typing import Dict, Any, Optional

import pyvisa
from tqdm import tqdm

from src.configs.pulse_generator.base_pulse_generator import BasePulseGenerator


class PulseGenerator81110A(BasePulseGenerator):
    """
    SCPI driver for Agilent/Keysight 81110A / 81115A pulse
    pattern generators, fully using MeasurementDevice's write/query helpers.
    """
    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def __init__(
        self,
        resource_manager: pyvisa.ResourceManager,
        resource_name: str,
        *,
        timeout: int = 2_000,
        debug_mode: bool = False,
    ) -> None:
        super().__init__(
            resource_manager=resource_manager,
            resource_name=resource_name,
            timeout=timeout,
            debug_mode=debug_mode,
        )
        self.is81150 = False
        self.MAX_COUNTER = 1_000_000_000

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------
    def initialize(self) -> None:
        """
        Connect and put the generator into a known PC-controlled state.
        """
        self.connect()

        idn = self.idn_query()
        if idn is None:
            raise RuntimeError("Failed to read device ID.")

        if "81150" in idn:
            self.is81150 = True
            self.log("Detected 81150A model", level="info")
        elif "81110" not in idn:
            self.log(f"Unexpected device ID: {idn}", level="warning")
        else:
            self.log(f"Connected to {idn}", level="info")



        self.write("*RST")
        self.write("*CLS")

        # Voltage mode
        self.write(":HOLD VOLT")

        # Disable external width mode
        self.write(":ARM:EWID OFF")
        
        self.write(":ARM:SENS EDGE")
        self.write(":ARM:SLOP POS")
        # Internal VFO as pulse-period source
        self.write(":TRIG:SOUR IMM")
        # MAN arm, immediate trigger
        for ch in (1, 2):
            self.write(f":ARM:SOUR{ch} MAN")
            self.write(f":DIG{ch}:PATT OFF")
            self.write(f":TRIG{ch}:COUN 1")
            self.write(f":OUTP{ch}:POL NORM")
            self.write(f":OUTP{ch}:IMP 50")
            self.write(f":OUTP{ch}:IMP:EXT 1MOHM")
            if self.is81150:
                self.write(f":FUNC{ch} PULS")

        self.log("Pulse generator initialized", level="info")

    # ------------------------------------------------------------------
    # Safe disconnect (OVERRIDDEN)
    # ------------------------------------------------------------------
    def disconnect(self, kill_rm: bool = False) -> None:
        """
        Disable all outputs and close VISA safely.
        """
        if self.device is not None:
            try:
                for ch in (1, 2):
                    self.write(f":OUTP{ch}:STAT OFF")
                self.wait_for_completion()
                self.log("All pulse outputs disabled", level="info")
            except Exception as e:
                self.log(f"Failed to disable outputs during disconnect: {e}", level="warning")

        super().disconnect(kill_rm=kill_rm)

    # ------------------------------------------------------------------
    # Helpers: key aliasing and normalization
    # ------------------------------------------------------------------
    @staticmethod
    def _aliases() -> Dict[str, set[str]]:
        return {
            "period": {"period", "per", "tperiod"},
            "width": {"width", "pw", "pulsewidth"},
            "delay": {"delay", "del"},
            "high": {"high", "hi", "vhigh", "vih"},
            "low": {"low", "lo", "vlow", "vil"},
            "rise": {"rise", "trise", "t_rise"},
            "fall": {"fall", "tfall", "t_fall"},
            "zsource": {"zsource", "zs", "source_impedance"},
            "zload": {"zload", "zl", "load_impedance"},
            "polarity": {"pol", "polarity"},
        }

    def _normalize_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        aliases = self._aliases()
        canonical: Dict[str, Any] = {}

        for raw_key, val in settings.items():
            key_l = raw_key.strip().lower()
            matched = False
            for canon, names in aliases.items():
                if key_l in names:
                    canonical[canon] = val
                    matched = True
                    break
            if not matched:
                allowed = ", ".join(sorted(aliases.keys()))
                raise ValueError(f"Unsupported setting key '{raw_key}'. Allowed canonical keys: {allowed}")

        def _normalize_polarity(value: Any) -> str:
            token = str(value).strip().upper()
            return "INV" if token in {"INV", "INVERTED", "INVERT"} else "NORM"

        # High/low auto-swap + deterministic polarity derivation.
        if "high" in canonical and "low" in canonical:
            try:
                hi = float(canonical["high"])
                lo = float(canonical["low"])
                requested_pol = _normalize_polarity(canonical.get("polarity", "NORM"))
                resolved_pol = requested_pol
                if hi < lo:
                    canonical["high"], canonical["low"] = lo, hi
                    resolved_pol = "INV" if requested_pol == "NORM" else "NORM"
                    self.log("Swapped HIGH/LOW and inverted polarity due to HIGH<LOW", level="warning")
                canonical["polarity"] = resolved_pol
            except Exception:
                # If cannot parse, skip swap logic safely
                pass
        elif "polarity" in canonical:
            canonical["polarity"] = _normalize_polarity(canonical["polarity"])

        return canonical

    def _scpi_map(self, channel: int) -> Dict[str, str]:
        return {
            "period": f":PULS:PER{channel} {{val}}",
            "width": f":PULS:WIDT{channel} {{val}}",
            "delay": f":PULS:DEL{channel} {{val}}",
            "high": f":VOLT{channel}:HIGH {{val}}",
            "low": f":VOLT{channel}:LOW {{val}}",
            "rise": f":PULS:TRAN{channel}:LEAD {{val}}",
            "fall": f":PULS:TRAN{channel}:TRA {{val}}",
            "zsource": f":OUTP{channel}:IMP {{val}}",
            "zload": f":OUTP{channel}:IMP:EXT {{val}}",
            "polarity": f":OUTP{channel}:POL {{val}}",
        }
        
    # ------------------------------------------------------------------
    # BasePulseGenerator abstract methods
    # ------------------------------------------------------------------
    def set_channel(
        self,
        channel: int,
        settings: Dict[str, Any],
    ) -> None:
        if self.device is None:
            raise ConnectionError("Device not connected.")
        if channel not in (1, 2):
            raise ValueError("Channel must be 1 or 2.")

        canonical = self._normalize_settings(settings)
        scpi = self._scpi_map(channel)

        for key, val in canonical.items():
            cmd = scpi[key].format(val=self.format_scpi_number(val))
            self.write(cmd)

        if self.debug_mode:
            self.log(f"CH{channel} configured: {canonical}", level="debug")

    def read_setpoints(self, channel: int) -> Dict[str, float]:
        if self.device is None:
            raise ConnectionError("Device not connected.")
        if channel not in (1, 2):
            raise ValueError("Channel must be 1 or 2.")

        def qf(cmd: str) -> float:
            resp = self.query(cmd)
            if resp is None:
                raise RuntimeError(f"Query failed: {cmd}")
            return float(resp)

        pol = self.query(f":OUTP{channel}:POL?")
        pol_s = pol.strip() if pol else ""

        return {
            "period": qf(f":PULS:PER{channel}?"),
            "width": qf(f":PULS:WIDT{channel}?"),
            "delay": qf(f":PULS:DEL{channel}?"),
            "high": qf(f":VOLT{channel}:HIGH?"),
            "low": qf(f":VOLT{channel}:LOW?"),
            "rise": qf(f":PULS:TRAN{channel}:LEAD?"),
            "fall": qf(f":PULS:TRAN{channel}:TRA?"),
            "zsource": qf(f":OUTP{channel}:IMP?"),
            "zload": qf(f":OUTP{channel}:IMP:EXT?"),
            "polarity": pol_s,
        }

    def enable_channel(self, channel: int, onoff: bool = True) -> None:
        if self.device is None:
            raise ConnectionError("Device not connected.")
        if channel not in (1, 2):
            raise ValueError("Channel must be 1 or 2.")
        self.write(f":OUTP{channel}:STAT {'ON' if onoff else 'OFF'}")
        if self.debug_mode:
            self.log(f"CH{channel} output {'ON' if onoff else 'OFF'}", level="debug")

    def set_math_mode(self, enable: bool) -> None:
        if self.device is None:
            raise ConnectionError("Device not connected.")
        mode = "PLUS" if enable else "OFF"
        self.write(f":CHAN:MATH {mode}")
        self.log(f"Math mode set to {mode}", level="info")

    def set_trigger(self, trigger_source: str) -> None:
        if self.device is None:
            raise ConnectionError("Device not connected.")
        self.write(f":TRIG:SOUR {trigger_source.upper()}")
        if self.debug_mode:
            self.log(f"Trigger source set to {trigger_source.upper()}", level="debug")

    def set_burst(self, channel: int, burst: Optional[int]) -> None:
        if self.device is None:
            raise ConnectionError("Device not connected.")
        if channel not in (1, 2):
            raise ValueError("Channel must be 1 or 2.")

        self._arm_manual(channel)

        if burst is None:
            self.write(f":BURSt{channel}:STAT OFF")
        else:
            if burst < 1:
                raise ValueError("burst must be >= 1, or None to disable.")
            self.write(f":BURSt{channel}:STAT ON")
            self.write(f":BURSt{channel}:NCYC {burst}")

        if self.debug_mode:
            self.log(f"CH{channel} burst {'OFF' if burst is None else burst}", level="debug")

    def trigger_pulse(self) -> None:
        if self.device is None:
            raise ConnectionError("Device not connected.")
        # ``*TRG`` is a bus/software trigger, so force BUS source before issuing it.
        # This keeps call sites simple and avoids silent no-op behavior when the
        # instrument was previously left in IMM/EXT trigger mode.
        self.set_trigger("BUS")
        self.write("*TRG")
        if self.debug_mode:
            self.log("Pulse triggered", level="debug")

    # ------------------------------------------------------------------
    # Settings memory management
    # ------------------------------------------------------------------
    def save_settings(self, name: str) -> None:
        if self.device is None:
            raise ConnectionError("Device not connected.")
        self.write(f":MMEM:STOR:STAT '{name}'")
        self.log(f"Saved settings '{name}'", level="info")

    def recall_settings(self, name: str) -> None:
        if self.device is None:
            raise ConnectionError("Device not connected.")
        self.write(f":MMEM:RECall:STAT '{name}'")
        self.log(f"Recalled settings '{name}'", level="info")
        
    # ------------------------------------------------------------------
    # Verification (implemented as requested)
    # ------------------------------------------------------------------
    def _verify_channel(
        self,
        channel: int,
        expected: Dict[str, Optional[Any]],
    ) -> None:
        """
        Verify programmed parameters by exact (non-tolerant) matching.

        - Numeric values are compared after float conversion (exact).
        - String values are compared case-insensitively.
        """
        actual = self.read_setpoints(channel)

        for key, expected_val in expected.items():
            if expected_val is None:
                continue

            if key not in actual:
                raise KeyError(f"Cannot verify unknown key '{key}'.")

            actual_val = actual[key]

            if isinstance(expected_val, (int, float)) and isinstance(actual_val, (int, float)):
                a = float(actual_val)
                e = float(expected_val)
                if a != e:
                    raise RuntimeError(f"Verify failed CH{channel}: {key}: actual={a}, expected={e}")
            else:
                a = str(actual_val).strip().upper()
                e = str(expected_val).strip().upper()
                if a != e:
                    raise RuntimeError(
                        f"Verify failed CH{channel}: {key}: actual={actual_val!r}, expected={expected_val!r}"
                    )
        
    # ------------------------------------------------------------------
    # Burst helpers (refactored)
    # ------------------------------------------------------------------
    def _arm_manual(self, channel: int) -> None:
        self.write(f":ARM:EWID:STAT{channel} OFF")
        self.write(f":ARM:SOUR{channel} MAN")
        self.write(f":ARM:SENS{channel} EDGE")
        self.write(f":ARM:SLOP{channel} POS")
        self.write(f":TRIG{channel}:SOUR IMM")
        self.write(f":TRIG{channel}:SLOP POS")
        self.write(f":DIG{channel}:PATT OFF")

    def _program_period_if_given(self, channel: int, period: Optional[float]) -> None:
        if period is None:
            return
        self.write(f":PULS:PER{channel} {self.format_scpi_number(period)}")

    def _program_trigger_count(self, channel: int, count: int) -> None:
        self.write(f":TRIG{channel}:COUN {count}")

    def _estimate_duration(self, count: int, period: Optional[float]) -> float:
        if period is None:
            return 0.0
        return max(float(count) * float(period), 0.0)

    def _sleep_with_progress(self, duration: float, desc: str) -> None:
        if duration <= 0.0:
            return
        if duration < 0.1:
            time.sleep(duration)
            return

        fmt = "{l_bar}{bar}| {n:.2f}/{total_fmt}s [{elapsed}]"
        with tqdm(total=duration, unit="s", desc=desc, bar_format=fmt) as pbar:
            start = time.time()
            while True:
                elapsed = time.time() - start
                if elapsed >= duration:
                    pbar.n = duration
                    pbar.refresh()
                    break
                pbar.n = elapsed
                pbar.refresh()
                time.sleep(0.1)

    def _execute_burst_segments(
        self,
        channel: int,
        total_count: int,
        *,
        period: Optional[float],
        progress_prefix: str,
    ) -> None:
        remaining = total_count

        while remaining > 0:
            ncyc = min(remaining, self.MAX_COUNTER)
            remaining -= ncyc

            self._program_trigger_count(channel, ncyc)
            self.write("*TRG")

            duration = self._estimate_duration(ncyc, period)
            desc = f"{progress_prefix} (n={ncyc}, remaining={remaining})"
            self._sleep_with_progress(duration, desc=desc)

            self.wait_for_completion()
        
    # ------------------------------------------------------------------
    # Public burst APIs
    # ------------------------------------------------------------------
    def burst_pulse(
        self,
        channel: int,
        burst_count: int,
        *,
        period: Optional[float] = None,
    ) -> None:
        """
        Host-controlled burst using TRIG:COUN segmentation if needed.
        """
        if self.device is None:
            raise ConnectionError("Device not connected.")
        if channel not in (1, 2):
            raise ValueError("Channel must be 1 or 2.")
        if burst_count < 1:
            raise ValueError("burst_count must be >= 1.")
        if period is None:
            raise ValueError("period must be provided for burst timing/progress.")

        self.enable_channel(channel, False)
        self._arm_manual(channel)
        self._program_period_if_given(channel, period)

        # In host-triggered burst mode, keep one pulse per accepted trigger and
        # let TRIG:COUN define the total pulse count for each segment.
        self.set_burst(channel, 1)

        self.enable_channel(channel, True)
        self._execute_burst_segments(
            channel,
            burst_count,
            period=period,
            progress_prefix=f"CH{channel} burst",
        )

    def math_burst_pulse(
        self,
        burst_count: int,
        *,
        extra_delay_ch2: float,
        period: Optional[float] = None,
        rollback: bool = True,
    ) -> None:
        """
        Math burst (CH1+CH2).

        Steps:
        - turn both outputs OFF
        - enable burst on both channels
        - compute CH2 delay = (CH1 delay + CH1 width + CH1 fall + extra_delay_ch2)
        - enable math mode PLUS
        - enable outputs
        - trigger segmented burst
        - rollback CH2 delay + math mode (if rollback=True)
        """
        if self.device is None:
            raise ConnectionError("Device not connected.")
        if burst_count < 1:
            raise ValueError("burst_count must be >= 1.")
        if period is None:
            raise ValueError("period must be provided for burst timing/progress.")

        # Disable outputs for safety during reconfiguration
        self.enable_channel(1, False)
        self.enable_channel(2, False)

        # Base readback
        base_ch1 = self.read_setpoints(1)
        base_ch2 = self.read_setpoints(2)

        # Keep one pulse per accepted trigger and use TRIG:COUN for total count.
        self.set_burst(1, 1)
        self.set_burst(2, 1)
        self._program_period_if_given(1, period)
        self._program_period_if_given(2, period)

        # Compute temporary CH2 delay relative to CH1 edges
        try:
            temporary_delay_ch2 = (
                float(base_ch1["delay"])
                + float(base_ch2["delay"])
                + float(base_ch1["width"])
                + float(base_ch1["fall"])
                + float(extra_delay_ch2)
            )
        except Exception as e:
            raise RuntimeError(f"Failed to compute temporary CH2 delay: {e}") from e

        # Apply CH2 delay (preserves aliasing pipeline)
        self.set_channel(2, {"delay": temporary_delay_ch2})
        if self.debug_mode:
            self.log(
                f"Math burst CH2 delay -> ch1.delay({base_ch1['delay']}) + "
                f"ch2.delay({base_ch2['delay']}) + ch1.width({base_ch1['width']}) + "
                f"ch1.fall({base_ch1['fall']}) + extra({extra_delay_ch2}) = {temporary_delay_ch2}",
                level="debug",
            )

        try:
            self.set_math_mode(True)
            self.enable_channel(1, True)
            self.enable_channel(2, True)

            # Program both trigger counters per segment; trigger once (*TRG) for both.
            remaining = burst_count
            while remaining > 0:
                ncyc = min(remaining, self.MAX_COUNTER)
                remaining -= ncyc

                self._program_trigger_count(1, ncyc)
                self._program_trigger_count(2, ncyc)
                if self.debug_mode:
                    self.log(f"Math burst segment armed with TRIG:COUN={ncyc} on CH1/CH2", level="debug")
                self.write("*TRG")

                duration = self._estimate_duration(ncyc, period)
                self._sleep_with_progress(duration, desc=f"Math burst (n={ncyc}, remaining={remaining})")
                self.wait_for_completion()

        finally:
            if rollback:
                # Restore CH2 original settings and disable math mode
                self.set_channel(2, base_ch2)
                self.set_math_mode(False)
                self.log("Rolled back CH2 settings and disabled math mode", level="info")
            else:
                self.log("Rollback disabled; leaving math mode/settings as-is", level="warning")

    def run_pulse_recipe(self, recipe: Dict[str, Any]) -> None:
        """
        Execute a pulse operation from a small recipe mapping.

        Supported modes:
        - burst: single-channel burst_pulse()
        - math_burst: CH1+CH2 math_burst_pulse()
        - trigger: plain *TRG after optional channel reconfiguration
        """
        if self.device is None:
            raise ConnectionError("Device not connected.")

        if not isinstance(recipe, dict):
            raise ValueError("recipe must be a dict.")

        mode = str(recipe.get("mode", "burst") or "burst").strip().lower()
        channel = int(recipe.get("channel", 1))

        # Reconfigure levels only while outputs are disabled; otherwise the
        # instrument can momentarily expose mixed old/new HIGH/LOW states.
        if mode == "math_burst":
            self.enable_channel(1, False)
            self.enable_channel(2, False)
        else:
            if channel not in (1, 2):
                raise ValueError("Channel must be 1 or 2.")
            self.enable_channel(channel, False)

        ch1_settings = recipe.get("channel_1_settings")
        if isinstance(ch1_settings, dict) and ch1_settings:
            self.set_channel(1, ch1_settings)

        ch2_settings = recipe.get("channel_2_settings")
        if isinstance(ch2_settings, dict) and ch2_settings:
            self.set_channel(2, ch2_settings)

        enable_ch1 = recipe.get("enable_channel_1")
        if enable_ch1 is not None:
            self.enable_channel(1, bool(enable_ch1))

        enable_ch2 = recipe.get("enable_channel_2")
        if enable_ch2 is not None:
            self.enable_channel(2, bool(enable_ch2))

        settle_s = float(recipe.get("settle_s", 0.0) or 0.0)
        if settle_s > 0.0:
            time.sleep(settle_s)

        if mode == "burst":
            burst_count = int(recipe["burst_count"])
            period = recipe.get("period")
            if period is None:
                raise ValueError("burst mode requires 'period'.")
            self.burst_pulse(channel=channel, burst_count=burst_count, period=float(period))
            return

        if mode == "math_burst":
            burst_count = int(recipe["burst_count"])
            period = recipe.get("period")
            if period is None:
                raise ValueError("math_burst mode requires 'period'.")
            extra_delay_ch2 = float(recipe.get("extra_delay_ch2", 0.0) or 0.0)
            rollback = bool(recipe.get("rollback", True))
            self.math_burst_pulse(
                burst_count=burst_count,
                extra_delay_ch2=extra_delay_ch2,
                period=float(period),
                rollback=rollback,
            )
            return

        if mode == "trigger":
            self.trigger_pulse()
            return

        raise ValueError(
            f"Unsupported pulse recipe mode '{mode}'. "
            "Use 'burst', 'math_burst', or 'trigger'."
        )
        
# ----------------------------------------------------------------------
# Example usage (run this file directly to test)
# ----------------------------------------------------------------------
if __name__ == "__main__":

    # CHANGE THIS address (or alias) to match your instrument!
    VISA_ADDRESS = "PulseGen"

    pg: Optional[PulseGenerator81110A] = None
    try:
        # --------------------------------------------------------------
        # 0. Create the ResourceManager FIRST (important!)
        # --------------------------------------------------------------
        rm = pyvisa.ResourceManager()

        # --------------------------------------------------------------
        # 1. Create the driver (does NOT connect yet)
        # --------------------------------------------------------------
        pg = PulseGenerator81110A(rm, VISA_ADDRESS, timeout=5000, debug_mode=True)
        pg.log("--- Self-test: PulseGenerator81110A ---", level="info")
        pg.log("VISA backend loaded.", level="info")
        pg.log(f"Available resources: {rm.list_resources()}", level="info")

        # --------------------------------------------------------------
        # 2. Connect and put instrument into MAN-armed pulse mode
        # --------------------------------------------------------------
        pg.log("Connecting and initializing pulse generator...", level="info")
        pg.initialize()

        # --------------------------------------------------------------
        # 3. Program pulse parameters on CH1/CH2
        # --------------------------------------------------------------
        settings1 = {
            "period": 800e-9,    # 1 MHz
            "width":  250e-9,  # 100 ns
            "high":   4.0,
            "low":    0.0,
            "rise":   40e-9,
            "fall":   40e-9,
            # Optional:
            # "zsource": 50.0,
            # "zload":   50.0,
            # "polarity": "NORM",
        }

        settings2 = {
            "period": 800e-9,    # 1 MHz
            "width":  250e-9,  # 100 ns
            "high":   0.0,
            "low":    -4.0,
            "rise":   40e-9,
            "fall":   40e-9,
            "polarity": "INV",
            # Optional:
            # "zsource": 50.0,
            # "zload":   50.0,
        }
        
        pg.log("Programming pulse parameters on both channels...", level="info")
        pg.set_channel(1, settings1)
        pg.set_channel(2, settings2)
        

        # --------------------------------------------------------------
        # 4. Read-back verification
        # --------------------------------------------------------------
        pg.log("Reading back setpoints...", level="info")
        readback1 = pg.read_setpoints(1)
        readback2 = pg.read_setpoints(2)

        for key, val in readback1.items():
            pg.log(f"CH1 {key:>8s} = {val}", level="info")

        for key, val in readback2.items():        
            pg.log(f"CH2 {key:>8s} = {val}", level="info")

        pg.log("Verifying programmed pulse parameters...", level="info")
        pg._verify_channel(1, settings1)
        pg._verify_channel(2, settings2)

        # --------------------------------------------------------------
        # 5. Trigger one pulse
        # --------------------------------------------------------------
        # pg.log("Triggering single pulse...", level="info")
        # pg.trigger_pulse()  # *TRG
        # pg.wait_for_completion()

        # --------------------------------------------------------------
        # 6. Burst API examples
        # --------------------------------------------------------------
        pg.log("Running burst_pulse on CH1 (5 pulses)...", level="info")
        pg.burst_pulse(channel=1, burst_count=10, period=settings1["period"])

        pg.log("Running math_burst_pulse (5 pulses)...", level="info")
        pg.math_burst_pulse(
            burst_count=10,
            extra_delay_ch2=100e-9,
            period=settings1["period"],
            rollback=True,
        )

        pg.log("Self-test complete.", level="info")

    except Exception as e:
        if pg is not None:
            pg.log(f"[ERROR OCCURRED] {e}", level="error")
        else:
            raise

    finally:
        if pg is not None:
            pg.log("Disconnecting...", level="info")
            try:
                pg.disconnect()
            except Exception as e:
                pg.log(f"Disconnect failed: {e}", level="warning")
            pg.log("DONE.", level="info")
