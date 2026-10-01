from __future__ import annotations

from pathlib import Path
import threading
import time
import traceback
from typing import Dict, Optional

from PySide6.QtCore import QObject, Signal

from LiteBuild.build_engine import BuildEngine
from LiteBuild.build_logger import BuildLogger, LogLevel
from LiteBuild.schema import YAMLSection
from LiteBuild.status_message import (
    BuildFinish,
    BuildStart,
    GroupFinish,
    GroupStart,
    StatusMessage,
)


_LOG_POLL_INTERVAL_S = 0.1


class _SignalStatusEmitter:
    """In-process status transport that forwards typed messages to Qt."""

    def __init__(self, signal: Signal) -> None:
        self._signal = signal

    def emit(self, message: StatusMessage) -> None:
        self._signal.emit(message)


class _GroupProfileStatusEmitter:
    """Forward profile/step status while suppressing nested build envelopes."""

    def __init__(self, emitter: _SignalStatusEmitter) -> None:
        self._emitter = emitter

    def emit(self, message: StatusMessage) -> None:
        if isinstance(message, (BuildStart, BuildFinish)):
            return
        self._emitter.emit(message)


def _tail_log_file(
    log_file: Path,
    stop_event: threading.Event,
    log_signal: Signal,
) -> None:
    """Relay appended log text to Qt while preserving CR/LF exactly.

    ``log_message`` carries raw text chunks rather than stripped logical lines.
    This preserves indentation, blank lines, and terminal-style carriage-return
    progress produced by tools such as tqdm.
    """
    position = 0

    while not stop_event.is_set():
        position = _relay_available_log_text(log_file, position, log_signal)
        stop_event.wait(_LOG_POLL_INTERVAL_S)

    # Drain anything written immediately before the build thread stopped.
    _relay_available_log_text(log_file, position, log_signal)


def _relay_available_log_text(log_file: Path, position: int, log_signal: Signal) -> int:
    """Emit newly appended text and return the new file position."""
    if not log_file.exists():
        return position

    with open(log_file, "r", encoding="utf-8", newline="") as file_obj:
        file_obj.seek(position)
        text = file_obj.read()
        new_position = file_obj.tell()

    if text:
        log_signal.emit(text)

    return new_position


def _start_log_tail(log_file: Path, log_signal: Signal) -> tuple[threading.Event, threading.Thread]:
    """Start a background log tailer for one persistent build log."""
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_tail_log_file,
        args=(log_file, stop_event, log_signal),
        daemon=True,
    )
    thread.start()
    return stop_event, thread


def _stop_log_tail(stop_event: threading.Event, thread: threading.Thread) -> None:
    """Stop the log tailer after draining the final appended text."""
    stop_event.set()
    thread.join()


