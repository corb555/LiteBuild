# lite_build_runner.py
import argparse
import logging
import multiprocessing
import os
import sys
import traceback
from typing import Optional, Dict

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QTextCursor, QCloseEvent
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QVBoxLayout,
                               QPushButton, QMessageBox, QTextEdit, QFileDialog)

from build_engine import BuildEngine, BuildStateManager, BuildPlanner, UpdateCode

# Get a logger for the GUI runner itself (for startup, etc.)
log = logging.getLogger(__name__)


class QtLogHandler(logging.Handler, QObject):
    """
    A logging handler that emits a Qt signal for each log record.
    This is the thread-safe way to log from a secondary thread to a GUI.
    """
    new_record = Signal(str)

    def __init__(self):
        super().__init__()
        QObject.__init__(self)

    def emit(self, record):
        """Emits the formatted log record as a signal."""
        msg = self.format(record)
        self.new_record.emit(msg)


class WorkerSignals(QObject):
    """Defines signals available from a running worker thread."""
    finished = Signal()
    error = Signal(Exception)


class BuildWorker(QObject):
    """
    A thin wrapper that calls the unified BuildEngine in a background thread.
    """
    def __init__(
            self, config_path: str, target_name: str, cli_vars: Optional[Dict] = None,
            step_name: str = None
    ):
        super().__init__()
        self.config_path = config_path
        self.target_name = target_name
        self.cli_vars = cli_vars
        self.step_name = step_name
        self.signals = WorkerSignals()
        self.worker_log = logging.getLogger((__name__))
        self.worker_log.propagate = False
        self.worker_log.setLevel(logging.INFO)

    def run(self):
        """Calls the BuildEngine and pipes its output to the GUI via callbacks."""
        try:
            # --- The ENTIRE build logic is now replaced by this block ---
            engine = BuildEngine.from_file(self.config_path, cli_vars=self.cli_vars)

            # Define callbacks that will log messages using the worker's logger.
            # This logger is already connected to the GUI.
            callbacks = {
                "on_info": self.worker_log.info,
                "on_step_output": lambda step_name, msg: self.worker_log.info(msg),
            }

            # Execute the build using the single, authoritative engine.
            # This will run in parallel.
            engine.execute(self.target_name, self.step_name, callbacks=callbacks)

            self.signals.finished.emit()

        except Exception as e:
            # Catch planning errors or any other unexpected issues
            tb_str = traceback.format_exc()
            self.worker_log.error(f"\n❌ A critical build error occurred.")
            self.worker_log.error(tb_str)
            self.signals.error.emit(e)


