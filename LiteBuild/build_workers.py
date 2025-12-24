# build_workers.py
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Optional, Dict

from PySide6.QtCore import QObject, Signal

from LiteBuild.build_engine import BuildEngine
from LiteBuild.build_logger import BuildLogger, LogLevel


class BuildWorker(QObject):
    """
    Worker for running a single build profile or step.
    """
    finished = Signal()
    error = Signal(Exception)
    log_message = Signal(str)

    def __init__(
            self, config_path: str, profile_name: str, cli_vars: Optional[Dict] = None,
            step_name: str = None
    ):
        super().__init__()
        # Store parameters as instance attributes.
        self.config_path = config_path
        self.profile_name = profile_name
        self.cli_vars = cli_vars
        self.step_name = step_name

    def run(self):
        """
        Sets up a log file, runs the engine in a separate thread, and robustly tails the log.
        """
        log_dir = Path("build/logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"litebuild_{self.profile_name}_{int(time.time())}.log"

        log_level_enum = LogLevel["INFO"]
        logger = BuildLogger(log_file, log_level=log_level_enum)

        try:
            engine = BuildEngine.from_file(self.config_path, cli_vars=self.cli_vars)

            # Determine the correct step to run
            final_step_name = self.step_name or engine.config.get("DEFAULT_WORKFLOW_STEP")
            if not final_step_name:
                raise ValueError(
                    "No workflow entry point specified. Please enter a step name in the GUI, "
                    "or set a DEFAULT_WORKFLOW_STEP in your configuration file."
                )

            # Run the actual build in a separate thread so we can tail the log
            build_thread = threading.Thread(
                target=engine.execute,
                args=(final_step_name, self.profile_name, logger)
            )
            build_thread.start()

            # Tail the log file for real-time output
            last_pos = 0
            time.sleep(0.2) # Give the file a moment to be created

            # Use a block that ensures the file is closed even if errors occur
            with open(log_file, 'r', encoding='utf-8') as f:
                while build_thread.is_alive():
                    lines = f.readlines()
                    if lines:
                        for line in lines:
                            self.log_message.emit(line.strip())
                        last_pos = f.tell()
                    time.sleep(0.1)

            build_thread.join() # Wait for the build to finish completely

            # Final read to catch any remaining log messages
            with open(log_file, 'r', encoding='utf-8') as f:
                f.seek(last_pos)
                for line in f.readlines():
                    self.log_message.emit(line.strip())

            self.log_message.emit("DONE")
            self.finished.emit()

        except Exception as e:
            tb_str = traceback.format_exc()
            self.log_message.emit("\n❌ A critical error occurred.")
            self.log_message.emit(tb_str)
            self.error.emit(e)


class BuildGroupWorker(QObject):
    """
    Worker for running a group of build profiles.
    """
    finished = Signal()
    error = Signal(Exception)
    log_message = Signal(str)

    def __init__(self, config_path: str, group_name: str, cli_vars: Optional[Dict] = None):
        super().__init__()
        self.config_path = config_path
        self.group_name = group_name
        self.cli_vars = cli_vars

    def run(self):
        """
        THIS IS THE REAL GROUP BUILD LOGIC.
        It iterates through profiles, calling BuildEngine for each one.
        """
        try:
            engine = BuildEngine.from_file(self.config_path, cli_vars=self.cli_vars)

            profile_groups = engine.config.get("PROFILE_GROUPS", {})
            if self.group_name not in profile_groups:
                available = list(profile_groups.keys())
                raise ValueError(f"Profile Group '{self.group_name}' not found. Available: {available}")

            profiles_to_run = profile_groups[self.group_name]
            self.log_message.emit(f"--- 🚀 Starting Profile Group: {self.group_name} ---")
            self.log_message.emit(f"--- Profiles to run: {', '.join(profiles_to_run)} ---")

            for i, profile_name in enumerate(profiles_to_run):
                self.log_message.emit("\n" + "="*80)
                self.log_message.emit(f"--- ({i+1}/{len(profiles_to_run)}) Running Profile: {profile_name} ---")

                log_dir = Path("build/logs")
                log_dir.mkdir(parents=True, exist_ok=True)
                log_file = log_dir / f"litebuild_{self.group_name}_{profile_name}_{int(time.time())}.log"

                logger = BuildLogger(log_file)
                profile_engine = BuildEngine.from_file(self.config_path, cli_vars=self.cli_vars)
                step_name = profile_engine.config.get("DEFAULT_WORKFLOW_STEP")
                if not step_name:
                    raise ValueError("No DEFAULT_WORKFLOW_STEP found in config for group execution.")

                build_thread = threading.Thread(
                    target=profile_engine.execute,
                    args=(step_name, profile_name, logger)
                )
                build_thread.start()

                # ... Tailing logic repeated for each profile in the group ...
                last_pos = 0
                time.sleep(0.2)
                while build_thread.is_alive():
                    if os.path.exists(log_file):
                        with open(log_file, 'r', encoding='utf-8') as f:
                            f.seek(last_pos)
                            for line in f.readlines():
                                self.log_message.emit(line.strip())
                            last_pos = f.tell()
                    time.sleep(0.2)

                build_thread.join()

                if os.path.exists(log_file):
                    with open(log_file, 'r', encoding='utf-8') as f:
                        f.seek(last_pos)
                        for line in f.readlines():
                            self.log_message.emit(line.strip())

                self.log_message.emit(f"--- ✅ Profile '{profile_name}' finished. ---")

            self.log_message.emit("\n" + "="*80)
            self.log_message.emit(f"--- ✅ Profile Group '{self.group_name}' finished successfully. ---")
            self.finished.emit()

        except Exception as e:
            tb_str = traceback.format_exc()
            self.log_message.emit("\n❌ A critical error occurred during the group build.")
            self.log_message.emit(tb_str)
            self.error.emit(e)