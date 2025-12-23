# lite_build_controller.py
import os
from typing import Optional, Dict, Type

from PySide6.QtCore import QObject, Signal, QThread


class LiteBuildController(QObject):
    """
    A non-GUI controller to manage the LiteBuild process.
    This is the reusable core logic of the application.
    """
    # Signals for the UI to connect to
    build_started = Signal()
    build_finished = Signal()
    build_error = Signal(str)  # Pass a formatted string for the UI
    log_received = Signal(str)

    def __init__(self, config_name: str, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.config_name = config_name
        self._thread: Optional[QThread] = None
        self._worker: Optional[QObject] = None

    def is_running(self) -> bool:
        """Checks if a build is currently in progress."""
        return self._thread is not None and self._thread.isRunning()

    @staticmethod
    def parse_vars(vars_text: str) -> Optional[Dict[str, str]]:
        """Parses a string of 'KEY=value' pairs into a dictionary."""
        vars_text = vars_text.strip()
        if not vars_text:
            return {}
        try:
            return dict(item.split('=', 1) for item in vars_text.split())
        except ValueError:
            return None  # Indicates a parsing error to the caller

    def start_build(self, worker_class: Type[QObject], cli_vars: Dict, **kwargs):
        if self.is_running():
            self.log_received.emit("A build is already in progress.")
            return

        if not os.path.exists(self.config_name):
            self.build_error.emit(f"Configuration file not found:\n{self.config_name}")
            return

        self.build_started.emit()

        self._thread = QThread()
        self._worker = worker_class(config_path=self.config_name, cli_vars=cli_vars, **kwargs)
        self._worker.moveToThread(self._thread)

        self._worker.log_message.connect(self.log_received)
        self._worker.finished.connect(self._on_build_complete)
        self._worker.error.connect(self._on_build_error)

        # Clean up thread and worker (unchanged)
        self._thread.started.connect(self._worker.run)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.destroyed.connect(self._on_thread_destroyed)

        self._thread.start()

    def describe_workflow(self, profile_name: str, cli_vars: Dict) -> Optional[str]:
        """
        Gets the workflow description. Returns markdown string or None on error.
        File dialog logic should be in the UI, not here.
        """
        try:
            # This would be your actual engine logic
            # engine = BuildEngine.from_file(self.config_name, cli_vars=cli_vars)
            # return engine.describe(profile_name)
            return f"# Workflow for {profile_name}\n\n- Step 1\n- Step 2"  # Dummy
        except Exception as e:
            self.build_error.emit(f"Failed to describe workflow: {e}")
            return None

    def _on_build_complete(self):
        """Handles the successful completion of a build."""
        self.build_finished.emit()
        self._cleanup()

    def _on_build_error(self, exception_obj: Exception):
        """Handles a failed build."""
        error_message = f"The process failed.\n\n{str(exception_obj)}\n\nSee log for details."
        self.build_error.emit(error_message)
        self.build_finished.emit()  # A failed build is still a "finished" build
        self._cleanup()

    def _cleanup(self):
        """
        CORRECTED: This method now only tells the thread to stop its event loop.
        The actual deletion is handled by the `deleteLater` connections.
        """
        if self._thread and self._thread.isRunning():
            self._thread.quit()

    def _on_thread_destroyed(self):
        """
        This is called automatically when the QThread C++ object is deleted.
        It's now safe to nullify our Python references.
        """
        self._thread = None
        self._worker = None