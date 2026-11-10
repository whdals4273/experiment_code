"""Hardware worker and queued console output for the persistent 4T1C window.

No Tk calls belong here. The window consumes events on its own main thread.
Closing requests completion of the active command, followed by serial cleanup;
it does not interrupt a PSU operation or introduce a new firmware protocol.
"""

from __future__ import annotations

import copy
from datetime import datetime
import logging
from queue import Queue
import sys
import threading
import time
import traceback


class _ConsoleTee:
    """Mirror worker print output without capturing other threads' stdout."""

    def __init__(self, original, events, owner):
        self.original, self.events, self.owner = original, events, owner

    def write(self, text):
        if text and threading.current_thread() is self.owner:
            self.events.put(("log", text))
        if self.original is not None:
            return self.original.write(text)
        return len(text)

    def flush(self):
        if self.original is not None:
            self.original.flush()

    def __getattr__(self, name):
        return getattr(self.original, name)


class _WorkerLogHandler(logging.Handler):
    """PSU logging bypasses print, and existing handlers retain their streams."""

    def __init__(self, events, owner):
        super().__init__(logging.INFO)
        self.events, self.owner = events, owner
        self.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    def emit(self, record):
        if record.thread == self.owner.ident:
            self.events.put(("log", self.format(record) + "\n"))


class ExperimentSession:
    """Serialize commands and cleanup on one worker, reusing its serial handle."""

    def __init__(self, runner, *, port="", baud=0):
        self.runner, self.port, self.baud = runner, port, baud
        self.events = Queue()
        self._jobs = Queue()
        self._lock = threading.Lock()
        self._interrupt_requested = threading.Event()
        self._busy = self._closing = self._fatal = False
        self._exp = None
        self._run_number = 0
        self.exit_code = 0
        # Non-daemon: exiting the window must not abandon energized resources.
        self._worker = threading.Thread(target=self._work, name="4T1C experiment", daemon=False)
        self._worker.start()

    def start(self, cfg):
        """Queue an immutable-at-submission configuration, or reject overlap."""
        snapshot = copy.deepcopy(cfg)
        if self.port:
            snapshot["arduino_port"] = self.port
        if self.baud:
            snapshot["arduino_baud"] = self.baud
        with self._lock:
            if self._busy or self._closing or self._fatal:
                return False
            self._interrupt_requested.clear()
            self._busy = True
            self._jobs.put(snapshot)
        return True

    def interrupt(self):
        """Ask the worker to abort at its next I/O checkpoint and clean up."""
        with self._lock:
            if not self._busy:
                return False
            self._interrupt_requested.set()
            return True

    def close(self):
        """Request cleanup after any current command, without blocking Tk."""
        with self._lock:
            if not self._closing:
                self._closing = True
                self._jobs.put(None)

    def join(self, timeout=None):
        self._worker.join(timeout)

    def _close_experiment(self):
        if self._exp is None:
            return None
        exp, self._exp = self._exp, None
        try:
            exp.close()
        except BaseException as exc:
            print(traceback.format_exc(), end="")
            self.exit_code = 1
            return f"{type(exc).__name__}: {exc}"
        return None

    def _execute(self, cfg):
        self._run_number += 1
        run_label = f"실험 #{self._run_number:03d}"
        command_name = str(cfg.get("command_name", "READ_ACTUAL")).strip()
        started = datetime.now().astimezone()
        started_clock = time.perf_counter()
        print(f"\n========== [{run_label} 시작] {command_name} ==========\n"
              f"시작 시각: {started.isoformat(sep=' ', timespec='seconds')}\n")
        success, fatal, error = False, False, None
        outcome = "오류"
        try:
            if self._exp is None:
                self._exp = self.runner.Experiment4T1C(cfg)
            self._exp.cancel_requested = self._interrupt_requested.is_set
            success = self.runner.execute_command(self._exp, cfg)
            if not success:
                fatal = True
                error = next((line for line in reversed(self._exp.output_lines)
                              if line.startswith(("ERR", "OK ABORT"))), "Firmware command failed")
                if error.startswith("OK ABORT"):
                    outcome = "중단"
            else:
                outcome = "중단" if command_name in ("ABORT", "CMD ABORT") else "완료"
        except BaseException as exc:
            print(traceback.format_exc(), end="")
            error = f"{type(exc).__name__}: {exc}"
            # Once a CMD may have been sent, the firmware could still be in an
            # OPC wait. Do not blindly issue another command into that state.
            fatal = (isinstance(exc, KeyboardInterrupt) or self._exp is not None and
                     (self._exp.command_sent_at is not None or self._exp.cleanup_failed))
            self.exit_code = 130 if isinstance(exc, KeyboardInterrupt) else 1
            if isinstance(exc, KeyboardInterrupt):
                outcome = "중단"
        else:
            self.exit_code = 0 if success else 1
        # Capture before fatal cleanup retires the experiment, including any
        # saved partial plot. Never discover plots by scanning older run files.
        result = self._exp.result if self._exp is not None else None
        if fatal:
            cleanup_error = self._close_experiment()
            if cleanup_error:
                error = f"{error}; cleanup: {cleanup_error}"
                outcome = "오류"
        finished = datetime.now().astimezone()
        elapsed = time.perf_counter() - started_clock
        print(f"\n========== [{run_label} 종료] {command_name} | {outcome} ==========\n"
              f"종료 시각: {finished.isoformat(sep=' ', timespec='seconds')}\n"
              f"총 소요 시간: {elapsed:.3f}초 (연결·장비 정리·결과 저장 포함)\n"
              f"{'=' * 64}\n")
        with self._lock:
            self._busy = False
            self._fatal = fatal
        self.events.put(("done", {"success": success, "error": error, "fatal": fatal,
                                  "result": result, "command_name": command_name,
                                  "run_number": self._run_number}))

    def _work(self):
        original = sys.stdout
        tee = _ConsoleTee(original, self.events, threading.current_thread())
        handler = _WorkerLogHandler(self.events, threading.current_thread())
        root_logger = logging.getLogger()
        sys.stdout = tee
        root_logger.addHandler(handler)
        close_error = None
        try:
            while True:
                cfg = self._jobs.get()
                if cfg is None:
                    break
                self._execute(cfg)
        finally:
            close_error = self._close_experiment()
            root_logger.removeHandler(handler)
            handler.close()
            if sys.stdout is tee:
                sys.stdout = original
            self.events.put(("closed", {"error": close_error}))
