# build_logger.py

from contextlib import nullcontext
from enum import IntEnum # <-- ADDED
from pathlib import Path
import sys
from typing import Optional, Tuple, Callable, Any, Union, TextIO

from filelock import FileLock

# Global instance for each process to hold its logger.
_logger_instance = None

# --- Define log levels for filtering ---
class LogLevel(IntEnum):
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40

def initialize_file_logger_for_worker(log_file_path_str: str, log_level_name: str):
    """Creates and sets up a BuildLogger instance in a new process."""
    global _logger_instance
    log_level = LogLevel[log_level_name.upper()]
    _logger_instance = BuildLogger(Path(log_file_path_str), log_level=log_level)


class BuildLogger:
    """A unified logger that supports levels and writes to a file or stream."""
    def __init__(self, output: Union[str, Path, Any], log_level: LogLevel = LogLevel.INFO):
        """
        Initializes the logger.

        Args:
            output: A file path or a text stream object.
            log_level: The minimum level of messages to record.
        """
        self.output_target = output
        self.level = log_level # Store the configured level
        self.is_file_based = isinstance(output, (str, Path))
        self.log_file_handle: Any
        self.lock = None

        if self.is_file_based:
            log_file = Path(output)
            # Use 'a' to append
            self.log_file_handle = open(log_file, 'a', encoding='utf-8')
            self.lock = FileLock(log_file.with_suffix(".lock"))

        # --- FIX: Duck typing check instead of strict isinstance(TextIO) ---
        elif hasattr(output, 'write') and hasattr(output, 'flush'):
            self.log_file_handle = output
            self.lock = nullcontext()
        else:
            # Fallback for None or invalid stdout in GUI apps
            # If we really can't write, point to a dummy object to prevent crashes
            if output is None:
                import os
                self.log_file_handle = open(os.devnull, 'w')
                self.lock = nullcontext()
            else:
                raise ValueError(f"Invalid Logger output: {type(output)}")

    def log(self, message: str, level: LogLevel = LogLevel.INFO):
        """
        Writes a message to the output if its level is sufficient.
        This is the core dispatcher for all logging methods.
        """
        # --- Filtering logic ---
        if level < self.level:
            return # Skip messages below the configured threshold

        formatted_message = f"{message}\n"
        with self.lock:
            self.log_file_handle.write(formatted_message)
            self.log_file_handle.flush()

    # --- Level-specific helper methods ---
    def debug(self, message: str):
        """Logs a message with DEBUG level."""
        self.log(message, level=LogLevel.DEBUG)

    def info(self, message: str):
        """Logs a message with INFO level."""
        self.log(message, level=LogLevel.INFO)

    def warning(self, message: str):
        """Logs a message with WARNING level."""
        self.log(f"⚠️  WARNING: {message}", level=LogLevel.WARNING)

    def error(self, message: str):
        """Logs a message with ERROR level."""
        self.log(f"❌ ERROR: {message}", level=LogLevel.ERROR)


    def get_worker_init_info(self) -> Optional[Tuple[Callable, Tuple[Any, ...]]]:
        """
        Returns worker initialization info ONLY if this is a file-based logger.
        """
        if self.is_file_based:
            # ---  Pass the log level name to the worker ---
            return initialize_file_logger_for_worker, (str(self.output_target), self.level.name)
        return None


def setup_logger(logger: BuildLogger):
    """Initializes the singleton BuildLogger for the current process."""
    global _logger_instance
    _logger_instance = logger


def get_logger() -> BuildLogger:
    """Retrieves the current process's logger instance."""
    global _logger_instance
    if _logger_instance is None:
        # Default to INFO level if not explicitly set up
        _logger_instance = BuildLogger(sys.stdout, log_level=LogLevel.INFO)
    return _logger_instance