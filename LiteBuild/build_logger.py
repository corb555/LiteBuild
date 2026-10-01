from contextlib import nullcontext
from enum import IntEnum
from pathlib import Path
import sys
from typing import Any, Callable, Optional, TextIO, Tuple, Union

from filelock import FileLock


_logger_instance = None


class LogLevel(IntEnum):
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40

LINE_SEPARATOR = "    " + "-" * 48

def initialize_file_logger_for_worker(log_file_path_str: str, log_level_name: str) -> None:
    """Create the process-local BuildLogger used by an executor worker."""
    global _logger_instance
    log_level = LogLevel[log_level_name.upper()]
    _logger_instance = BuildLogger(Path(log_file_path_str), log_level=log_level)


class BuildLogger:
    """Human-readable LiteBuild diagnostic logger.

    BuildLogger owns the text transcript only. Structured build state is emitted
    separately through the status-message protocol.

    ``log()`` writes one logical log record and terminates it with exactly one
    newline. ``write_raw()`` preserves text exactly, including CR/LF, for output
    that has already been deliberately normalized/formatted by the command
    output layer.
    """

    def __init__(
        self,
        output: Union[str, Path, TextIO, Any],
        log_level: LogLevel = LogLevel.INFO,
    ) -> None:
        self.output_target = output
        self.level = log_level
        self.is_file_based = isinstance(output, (str, Path))
        self.log_file_handle: Any
        self.lock: Any

        if self.is_file_based:
            log_file = Path(output)
            self.log_file_handle = open(log_file, "a", encoding="utf-8")
            self.lock = FileLock(log_file.with_suffix(".lock"))
        elif hasattr(output, "write") and hasattr(output, "flush"):
            self.log_file_handle = output
            self.lock = nullcontext()
        elif output is None:
            import os

            self.log_file_handle = open(os.devnull, "w", encoding="utf-8")
            self.lock = nullcontext()
        else:
            raise ValueError(f"Invalid logger output: {type(output)}")

    def log(self, message: str, level: LogLevel = LogLevel.INFO) -> None:
        """Write one human-readable log record."""
        if level < self.level:
            return

        formatted_message = f"{message.rstrip(chr(10) + chr(13))}\n"
        self._write(formatted_message)

    def write_raw(self, text: str, level: LogLevel = LogLevel.INFO) -> None:
        """Write already-formatted text without changing CR/LF characters."""
        if level < self.level or not text:
            return
        self._write(text)

    def blank(self, lines: int = 1, level: LogLevel = LogLevel.INFO) -> None:
        """Write explicit blank lines as semantic separators."""
        if lines < 1:
            raise ValueError("lines must be >= 1")
        if level < self.level:
            return
        self._write("\n" * lines)

    def _write(self, text: str) -> None:
        with self.lock:
            self.log_file_handle.write(text)
            self.log_file_handle.flush()

    def debug(self, message: str) -> None:
        self.log(message, level=LogLevel.DEBUG)

    def info(self, message: str) -> None:
        self.log(message, level=LogLevel.INFO)

    def warning(self, message: str, *, show_level: bool = True) -> None:
        """Write a warning, optionally suppressing the visual severity prefix."""
        if show_level:
            message = f"⚠️  WARNING  {message}"
        self.log(message, level=LogLevel.WARNING)

    def error(self, message: str, *, show_level: bool = True) -> None:
        """Write an error, optionally suppressing the visual severity prefix."""
        if show_level:
            message = f"❌ ERROR  {message}"
        self.log(message, level=LogLevel.ERROR)

    def get_worker_init_info(self) -> Optional[Tuple[Callable, Tuple[Any, ...]]]:
        """Return process-pool logger initialization when logging to a file."""
        if self.is_file_based:
            return initialize_file_logger_for_worker, (
                str(self.output_target),
                self.level.name,
            )
        return None


def setup_logger(logger: BuildLogger) -> None:
    """Set the process-local BuildLogger instance."""
    global _logger_instance
    _logger_instance = logger


def get_logger() -> BuildLogger:
    """Return the process-local BuildLogger."""
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = BuildLogger(sys.stdout, log_level=LogLevel.INFO)
    return _logger_instance
