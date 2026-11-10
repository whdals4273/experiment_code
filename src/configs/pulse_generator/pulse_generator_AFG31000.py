from __future__ import annotations
__version__ = "0.0.0"

import warnings
from typing import Dict, Any, Optional

import pyvisa

from base_pulse_generator import BasePulseGenerator


class PulseGeneratorAFG31000(BasePulseGenerator):
    """
    SCPI driver for **Tektronix AFG31000-series** arbitrary/function generators
    operated in *pulse* mode.

    This class implements all abstract methods from :class:`BasePulseGenerator`:

    - Connection:
      - ``connect()``
      - ``disconnect()``

    - Per-channel configuration:
      - ``set_channel(channel, settings)``
      - ``read_setpoint(channel)``
      - ``enable_channel(channel, onoff)``

    - Timing / sequencing:
      - ``set_trigger(trigger_source)``
      - ``set_burst(channel, burst)``

    - Utility:
      - ``save_settings(name)``
      - ``recall_settings(name)``
      - ``trigger_pulse()``
      - ``_verify(channel, expected)``

    Notes
    -----
    * Waveform shape is forced to **PULSe** when calling :meth:`set_channel`.
      SCPI: ``[SOURce[1|2]]:FUNCtion:SHAPe PULSe``. 
    * Pulse parameters use the AFG31000 pulse group:

      - Period: ``SOURce{ch}:PULSe:PERiod``   
      - Width:  ``SOURce{ch}:PULSe:WIDTh``   
      - Delay:  ``SOURce{ch}:PULSe:DELay``   
      - Rise:   ``SOURce{ch}:PULSe:TRANsition:LEADing``   
      - Fall:   ``SOURce{ch}:PULSe:TRANsition:TRAiling``   

    * High/low levels are set via:

      - High: ``SOURce{ch}:VOLTage:LEVel:IMMediate:HIGH``   
      - Low:  ``SOURce{ch}:VOLTage:LEVel:IMMediate:LOW`` (can be negative)   

    * Burst uses the dedicated burst/run-mode group:

      - Run mode: ``SOURce{ch}:RMOD {CONTinuous|BURSt|...}``   
      - Burst count: ``SOURce{ch}:BURSt:NCYCles``   
      - Burst mode: ``SOURce{ch}:BURSt:MODE {TRIGgered|GATed}``   
      - Burst state: ``SOURce{ch}:BURSt:STATe {ON|OFF}``   

    * Triggering is controlled via the Trigger group:

      - Source: ``TRIGger:SEQuence:SOURce {TIMer|EXTernal}``   
      - Manual trigger: ``TRIGger:SEQuence:IMMediate`` or ``*TRG``   

    * Output enable/disable uses:

      - ``OUTPut{ch}:STATe {ON|OFF}`` 
    """

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------
    def connect(self) -> None:
        """
        Open VISA session, verify identity, and perform a reset/clear.

        - Opens a :class:`pyvisa.ResourceManager`
        - Opens the instrument resource given by ``self.resource_name``
        - Sets the VISA timeout to ``self._default_timeout``
        - Issues ``*RST; *CLS`` (factory reset + clear status)
        """
        self.rm = pyvisa.ResourceManager()
        self.device = self.rm.open_resource(self.resource_name)
        self.device.timeout = self._default_timeout

        idn = self.query("*IDN?").strip()
        if "AFG3" not in idn and "AFG31" not in idn:
            warnings.warn(f"Connected to unexpected device: {idn}")
        else:
            print(f"Connected to {idn}")

        # Reset & clear status
        self.write("*RST; *CLS")

    def disconnect(self) -> None:
        """
        Disable both outputs and close the VISA session.
        """
        if self.device is not None:
            # Turn outputs off for safety
            self.write("OUTPut1:STATe OFF")
            self.write("OUTPut2:STATe OFF")
            self.device.close()
            self.device = None

        if self.rm is not None:
            self.rm.close()
            self.rm = None

        print("AFG31000 disconnected.")

    # ------------------------------------------------------------------
    # Per-channel programming
    # ------------------------------------------------------------------
    def set_channel(self,
                    channel: int,
                    settings: Dict[str, Any]) -> None:
        """
        Configure pulse parameters on a given channel.

        Parameters
        ----------
        channel : int
            Channel index (1 or 2).
        settings : dict
            Mapping from *setting name* → value. Keys are case/whitespace
            insensitive and may use common aliases. Recognized logical keys:

            - ``period`` : Pulse period (seconds)
              → ``SOURce{ch}:PULSe:PERiod`` 
            - ``width``  : Pulse width (seconds)
              → ``SOURce{ch}:PULSe:WIDTh`` 
            - ``delay``  : Leading delay (seconds)
              → ``SOURce{ch}:PULSe:DELay`` 
            - ``high``   : High level (V)
              → ``SOURce{ch}:VOLTage:LEVel:IMMediate:HIGH`` 
            - ``low``    : Low level (V)
              → ``SOURce{ch}:VOLTage:LEVel:IMMediate:LOW`` 
            - ``rise``   : Leading edge time (s)
              → ``SOURce{ch}:PULSe:TRANsition:LEADing`` 
            - ``fall``   : Trailing edge time (s)
              → ``SOURce{ch}:PULSe:TRANsition:TRAiling`` 

        Aliases
        -------
        The following aliases are accepted (all normalized internally):

        - period: ``["period", "per", "tperiod"]``
        - width : ``["width", "wid", "pulsewidth", "pw"]``
        - delay : ``["delay", "del"]``
        - high  : ``["high", "vhigh", "vih", "hi"]``
        - low   : ``["low", "vlow", "vil", "lo"]``
        - rise  : ``["rise", "trise", "t_rise", "leading"]``
        - fall  : ``["fall", "tfall", "t_fall", "trailing"]``
        """
        if self.device is None:
            raise ConnectionError("Device not connected.")

        if channel not in (1, 2):
            raise ValueError("Channel must be 1 or 2.")

        # Ensure waveform type is PULSE
        self.write(f"SOURce{channel}:FUNCtion:SHAPe PULSe")

        # Normalise keys (strip whitespace, lower case)
        norm_settings = {
            str(key).strip().lower(): val
            for key, val in settings.items()
        }

        # Canonical key aliases
        aliases = {
            "period": ["period", "per", "tperiod"],
            "width":  ["width", "wid", "pulsewidth", "pw"],
            "delay":  ["delay", "del"],
            "high":   ["high", "vhigh", "vih", "hi"],
            "low":    ["low", "vlow", "vil", "lo"],
            "rise":   ["rise", "trise", "t_rise", "leading"],
            "fall":   ["fall", "tfall", "t_fall", "trailing"],
        }

        # Reverse lookup: alias → canonical
        key_lookup: Dict[str, str] = {}
        for canonical, names in aliases.items():
            for name in names:
                key_lookup[name] = canonical

        # SCPI templates
        scpi = {
            "period": f"SOURce{channel}:PULSe:PERiod {{val}}",
            "width":  f"SOURce{channel}:PULSe:WIDTh {{val}}",
            "delay":  f"SOURce{channel}:PULSe:DELay {{val}}",
            "high":   f"SOURce{channel}:VOLTage:LEVel:IMMediate:HIGH {{val}}",
            "low":    f"SOURce{channel}:VOLTage:LEVel:IMMediate:LOW {{val}}",
            "rise":   f"SOURce{channel}:PULSe:TRANsition:LEADing {{val}}",
            "fall":   f"SOURce{channel}:PULSe:TRANsition:TRAiling {{val}}",
        }

        valid_keys = sorted(set(key_lookup.values()))

        for raw_key, val in norm_settings.items():
            if raw_key not in key_lookup:
                raise ValueError(
                    f"Unsupported setting key '{raw_key}'. "
                    f"Valid logical keys: {', '.join(valid_keys)}"
                )

            canonical = key_lookup[raw_key]
            cmd = scpi[canonical].format(val=self._nr3(val))
            self.write(cmd)

    # ------------------------------------------------------------------
    def read_setpoint(self, channel: int) -> Dict[str, float]:
        """
        Read back the programmed pulse parameters on a channel.

        Parameters
        ----------
        channel : int
            Channel index (1 or 2).

        Returns
        -------
        dict
            Mapping with keys:
            ``period``, ``width``, ``delay``, ``high``, ``low``,
            ``rise``, ``fall``.
        """
        if self.device is None:
            raise ConnectionError("Device not connected.")
        if channel not in (1, 2):
            raise ValueError("Channel must be 1 or 2.")

        # Queries match the same SCPI as in set_channel
        period = float(self.query(f"SOURce{channel}:PULSe:PERiod?"))
        width  = float(self.query(f"SOURce{channel}:PULSe:WIDTh?"))
        delay  = float(self.query(f"SOURce{channel}:PULSe:DELay?"))
        high   = float(self.query(
            f"SOURce{channel}:VOLTage:LEVel:IMMediate:HIGH?"
        ))
        low    = float(self.query(
            f"SOURce{channel}:VOLTage:LEVel:IMMediate:LOW?"
        ))
        rise   = float(self.query(
            f"SOURce{channel}:PULSe:TRANsition:LEADing?"
        ))
        fall   = float(self.query(
            f"SOURce{channel}:PULSe:TRANsition:TRAiling?"
        ))

        return {
            "period": period,
            "width":  width,
            "delay":  delay,
            "high":   high,
            "low":    low,
            "rise":   rise,
            "fall":   fall,
        }

    # ------------------------------------------------------------------
    def enable_channel(self, channel: int, onoff: bool) -> None:
        """
        Enable or disable the specified output channel.

        SCPI: ``OUTPut{ch}:STATe {ON|OFF}``. 
        """
        if self.device is None:
            raise ConnectionError("Device not connected.")
        if channel not in (1, 2):
            raise ValueError("Channel must be 1 or 2.")

        self.write(f"OUTPut{channel}:STATe {'ON' if onoff else 'OFF'}")

    # ------------------------------------------------------------------
    # Math mode
    # ------------------------------------------------------------------
    def set_math_mode(self, mode: str) -> None:
        """
        Placeholder for channel-combination “math” mode.

        The AFG31000 family does **not** provide a simple `CHAN:MATH PLUS`
        style summation like the 81110A; channel interaction is handled via
        COMBine / noise feed and advanced-mode sequencing instead. 

        This method therefore raises :class:`NotImplementedError`.
        """
        raise NotImplementedError(
            "AFG31000 does not support a simple CH1+CH2 math output "
            "like the 81110A. Use advanced mode / sequencing instead."
        )

    # ------------------------------------------------------------------
    # Trigger configuration
    # ------------------------------------------------------------------
    def set_trigger(self, trigger: str) -> None:
        """
        Set the *global* trigger source used for bursts/sweep.

        Parameters
        ----------
        trigger : str
            One of:

            - ``'TIMER'`` / ``'INT'`` / ``'INTERNAL'`` → internal timer  
              → ``TRIGger:SEQuence:SOURce TIMer`` 
            - ``'EXT'`` / ``'EXTERNAL'`` → external trigger input  
              → ``TRIGger:SEQuence:SOURce EXTernal`` 
        """
        if self.device is None:
            raise ConnectionError("Device not connected.")

        key = trigger.strip().upper()
        if key in ("TIM", "TIMER", "INT", "INTERNAL"):
            src = "TIMer"
        elif key in ("EXT", "EXTERNAL"):
            src = "EXTernal"
        else:
            raise ValueError(
                "Unsupported trigger source. Use 'TIMER' or 'EXTERNAL'."
            )

        self.write(f"TRIGger:SEQuence:SOURce {src}")

    # ------------------------------------------------------------------
    # Burst mode
    # ------------------------------------------------------------------
    def set_burst(self,
                  channel: int,
                  burst: Optional[int]) -> None:
        """
        Configure pulse *burst* behaviour on a channel.

        Parameters
        ----------
        channel : int
            Channel index (1 or 2).
        burst : int or None
            If ``None``:
                - Disable burst on this channel
                - Set run mode back to ``CONTinuous``

            If an integer:
                - Enable **Triggered N-cycle burst** with that count:

                  * ``SOURce{ch}:RMOD BURSt``   
                  * ``SOURce{ch}:BURSt:MODE TRIGgered``   
                  * ``SOURce{ch}:BURSt:NCYCles <burst>``   
                  * ``SOURce{ch}:BURSt:STATe ON`` 

        Notes
        -----
        * This method **does not** start the burst; use :meth:`trigger_pulse`
          (or `TRIGger:SEQuence:IMMediate`) to fire it. 
        * Valid burst count range is given in the manual; typical range is
          ``1 … 1e6`` cycles. 
        """
        if self.device is None:
            raise ConnectionError("Device not connected.")
        if channel not in (1, 2):
            raise ValueError("Channel must be 1 or 2.")

        if burst is None:
            # Back to continuous run mode, burst off
            self.write(f"SOURce{channel}:BURSt:STATe OFF")
            self.write(f"SOURce{channel}:RMOD CONTinuous")
            return

        if burst <= 0:
            raise ValueError("Burst count must be a positive integer.")

        # Tek docs specify range (typically 1 to 1e6); we enforce a soft cap
        max_cycles = 1_000_000
        if burst > max_cycles:
            warnings.warn(
                f"Requested burst count {burst} exceeds typical max "
                f"{max_cycles}; clipping to {max_cycles}."
            )
            burst = max_cycles

        # Configure triggered N-cycle burst
        self.write(f"SOURce{channel}:RMOD BURSt")
        self.write(f"SOURce{channel}:BURSt:MODE TRIGgered")
        self.write(f"SOURce{channel}:BURSt:NCYCles {int(burst)}")
        self.write(f"SOURce{channel}:BURSt:STATe ON")

    # ------------------------------------------------------------------
    # Save / recall instrument state
    # ------------------------------------------------------------------
    def save_settings(self, name: str) -> None:
        """
        Save the **entire instrument setup** under a given name.

        The AFG31000 uses the generic SETUP save mechanism:

        - ``SAVe:SETUp "<name>"`` stores the current instrument setup. 
        """
        if self.device is None:
            raise ConnectionError("Device not connected.")

        # Double-quote to be safe with path/name
        self.write(f'SAVe:SETUp "{name}"')

    def recall_settings(self, name: str) -> None:
        """
        Recall a previously saved instrument setup.

        - ``RCL:SETUp "<name>"`` 
        """
        if self.device is None:
            raise ConnectionError("Device not connected.")

        self.write(f'RCL:SETUp "{name}"')

    # ------------------------------------------------------------------
    # Trigger pulse / burst
    # ------------------------------------------------------------------
    def trigger_pulse(self) -> None:
        """
        Force a **trigger event** for the configured trigger system.

        Equivalent to pressing the *Trigger* front-panel key when the
        trigger source is set to internal timer or external. Uses:

        - ``TRIGger:SEQuence:IMMediate`` 

        This will:
        - Start a triggered N-cycle burst (if Burst mode is enabled), or
        - Cause a sweep/other triggered event, depending on run mode.
        """
        if self.device is None:
            raise ConnectionError("Device not connected.")

        self.write("TRIGger:SEQuence:IMMediate")

    # ------------------------------------------------------------------
    # Verification helper
    # ------------------------------------------------------------------
    def _verify(self,
                channel: int,
                expected: Dict[str, Optional[float]]) -> None:
        """
        Compare read-back against expected values with appropriate tolerance.

        Parameters
        ----------
        channel : int
            Channel index (1 or 2).
        expected : dict
            Mapping from key → expected value, for example::

                {
                    'period': 1e-6,
                    'width':  4e-7,
                    'high':   1.0,
                    'low':    0.0,
                }

            Use ``None`` for entries you do **not** wish to verify.

        Behaviour
        ---------
        * Uses :meth:`read_setpoint` to obtain the current settings.
        * Uses ``self.tol_voltage`` for keys ``{"high", "low"}`` and other
          voltage-like keys, and ``self.tol_time`` for timing parameters.
        * Raises :class:`RuntimeError` if any difference exceeds tolerance.
        """
        actual = self.read_setpoint(channel)

        for key, exp_val in expected.items():
            if exp_val is None:
                continue

            if key not in actual:
                raise KeyError(f"Cannot verify unknown key '{key}'.")

            act_val = actual[key]
            err = abs(act_val - exp_val)

            # Heuristic: voltage vs. time tolerance
            if key in ("high", "low") or "volt" in key.lower():
                tol = self.tol_voltage
            else:
                tol = self.tol_time

            if err > tol:
                raise RuntimeError(
                    f"Verification failed for CH{channel}: {key} "
                    f"(actual={act_val}, expected={exp_val}, tol={tol})"
                )
