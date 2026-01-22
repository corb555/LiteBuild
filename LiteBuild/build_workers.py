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

    # Status Signal (type, current, total, status_code)
    status_signal = Signal(str, int, int, str)

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

    def _relay_step_status(self, type, current, total, status):
        """
        Callback function passed to the BuildEngine.
        It bridges the generic python callback to the Qt Signal.
        """
        self.status_signal.emit(type, current, total, status)

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

            # 1. Update GUI that Profile is starting (1 of 1)
            self.status_signal.emit("profile", 1, 1, "started")

            # Run the actual build in a separate thread so we can tail the log
            # We pass _relay_step_status as a kwarg to the engine
            build_thread = threading.Thread(
                target=engine.execute,
                args=(final_step_name, self.profile_name, logger),
                kwargs={"status_callback": self._relay_step_status}
            )
            build_thread.start()

            # Tail the log file for real-time output
            last_pos = 0
            time.sleep(0.2) # Give the file a moment to be created

            # Use a block that ensures the file is closed even if errors occur
            # Note: We check existence to avoid race conditions on fast failures
            if log_file.exists():
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
            if log_file.exists():
                with open(log_file, 'r', encoding='utf-8') as f:
                    f.seek(last_pos)
                    for line in f.readlines():
                        self.log_message.emit(line.strip())

            # 2. Update GUI that Profile is done
            self.status_signal.emit("profile", 1, 1, "done")

            self.log_message.emit("DONE")
            self.finished.emit()

        except Exception as e:
            tb_str = traceback.format_exc()
            self.log_message.emit("\n❌ A critical error occurred.")
            self.log_message.emit(tb_str)
            self.error.emit(e)

            # Emit error status to reset GUI state
            self.status_signal.emit("profile", 1, 1, "error")
class BuildGroupWorker(QObject):
    """
    Worker for running a GROUP of profiles sequentially.
    """
    finished = Signal()
    error = Signal(Exception)
    log_message = Signal(str)

    # Status Signal (type, current, total, status_code)
    status_signal = Signal(str, int, int, str)

    def __init__(self, config_path: str, group_name: str, cli_vars: Optional[Dict] = None):
        super().__init__()
        self.config_path = config_path
        self.group_name = group_name
        self.cli_vars = cli_vars

    def _relay_step_status(self, type, current, total, status):
        """
        Callback helper.
        This is passed to the BuildEngine. When the Engine reports a step update,
        this relays it to the GUI via Qt Signal.
        """
        self.status_signal.emit(type, current, total, status)

    def run(self):
        """
        Iterates through profiles, calling BuildEngine for each one.
        """
        try:
            # 1. Initialize Engine to read the config and get the list of profiles
            engine = BuildEngine.from_file(self.config_path, cli_vars=self.cli_vars)

            profile_groups = engine.config.get("PROFILE_GROUPS", {})
            if self.group_name not in profile_groups:
                available = list(profile_groups.keys())
                raise ValueError(f"Profile Group '{self.group_name}' not found. Available: {available}")

            profiles_to_run = profile_groups[self.group_name]
            total_profiles = len(profiles_to_run)

            self.log_message.emit(f"--- Starting Profile Group: {self.group_name} ---")
            self.log_message.emit(f"--- Profiles to run: {', '.join(profiles_to_run)} ---")

            # 2. Iterate through profiles
            for i, profile_name in enumerate(profiles_to_run):
                current_profile_num = i + 1

                # --- UPDATE STATUS: Profile Started ---
                self.status_signal.emit("profile", current_profile_num, total_profiles, "started")

                self.log_message.emit("\n" + "="*80)
                self.log_message.emit(f"--- ({current_profile_num}/{total_profiles}) Running Profile: {profile_name} ---")

                # Setup Logging
                log_dir = Path("build/logs")
                log_dir.mkdir(parents=True, exist_ok=True)
                log_file = log_dir / f"litebuild_{self.group_name}_{profile_name}_{int(time.time())}.log"

                # Use standard INFO logging
                logger = BuildLogger(log_file, log_level=LogLevel["INFO"])

                # Create a fresh engine instance for this specific profile
                profile_engine = BuildEngine.from_file(self.config_path, cli_vars=self.cli_vars)
                step_name = profile_engine.config.get("DEFAULT_WORKFLOW_STEP")

                if not step_name:
                    raise ValueError("No DEFAULT_WORKFLOW_STEP found in config for group execution.")

                # 3. Start Build Thread
                # We pass 'self._relay_step_status' as the status_callback
                build_thread = threading.Thread(
                    target=profile_engine.execute,
                    args=(step_name, profile_name, logger),
                    kwargs={"status_callback": self._relay_step_status}
                )
                build_thread.start()

                # 4. Tail Logs
                last_pos = 0
                time.sleep(0.2)

                # Robust tailing loop
                while build_thread.is_alive():
                    if log_file.exists():
                        with open(log_file, 'r', encoding='utf-8') as f:
                            f.seek(last_pos)
                            for line in f.readlines():
                                self.log_message.emit(line.strip())
                            last_pos = f.tell()
                    time.sleep(0.2)

                build_thread.join()

                # Final log flush
                if log_file.exists():
                    with open(log_file, 'r', encoding='utf-8') as f:
                        f.seek(last_pos)
                        for line in f.readlines():
                            self.log_message.emit(line.strip())

                self.log_message.emit(f"--- ✅ Profile '{profile_name}' finished. ---")

                # --- UPDATE STATUS: Profile Done ---
                self.status_signal.emit("profile", current_profile_num, total_profiles, "done")

            self.log_message.emit("\n" + "="*80)
            self.log_message.emit(f"--- ✅ Profile Group '{self.group_name}' finished successfully. ---")
            self.finished.emit()

        except Exception as e:
            tb_str = traceback.format_exc()
            self.log_message.emit("\n❌ A critical error occurred during the group build.")
            self.log_message.emit(tb_str)
            self.error.emit(e)

            # Reset status on critical error
            self.status_signal.emit("profile", 0, 0, "error")