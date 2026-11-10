__version__ = "0.1.0"

from typing import Optional
import warnings
import time

import pyvisa as visa

try:
    from tqdm import tqdm  # type: ignore
except ModuleNotFoundError:  # graceful fallback
    class _TqdmFallback:
        def __init__(self, iterable=None, total=None, desc=None, **kwargs):
            self.iterable = iterable
            self.total = total
            self.desc = desc

        def __iter__(self):
            return iter(self.iterable or [])

        def update(self, _n=1):
            return None

        def close(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def tqdm(iterable=None, *args, **kwargs):  # type: ignore
        return _TqdmFallback(iterable=iterable, **kwargs)

from src.utils.logger import get_logger


class MeasurementDevice:
    """
    Base class for measurement devices.
    """

    def __init__(
        self,
        resource_manager: visa.ResourceManager,
        resource_name: str,
        timeout: int = 1000,
        debug_mode: bool = False,
    ) -> None:
        """
        MeasurementDevice class constructor: use pre-constructed ResourceManager instance and
        resource name to initialize specific measurement device.

        Args:
            resource_manager (visa.ResourceManager): Pre-constructed (global) ResourceManager instance
            resource_name (str): Resource name of device (alias) to be connected
            timeout (int, optional): Communication timeout in milliseconds. Defaults to 1_000 (1 second).
            debug_mode (bool, optional): on/off debug log message print. Defaults to False.
        """
        self.resource_name = resource_name
        self.rm = resource_manager
        self.device = None

        self.debug_mode = debug_mode
        self.timeout = timeout

        self.MAX_RETRIES = 3
        self.TOLERANCE = 5e-3

        logger_name = f"{self.__class__.__name__}({resource_name})"
        self.logger = get_logger(name=f"{logger_name} (v{__version__})")

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    def connect(self) -> None:
        """
        connect to device method (open communication channel)
        log open resource success or failure message
        """
        try:
            self.device = self.rm.open_resource(self.resource_name)
            self.device.timeout = self.timeout
            self.log("Connected to device", level="info")
        except Exception:
            self.log("Connection failure", level="error")
            raise

    def disconnect(self, kill_rm: bool = False) -> None:
        """
        disconnect from device method (close communication channel)
        Close the instrument connection, and optionally kill the ResourceManager instance as well.

        Args:
            kill_rm (bool, optional): Close the global ResourceManager instance. Defaults to False.
                                      Advised to keep this False if multiple devices are used.
        """
        if self.device is not None:
            try:
                self.device.close()
                self.log("Disconnected from device", level="info")
            except Exception:
                self.log("Error during disconnect", level="error")
            finally:
                self.device = None
        else:
            self.log("Disconnect called but device was not connected", level="warning")

        if kill_rm:
            try:
                self.rm.close()
            except Exception:
                self.log("Failed to close ResourceManager", level="error")

    # ------------------------------------------------------------------
    # Low-level IO
    # ------------------------------------------------------------------
    # NOTE: SCPI-based commands compose of three actions: write, read, query
    #     * write: send command without expecting response
    #     * read: read response in buffer without sending command      
    #     * query = write + read (Usually the command ends with '?')
    def query(self, command: str) -> Optional[str]:
        """
        Send query command and return the response from instrument.
        Returns instrument response string if successful, None returned if failed.

        Args:
            command (str): Command to be queried.

        Returns:
            Optional[str]: Response from instrument if successful, None otherwise.
        """
        if self.device is None:
            self.log("Query attempted while device not connected", level="error")
            return None

        try:
            response = self.device.query(command)
            if self.debug_mode:
                self.log(
                    f"QUERY → {command}",
                    level="debug"
                )
                self.log(
                    f"RESPONSE ← {response.strip()}",
                    level="debug"
                )
            return response
        except Exception:
            self.log(
                f"Query failed → {command}",
                level="error"
            )
            return None

    def write(self, command: str) -> None:
        """
        Send write command to instrument.
        Write commands do not expect a response from the instrument.
        
        Args:
            command (str): Command to be written to device

        Returns:
            None (Write command does not expect response from instrument)
        """
        if self.device is None:
            self.log(
                "Write attempted while device not connected",
                level="error"
            )   
            return

        try:
            self.device.write(command)
            if self.debug_mode:
                self.log(
                    f"WRITE → {command}",
                    level="debug"
                )
        except Exception:
            self.log(
                f"Write failed → {command}",
                level="error"
            )

    def read(self) -> Optional[str]:
        """
        Read response from instrument.
        Returns instrument response string if successful, None returned if failed.
        
        Returns:
            Optional[str]: response from device and if failed, then no response(optional none)
        """
        if self.device is None:
            self.log(
                "Read attempted while device not connected",
                level="error"
            )
            return None

        try:
            data = self.device.read()
            if self.debug_mode:
                self.log(
                    f"READ ← {data.strip()}",
                    level="debug"
                )
            return data
        except Exception:
            self.log(
                "Read failed",
                level="error"
            )
            return None

    # ------------------------------------------------------------------
    # Higher-level helpers
    # ------------------------------------------------------------------
    def idn_query(self) -> Optional[str]:
        """
        Queries the instrument for its identification string using the *IDN? command.
        Returns the ID string if successful, None otherwise.
        NOTE: This does not work if the instrument does not support the SCPI standard.
        NOTE: Instrument has to be connected using the self.connect() method before calling this method.
        
        Returns:
            Optional[str]: Instrument ID string if successful, None otherwise.
        """
        if self.device is None:
            self.log(
                "IDN query attempted while device not connected",
                level="error"
            )
            return None

        resp = self.query("*IDN?")
        if resp is None:
            return None

        if self.debug_mode:
            self.log(
                f"IDN RESPONSE ← {resp.strip()}",
                level="debug"
            )
        return resp.strip()

    def write_and_verify(
            self,
            write_cmd: str,
            query_cmd: str,
            expected: str | float | int,
            retries: Optional[int] = None,
            tolerance: Optional[float] = None,
        ) -> bool:
        """
        Write a command to the device and verify the result by querying the device in succession.
        Supports retries, and tolerance for floating point set value comparisons.
        Returns True if verification succeeds within the allowed retries, False otherwise.
        
        Args:
            write_cmd (str): Command to be written to the device.
            query_cmd (str): Query command that retrieves the value to be verified.
            expected (str | float | int): The expected value. Can be a string, float, or int.
            retries (Optional[int], optional): The number of retries. Defaults to None.
            tolerance (Optional[float], optional): The tolerance for floating point comparisons. Defaults to None.

        Returns:
            bool: True if the verification succeeds, False otherwise.
        """
        if retries is None:
            retries = self.MAX_RETRIES

        last_resp: Optional[str] = None

        for _ in range(retries):
            self.write(write_cmd)
            resp = self.query(query_cmd)

            if resp is None:
                time.sleep(0.05)
                continue

            resp = resp.strip()
            last_resp = resp

            try:
                exp_val = float(expected)
                act_val = float(resp)
                tol = tolerance if tolerance is not None else self.TOLERANCE
                if abs(act_val - exp_val) <= tol:
                    return True
            except (ValueError, TypeError):
                if resp == expected or resp in expected:
                    return True

            time.sleep(0.05)

        self.log(
            f"Failed to verify '{query_cmd}' == '{expected}' "
            f"after {retries} attempts (last response: '{last_resp}')",
            level="warning"
        )
        return False

    def wait_for_completion(self, timeout: Optional[float] = None) -> bool:
        """
        Wait for the instrument to signal operation completion using the *OPC? command.
        This method is required for the host PC to wait until the instrument has completed its measurement.
        NOTE: To use this method, the instrument must support the SCPI standard.
        NOTE: This method uses infinite loop for polling completion.
        * In order to avoid sending repetitive *OPC? queries too much, query is separated into a 
        * single write (query command) followed by multiple reads in a loop.

        Args:
            timeout (Optional[float], optional): Optionally change the timeout value
                                                 within the infinite looping phase. Defaults to None.

        Returns:
            bool: True if the operation completes successfully, 
                  False if a timeout occurs or an error is encountered.
        """
        SLEEP_INTERVAL = 0.05
        start_time = time.time()

        original_timeout = self.device.timeout if self.device else None
        if timeout is not None and self.device is not None:
            self.device.timeout = int(timeout * 1000)

        self.log(
            "Waiting for Operation Complete (*OPC)",
            level="info"
        )

        try:
            self.write("*OPC?")
        except Exception:
            self.log(
                "Failed to send *OPC?",
                level="error"
            )
            return False

        try:
            while True:
                resp = self.read()
                if resp is not None and resp.strip() == "1":
                    self.log(
                        "Operation complete",
                        level="info"
                    )
                    return True

                if timeout is not None and (time.time() - start_time) > timeout:
                    self.log(
                        "Timeout waiting for *OPC",
                        level="warning"
                    )
                    return False

                time.sleep(SLEEP_INTERVAL)
        finally:
            if original_timeout is not None and self.device is not None:
                self.device.timeout = original_timeout

    # ------------------------------------------------------------------
    # Utils
    # ------------------------------------------------------------------
    def log(
        self,
        message: str,
        level: str = "info",
        *,
        width: int = 20,
    ) -> None:
        """
        Unified logging method with resource name alignment. Uses a global logger instance, with 
        instance specific resource name prepended to each log message for clarity.

        Args:
            message (str): Message to be logged.
            level (str, optional): Log level, one of {"debug", "info", "warning", "error", "critical"}.
                                   Defaults to "info".
            width (int, optional): Fixed width for resource-name field (for alignment).
                                   Defaults to 20.

        Returns:
            None
        """

        # Normalize and validate level
        level = level.lower()
        log_fn = {
            "debug": self.logger.debug,
            "info": self.logger.info,
            "warning": self.logger.warning,
            "error": self.logger.error,
            "critical": self.logger.critical,
        }.get(level, self.logger.info)

        # Format resource name (truncate or pad)
        res = self.resource_name
        if len(res) > width:
            res = res[: width - 1] + "…"
        else:
            res = res.ljust(width)

        # Final aligned message
        formatted = f"[{res}] | {message}"

        log_fn(formatted)
        
        return None

    def _warn_deprecated(self, name: str, replacement: str) -> None:
        msg = f"{name} is deprecated and will be removed in a future release. Use {replacement}."
        warnings.warn(msg, DeprecationWarning, stacklevel=3)
        self.log(msg, level="warning")

    # ------------------------------------------------------------------
    # Progress helper
    # ------------------------------------------------------------------
    def progress(
        self,
        iterable=None,
        *,
        total: Optional[int] = None,
        desc: Optional[str] = None,
        **kwargs,
    ):
        """
        Convenience wrapper around tqdm for consistent progress bars.

        Example:
            for x in self.progress(range(100), desc="sweep"):
                ...
        """
        if desc is None:
            desc = f"{self.__class__.__name__}:{self.resource_name}"
        return tqdm(iterable, total=total, desc=desc, **kwargs)
    
    @staticmethod
    def format_scpi_number(
        val: float | int | str,
        *,
        sigfigs: int = 3,
    ) -> str:
        """
        format_scpi_number method : format a number for SCPI commands.
        depending on the type of val, format as follows:        

        - str → passed through unchanged
        - int → decimal string
        - float → normalized scientific notation (NR3, 3 sig figs)

        Args:
            val (float | int | str): value to format
            sigfigs (int, optional): number of significant figures. Defaults to 3.

        Returns:
            str: formatted string
        """
        if val is None:
            raise TypeError("format_scpi_number() received None")
        if isinstance(val, str):
            return val
        if isinstance(val, int):
            return str(val)
        return f"{float(val):.{sigfigs}E}"
