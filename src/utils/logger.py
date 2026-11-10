"""
logger.py
----------
Shared logging utility for the entire project.

Provides a globally consistent logger instance across modules.
Each logger writes both to console and to a timestamped log file under 'logs/'.
"""
__version__ = "0.0.0"

import logging
from datetime import datetime
from pathlib import Path

# Global configuration guards
_LOGGER_INITIALIZED = False
_LOG_FILE_PATH = None


def get_logger(name: str = "main", log_dir: str | Path = "logs") -> logging.Logger:
    """
    Retrieve a configured logger for a given module or component.

    The logger writes both to the console (human-readable output)
    and to a timestamped log file located in the given `log_dir`.

    Args:
        name: Name of the logger instance (typically `__name__`).
        log_dir: Directory path where the log file will be saved.

    Returns:
        logging.Logger: Configured logger instance shared across modules.
    """
    global _LOGGER_INITIALIZED, _LOG_FILE_PATH

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if not _LOGGER_INITIALIZED:
        # Initialize only once (root logger)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        _LOG_FILE_PATH = log_dir / f"experiment_{timestamp}.log"

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_format = logging.Formatter("[%(levelname)s] %(message)s")
        console_handler.setFormatter(console_format)

        # File handler
        file_handler = logging.FileHandler(_LOG_FILE_PATH, encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_format = logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_format)

        # Root logger setup
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(console_handler)
        root_logger.addHandler(file_handler)

        _LOGGER_INITIALIZED = True
        root_logger.info(f"Logging initialized → {_LOG_FILE_PATH}")

    return logger


def get_log_file_path() -> Path | None:
    """
    Return the current active log file path, if available.

    Returns:
        Path | None: The path to the current log file or None if not yet initialized.
    """
    return _LOG_FILE_PATH