class LiteBuildApp(QMainWindow):
    """The main GUI application for running LiteBuild."""

    def __init__(self, config_name):
        super().__init__()
        self.setWindowTitle("LiteBuild Runner")

        if not config_name.startswith("LB_"):
            QMessageBox.critical(
                self, "Invalid Configuration", f"Invalid config filename '{config_name}'.\n\n"
                                               "LiteBuild config files must start with the prefix "
                                               "'LB_' (e.g., 'LB_config.yml')."
            )
        self.config_name = config_name

        # --- UI Widget Setup ---
        self.project_input = QLineEdit("./")
        self.target_input = QLineEdit("")
        self.vars_input = QLineEdit("")
        self.vars_input.setPlaceholderText("e.g., REGION=USWest DATA_TYPE=popups")
        self.step_input = QLineEdit("")

        self.run_full_build_button = QPushButton("Full Build")
        self.run_step_button = QPushButton("Build Step")
        self.describe_button = QPushButton("Describe")
        self.run_full_build_button.clicked.connect(self.start_full_build)
        self.run_step_button.clicked.connect(self.start_single_step)
        self.describe_button.clicked.connect(self.describe_workflow)

        self.console_output = QTextEdit()
        self.console_output.setReadOnly(True)
        self.console_output.setStyleSheet(
            "QTextEdit { background-color: #2b2b2b; color: #f0f0f0; font-family: monospace; }"
        )

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Project Folder:"))
        layout.addWidget(self.project_input)
        layout.addWidget(QLabel("Target Name:"))
        layout.addWidget(self.target_input)
        layout.addWidget(QLabel("Build Variables (--vars):"))
        layout.addWidget(self.vars_input)
        layout.addWidget(self.run_full_build_button)
        layout.addWidget(QLabel("Step Name (for partial builds):"))
        layout.addWidget(self.step_input)
        layout.addWidget(self.run_step_button)
        layout.addWidget(self.describe_button)
        layout.addWidget(QLabel("Build Log:"))
        layout.addWidget(self.console_output)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.thread = None
        self.worker = None

        # --- Set up the dedicated Qt logging handler ---
        self.log_handler = QtLogHandler()
        self.log_handler.setFormatter(logging.Formatter('%(message)s'))
        self.log_handler.new_record.connect(self.update_console)

    def closeEvent(self, event: QCloseEvent):
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(
                self, "Build in Progress", "Please wait for the build to complete before closing."
                )
            event.ignore()
        else:
            event.accept()

    def _parse_vars(self) -> Optional[Dict[str, str]]:
        vars_text = self.vars_input.text().strip()
        if not vars_text: return {}
        try:
            return dict(item.split('=', 1) for item in vars_text.split())
        except ValueError:
            QMessageBox.warning(
                self, "Input Error", "Build Variables must be in 'KEY=value' format."
                )
            return None

    def start_full_build(self):
        self._launch_worker(step_name=None)

    def start_single_step(self):
        step_name = self.step_input.text().strip()
        if not step_name:
            QMessageBox.warning(self, "Input Error", "Please provide a step name.")
            return
        self._launch_worker(step_name=step_name)

    def describe_workflow(self):
        project_name = self.project_input.text().strip()
        target_name = self.target_input.text().strip()
        if not project_name or not target_name:
            QMessageBox.warning(self, "Input Error", "Project and Target names are required.")
            return
        cli_vars = self._parse_vars()
        if cli_vars is None: return
        config_path = os.path.join(project_name, self.config_name)
        if not os.path.exists(config_path):
            QMessageBox.critical(
                self, "File Not Found", f"Configuration file not found:\n{config_path}"
                )
            return
        try:
            engine = BuildEngine.from_file(config_path, cli_vars=cli_vars)
            markdown_content = engine.describe(target_name)
            suggested_filename = f"{target_name}_Workflow.md"
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Save Workflow Description", suggested_filename,
                "Markdown Files (*.md);;All Files (*)"
                )
            if file_path:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(markdown_content)
                QMessageBox.information(
                    self, "Success", f"Workflow description saved to:\n{file_path}"
                    )
        except Exception as e:
            self.on_build_error(e)

    def _launch_worker(self, step_name: str = None):
        project_name = self.project_input.text().strip()
        target_name = self.target_input.text().strip()
        cli_vars = self._parse_vars()
        if cli_vars is None: return

        # Use the original, correct logic that allows builds without a target name
        if not target_name and not cli_vars:
            QMessageBox.warning(
                self, "Input Error", "A Target Name or Build Variables are required."
                )
            return

        config_path = os.path.join(project_name, self.config_name)
        if not os.path.exists(config_path):
            QMessageBox.critical(
                self, "File Not Found", f"Configuration file not found:\n{config_path}"
                )
            return

        self.console_output.clear()
        self.run_full_build_button.setEnabled(False)
        self.run_step_button.setEnabled(False)
        self.describe_button.setEnabled(False)

        self.thread = QThread()
        self.worker = BuildWorker(config_path, target_name, cli_vars=cli_vars, step_name=step_name)

        self.worker.worker_log.addHandler(self.log_handler)
        self.worker.moveToThread(self.thread)

        # This robust connection cascade ensures the thread is always cleaned up properly.
        # 1. Start the worker's job when the thread's event loop starts.
        self.thread.started.connect(self.worker.run)

        # 2. Tell the thread to stop its event loop when the worker is done (success or fail).
        self.worker.signals.finished.connect(self.thread.quit)
        self.worker.signals.error.connect(self.thread.quit)

        # 3. Connect worker signals to the main GUI slots.
        self.worker.signals.finished.connect(self.on_build_complete)
        self.worker.signals.error.connect(self.on_build_error)

        # 4. Schedule the thread and worker objects for deletion once the thread has fully finished.
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        # 5. Clean up the logger connection as the final step.
        self.thread.finished.connect(lambda: self.worker.worker_log.removeHandler(self.log_handler))

        self.thread.start()

    def update_console(self, text: str):
        """Appends text from the logging handler to the console output widget."""
        self.console_output.moveCursor(QTextCursor.End)
        self.console_output.insertPlainText(text + "\n")

    def on_build_complete(self):
        """Handles the successful completion of a build."""
        self.run_full_build_button.setEnabled(True)
        self.run_step_button.setEnabled(True)
        self.describe_button.setEnabled(True)

    def on_build_error(self, exception_obj: Exception):
        """Handles errors reported by the worker."""
        # The worker's logger has already sent the detailed error to the console.
        # We just need to show a high-level message box and re-enable the UI.
        QMessageBox.critical(
            self, "Build Failed",
            f"The build process failed.\n\n{str(exception_obj)}\n\nSee log for details."
        )
        self.run_full_build_button.setEnabled(True)
        self.run_step_button.setEnabled(True)
        self.describe_button.setEnabled(True)


def setup_logging(quiet: bool = False, verbose: bool = False):
    """Configures the root logger for the application."""
    level = logging.INFO
    if quiet:
        level = logging.WARNING
    if verbose:
        level = logging.DEBUG

    logging.basicConfig(
        level=level, format='%(message)s', stream=sys.stdout
    )



if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = QApplication(sys.argv)
    parser = argparse.ArgumentParser(description="LiteBuild GUI Runner")
    parser.add_argument("config", help="Name of the config file (e.g., 'LB_config.yml').")
    parser.add_argument(
        "--quiet", "-q", action='store_true', help="Suppress informational messages."
        )
    # NEW: Add a verbose flag for debug logging
    parser.add_argument(
        "--verbose", "-v", action='store_true', help="Enable detailed debug logging."
        )

    args = parser.parse_args()
    setup_logging(args.quiet)
    window = LiteBuildApp(args.config)
    window.resize(1200,800)
    window.show()
    sys.exit(app.exec())