def _new_log_file(label: str) -> Path:
    """Create the build log directory and return a timestamped log path."""
    log_dir = Path("build/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"litebuild_{label}_{int(time.time())}.log"


class BuildWorker(QObject):
    """Qt worker for one LiteBuild profile or explicit workflow target."""

    finished = Signal()
    error = Signal(Exception)

    # Raw text chunks from the persistent diagnostic log. CR/LF are preserved.
    log_message = Signal(str)

    # Typed objects from LiteBuild.status_message.
    status_signal = Signal(object)

    def __init__(
        self,
        config_path: str,
        profile_name: str,
        cli_vars: Optional[Dict] = None,
        step_name: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.config_path = config_path
        self.profile_name = profile_name
        self.cli_vars = cli_vars
        self.step_name = step_name

    def run(self) -> None:
        """Run one build while independently relaying text and structured status."""
        label = self.profile_name or "build"
        log_file = _new_log_file(label)
        logger = BuildLogger(log_file, log_level=LogLevel.INFO)
        status_emitter = _SignalStatusEmitter(self.status_signal)
        stop_event, tail_thread = _start_log_tail(log_file, self.log_message)

        try:
            engine = BuildEngine.from_file(self.config_path, cli_vars=self.cli_vars)
            project_cfg = engine.config[YAMLSection.PROJECT]
            final_step_name = self.step_name or project_cfg["DEFAULT_WORKFLOW_STEP"]

            success = engine.execute(
                final_step_name=final_step_name,
                profile_name=self.profile_name,
                logger=logger,
                status_emitter=status_emitter,
            )

            if not success:
                raise ValueError(
                    f"Build failed for profile '{self.profile_name}'"
                    if self.profile_name
                    else f"Build failed for target '{final_step_name}'"
                )

            self.finished.emit()

        except Exception as exc:
            logger.blank()
            logger.error(str(exc))
            #logger.error(traceback.format_exc())
            self.error.emit(exc)

        finally:
            _stop_log_tail(stop_event, tail_thread)


class BuildGroupWorker(QObject):
    """Qt worker for a sequential LiteBuild profile-group build."""

    finished = Signal()
    error = Signal(Exception)

    # Raw text chunks from the persistent diagnostic log. CR/LF are preserved.
    log_message = Signal(str)

    # Typed objects from LiteBuild.status_message.
    status_signal = Signal(object)

    def __init__(
        self,
        config_path: str,
        group_name: str,
        cli_vars: Optional[Dict] = None,
    ) -> None:
        super().__init__()
        self.config_path = config_path
        self.group_name = group_name
        self.cli_vars = cli_vars

    def run(self) -> None:
        """Run all group profiles sequentially with one text log and status stream."""
        log_file = _new_log_file(self.group_name)
        logger = BuildLogger(log_file, log_level=LogLevel.INFO)
        status_emitter = _SignalStatusEmitter(self.status_signal)
        profile_status_emitter = _GroupProfileStatusEmitter(status_emitter)
        stop_event, tail_thread = _start_log_tail(log_file, self.log_message)

        group_start_time = time.perf_counter()
        group_started = False
        build_started = False

        try:
            engine = BuildEngine.from_file(self.config_path, cli_vars=self.cli_vars)
            profiles = engine.resolve_profile_group(self.group_name)
            total_profiles = len(profiles)

            project_cfg = engine.config[YAMLSection.PROJECT]
            final_step_name = project_cfg["DEFAULT_WORKFLOW_STEP"]

            logger.blank()
            logger.info("🔵 BUILD")
            logger.info(f"   Target: {final_step_name}")
            logger.info(f"ℹ️  GROUP  {self.group_name}")
            logger.info(f"   Profiles: {', '.join(profiles)}")
            logger.blank()

            status_emitter.emit(BuildStart(target=final_step_name))
            build_started = True

            status_emitter.emit(
                GroupStart(
                    name=self.group_name,
                    index=1,
                    total=1,
                    profiles=tuple(profiles),
                )
            )
            group_started = True

            for index, profile_name in enumerate(profiles, start=1):
                logger.blank()
                logger.info(f"   PROFILE  {profile_name} [{index}/{total_profiles}]")
                logger.blank()

                # BuildEngine/BuildExecutor own profile-plan and step status,
                # where the authoritative step counts and rebuild reasons exist.
                profile_engine = BuildEngine.from_file(self.config_path, cli_vars=self.cli_vars)

                profile_success = profile_engine.execute(
                    final_step_name=final_step_name,
                    profile_name=profile_name,
                    logger=logger,
                    status_emitter=profile_status_emitter,
                )

                if not profile_success:
                    raise ValueError(f"Profile '{profile_name}' failed")

            elapsed_s = time.perf_counter() - group_start_time

            logger.blank()
            logger.info(f"✅ GROUP COMPLETE  {self.group_name}  {elapsed_s:.2f}s")
            logger.blank()

            status_emitter.emit(
                GroupFinish(
                    name=self.group_name,
                    success=True,
                    elapsed_s=elapsed_s,
                    status_text="Done",
                )
            )
            status_emitter.emit(
                BuildFinish(
                    success=True,
                    elapsed_s=elapsed_s,
                    status_text="Done",
                )
            )

            self.finished.emit()

        except Exception as exc:
            elapsed_s = time.perf_counter() - group_start_time

            logger.blank()
            logger.error(str(exc))
            logger.error(traceback.format_exc())

            if group_started:
                status_emitter.emit(
                    GroupFinish(
                        name=self.group_name,
                        success=False,
                        elapsed_s=elapsed_s,
                        status_text=str(exc),
                    )
                )

            if build_started:
                status_emitter.emit(
                    BuildFinish(
                        success=False,
                        elapsed_s=elapsed_s,
                        status_text=str(exc),
                    )
                )

            self.error.emit(exc)

        finally:
            _stop_log_tail(stop_event, tail_thread)
