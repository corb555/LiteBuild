from __future__ import annotations

import difflib
import threading
import time
import traceback
from pathlib import Path
from typing import Dict, Optional

from LiteBuild.build_engine import BuildEngine
from LiteBuild.build_logger import BuildLogger, LogLevel
from LiteBuild.schema import YAMLSection
from PySide6.QtCore import QObject, Signal

from LiteBuild.build_util import StatusCode, StatusMessage


# build_workers.py

class BuildWorker(QObject):
    """
    Worker for running a single build profile or step.
    """

    finished = Signal()
    error = Signal(Exception)
    log_message = Signal(str)
    status_signal = Signal(object)

    def __init__(
        self,
        config_path: str,
        profile_name: str,
        cli_vars: Optional[Dict] = None,
        step_name: Optional[str] = None,
    ) -> None:
        """Initialize the worker.

        Args:
            config_path: Path to the LiteBuild config file.
            profile_name: Profile to execute.
            cli_vars: Optional CLI variable overrides.
            step_name: Optional explicit workflow step to execute.
        """
        super().__init__()
        self.config_path = config_path
        self.profile_name = profile_name
        self.cli_vars = cli_vars
        self.step_name = step_name

    def _emit_status(self, message: StatusMessage) -> None:
        """Emit a structured status update.

        Args:
            message: Status payload to emit.
        """
        self.status_signal.emit(message)

    def _relay_step_status(
        self,
        context_type: str,
        current_task: int,
        total: int,
        status_code: str,
    ) -> None:
        """Relay engine status callback to the Qt signal.

        Args:
            context_type: Status context type.
            current_task: Current step/profile index.
            total: Total number of items in the context.
            status_code: Status code such as started/done/error.
        """
        message = (
            StatusMessage.profile(current_task, total, status_code)
            if context_type == "profile"
            else StatusMessage.step(current_task, total, status_code)
        )
        self._emit_status(message)

    def run(self) -> None:
        """Run the build and tail log output."""
        log_dir = Path("build/logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"litebuild_{self.profile_name}_{int(time.time())}.log"

        log_level_enum = LogLevel["INFO"]
        logger = BuildLogger(log_file, log_level=log_level_enum)

        try:
            engine = BuildEngine.from_file(self.config_path, cli_vars=self.cli_vars)
            project_cfg = engine.config.get(YAMLSection.PROJECT)

            final_step_name = self.step_name or project_cfg.get("DEFAULT_WORKFLOW_STEP")
            if not final_step_name:
                raise ValueError(
                    "No workflow target step specified. Please  "
                    "set a DEFAULT_WORKFLOW_STEP in PROJECT: section of your configuration file."
                )

            self._emit_status(StatusMessage.profile(1, 1, StatusCode.STARTED))

            build_thread = threading.Thread(
                target=engine.execute,
                args=(final_step_name, self.profile_name, logger),
                kwargs={"status_callback": self._relay_step_status},
            )
            build_thread.start()

            last_pos = 0
            time.sleep(0.2)

            if log_file.exists():
                with open(log_file, "r", encoding="utf-8") as file_obj:
                    while build_thread.is_alive():
                        lines = file_obj.readlines()
                        if lines:
                            for line in lines:
                                self.log_message.emit(line.strip())
                            last_pos = file_obj.tell()
                        time.sleep(0.1)

            build_thread.join()

            if log_file.exists():
                with open(log_file, "r", encoding="utf-8") as file_obj:
                    file_obj.seek(last_pos)
                    for line in file_obj.readlines():
                        self.log_message.emit(line.strip())

            self._emit_status(StatusMessage.profile(1, 1, StatusCode.DONE))
            self.log_message.emit("DONE")
            self.finished.emit()

        except Exception as exc:
            tb_str = traceback.format_exc()
            self.log_message.emit("\n❌ A critical error occurred.")
            self.log_message.emit(tb_str)
            self.error.emit(exc)
            self._emit_status(StatusMessage.profile(1, 1, StatusCode.ERROR))


class BuildGroupWorker(QObject):
    """
    Worker for running a GROUP of profiles sequentially.
    """

    finished = Signal()
    error = Signal(Exception)
    log_message = Signal(str)
    status_signal = Signal(object)

    def __init__(
        self,
        config_path: str,
        group_name: str,
        cli_vars: Optional[Dict] = None,
    ) -> None:
        """Initialize the worker.

        Args:
            config_path: Path to the LiteBuild config file.
            group_name: Profile group to execute.
            cli_vars: Optional CLI variable overrides.
        """
        super().__init__()
        self.config_path = config_path
        self.group_name = group_name
        self.cli_vars = cli_vars

    def _emit_status(self, message: StatusMessage) -> None:
        """Emit a structured status update.

        Args:
            message: Status payload to emit.
        """
        self.status_signal.emit(message)

    def _relay_step_status(
        self,
        context_type: str,
        current_task: int,
        total: int,
        status_code: str,
    ) -> None:
        """Relay engine status callback to the GUI.

        Args:
            context_type: Status context type.
            current_task: Current step/profile index.
            total: Total number of items in the context.
            status_code: Status code such as started/done/error.
        """
        message = (
            StatusMessage.profile(current_task, total, status_code)
            if context_type == "profile"
            else StatusMessage.step(current_task, total, status_code)
        )
        self._emit_status(message)

    @staticmethod
    def get_suggestion(invalid_key: str, valid_options: list[str]) -> str:
        """Return a 'Did you mean X?' hint if a close match is found.

        Args:
            invalid_key: Invalid user-provided key.
            valid_options: Valid available options.

        Returns:
            Suggestion text or an empty string.
        """
        matches = difflib.get_close_matches(invalid_key, valid_options, n=1, cutoff=0.6)
        return f"\n   Did you mean '{matches[0]}'?" if matches else ""

    def run(self) -> None:
        """Iterate through profiles and execute them sequentially."""
        try:
            engine = BuildEngine.from_file(self.config_path, cli_vars=self.cli_vars)

            profile_groups = engine.config.get(YAMLSection.PROFILE_GROUPS, {})
            if self.group_name not in profile_groups:
                available = list(profile_groups.keys())
                hint = self.get_suggestion(self.group_name, available)
                raise ValueError(
                    f"Profile Group '{self.group_name}' not found. {hint}\n"
                    f"Available: {available}"
                )

            profiles_to_run = profile_groups[self.group_name]
            total_profiles = len(profiles_to_run)

            self.log_message.emit(f"Starting Profile Group: {self.group_name} ")
            self.log_message.emit(f"Profiles to run: {', '.join(profiles_to_run)} ")

            for index, profile_name in enumerate(profiles_to_run, start=1):
                self._emit_status(StatusMessage.profile(index, total_profiles, StatusCode.STARTED))

                self.log_message.emit("\n" + "=" * 80)
                self.log_message.emit(
                    f" ({index}/{total_profiles}) Running Profile: {profile_name}\n "
                )

                log_dir = Path("build/logs")
                log_dir.mkdir(parents=True, exist_ok=True)
                log_file = (
                    log_dir / f"litebuild_{self.group_name}_{profile_name}_{int(time.time())}.log"
                )

                logger = BuildLogger(log_file, log_level=LogLevel["INFO"])

                profile_engine = BuildEngine.from_file(self.config_path, cli_vars=self.cli_vars)
                step_name = profile_engine.config.get("DEFAULT_WORKFLOW_STEP")
                if not step_name:
                    raise ValueError(
                        "No DEFAULT_WORKFLOW_STEP found in config for group execution."
                    )

                build_thread = threading.Thread(
                    target=profile_engine.execute,
                    args=(step_name, profile_name, logger),
                    kwargs={"status_callback": self._relay_step_status},
                )
                build_thread.start()

                last_pos = 0
                time.sleep(0.2)

                while build_thread.is_alive():
                    if log_file.exists():
                        with open(log_file, "r", encoding="utf-8") as file_obj:
                            file_obj.seek(last_pos)
                            for line in file_obj.readlines():
                                self.log_message.emit(line.strip())
                            last_pos = file_obj.tell()
                    time.sleep(0.2)

                build_thread.join()

                if log_file.exists():
                    with open(log_file, "r", encoding="utf-8") as file_obj:
                        file_obj.seek(last_pos)
                        for line in file_obj.readlines():
                            self.log_message.emit(line.strip())

                self.log_message.emit(f"✅ Profile '{profile_name}' finished.")
                self._emit_status(StatusMessage.profile(index, total_profiles, StatusCode.DONE))

            self.log_message.emit("\n" + "=" * 80)
            self.log_message.emit(
                f"✅ Profile Group '{self.group_name}' finished successfully. "
            )
            self.finished.emit()

        except Exception as exc:
            tb_str = traceback.format_exc()
            self.log_message.emit("\n❌ A critical error occurred during the group build.")
            self.log_message.emit(tb_str)
            self.error.emit(exc)
            self._emit_status(StatusMessage.profile(0, 0, StatusCode.ERROR))