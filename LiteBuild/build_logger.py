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
        self.lock_file: Optional[Path] = None
        self._closed = False

        if self.is_file_based:
            log_file = Path(output)
            self.log_file_handle = open(log_file, "a", encoding="utf-8")
            self.lock_file = log_file.with_name(f"._{log_file.name}.lock")
            self.lock = FileLock(self.lock_file)
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

    def close(self) -> None:
        """Close the file logger and remove its lock file.

        The owning application should call this only after all worker processes that
        share the log file have stopped. The lock-file name is validated before
        deletion as a safeguard against removing an unrelated file.
        """
        if self._closed:
            return

        if not self.is_file_based:
            self._closed = True
            return

        with self.lock:
            if not self.log_file_handle.closed:
                self.log_file_handle.close()

        if self.lock_file is None:
            raise RuntimeError("File-based logger has no lock-file path")

        lock_name = self.lock_file.name
        if not (lock_name.startswith("._") and lock_name.endswith(".lock")):
            raise RuntimeError(
                f"Refusing to delete unexpected logger lock file: {self.lock_file}"
            )

        try:
            self.lock_file.unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"Unable to remove logger lock file '{self.lock_file}': {exc}"
            ) from exc

        self._closed = True

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
