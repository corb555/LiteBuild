# lite_build_runner.py
import argparse
import sys

from PySide6.QtGui import QTextCursor, QCloseEvent
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QVBoxLayout,
                               QPushButton, QMessageBox, QTextEdit, QFileDialog, QHBoxLayout)

from LiteBuild.build_workers import BuildWorker, BuildGroupWorker
from LiteBuild.lite_build_controller import LiteBuildController


class LiteBuildApp(QMainWindow):
    """The GUI application (View) for running LiteBuild."""

    def __init__(self, config_name):
        super().__init__()
        self.setWindowTitle(f"LiteBuild: {config_name}")

        self.controller = LiteBuildController(config_name)

        # --- UI Element Definitions  ---
        self.profile_input = QLineEdit()
        self.group_input = QLineEdit()
        self.vars_input = QLineEdit("")
        self.step_input = QLineEdit()
        self.run_profile_button = QPushButton("Run Profile")
        self.run_group_button = QPushButton("Run Group")
        self.run_step_button = QPushButton("Run Step")
        self.describe_button = QPushButton("Describe Profile")
        self.console_output = QTextEdit()
        self.console_output.setReadOnly(True)
        self.console_output.setStyleSheet(
            "QTextEdit { background-color: #2b2b2b; color: #f0f0f0; font-family: monospace; }"
            )

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        main_layout = QVBoxLayout()
        button_width = 120
        profile_layout = QHBoxLayout()
        profile_layout.addWidget(QLabel("Profile:"), stretch=0)
        profile_layout.addWidget(self.profile_input, stretch=1)
        self.run_profile_button.setFixedWidth(button_width)
        self.describe_button.setFixedWidth(button_width)
        profile_layout.addWidget(self.run_profile_button)
        profile_layout.addWidget(self.describe_button)
        main_layout.addLayout(profile_layout)
        group_layout = QHBoxLayout()
        group_layout.addWidget(QLabel("Profile Group:"), stretch=0)
        group_layout.addWidget(self.group_input, stretch=1)
        self.run_group_button.setFixedWidth(button_width)
        group_layout.addWidget(self.run_group_button)
        main_layout.addLayout(group_layout)
        vars_layout = QHBoxLayout()
        vars_layout.addWidget(QLabel("Build Variables:"), stretch=0)
        vars_layout.addWidget(self.vars_input, stretch=1)
        main_layout.addLayout(vars_layout)
        step_layout = QHBoxLayout()
        step_layout.addWidget(QLabel("Step:"), stretch=0)
        step_layout.addWidget(self.step_input, stretch=1)
        self.run_step_button.setFixedWidth(button_width)
        step_layout.addWidget(self.run_step_button)
        main_layout.addLayout(step_layout)
        main_layout.addWidget(QLabel("Build Log:"))
        main_layout.addWidget(self.console_output)
        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

    def _connect_signals(self):
        # Connect UI actions to controller slots
        self.run_profile_button.clicked.connect(self.start_profile_build)
        self.run_group_button.clicked.connect(self.start_group_build)
        self.run_step_button.clicked.connect(self.start_single_step)
        self.describe_button.clicked.connect(self.describe_workflow)

        # Connect controller signals to UI update slots
        self.controller.build_started.connect(self.on_build_started)
        self.controller.build_finished.connect(self.on_build_finished)
        self.controller.build_error.connect(self.on_build_error)
        self.controller.log_received.connect(self.update_console)

    # --- Methods that delegate to the controller ---
    def start_profile_build(self):
        profile_name = self.profile_input.text().strip()
        if not profile_name:
            QMessageBox.warning(self, "Input Error", "Please provide a Profile name.")
            return
        self._execute_build(BuildWorker, profile_name=profile_name)

    def start_group_build(self):
        group_name = self.group_input.text().strip()
        if not group_name:
            QMessageBox.warning(self, "Input Error", "Please provide a Profile Group name.")
            return
        self._execute_build(BuildGroupWorker, group_name=group_name)

    def start_single_step(self):
        profile_name = self.profile_input.text().strip()
        step_name = self.step_input.text().strip()
        if not profile_name or not step_name:
            QMessageBox.warning(
                self, "Input Error", "Please provide both a Profile and a Step name."
                )
            return
        self._execute_build(BuildWorker, profile_name=profile_name, step_name=step_name)

    def _execute_build(self, worker_class, **kwargs):
        cli_vars = self.controller.parse_vars(self.vars_input.text())
        if cli_vars is None:
            QMessageBox.warning(
                self, "Input Error", "Build Variables must be in 'KEY=value' format."
                )
            return
        self.console_output.clear()
        self.controller.start_build(worker_class, cli_vars=cli_vars, **kwargs)

    def describe_workflow(self):
        profile_name = self.profile_input.text().strip()
        if not profile_name:
            QMessageBox.warning(
                self, "Input Error", "A profile name is required to describe a workflow."
                )
            return
        cli_vars = self.controller.parse_vars(self.vars_input.text())
        if cli_vars is None:
            QMessageBox.warning(
                self, "Input Error", "Build Variables must be in 'KEY=value' format."
                )
            return

        markdown_content = self.controller.describe_workflow(profile_name, cli_vars)
        if markdown_content:
            suggested_filename = f"{profile_name}_Workflow.md"
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

    # --- Slots for handling signals from the controller ---
    def on_build_started(self):
        self._set_ui_enabled(False)

    def on_build_finished(self):
        self._set_ui_enabled(True)

    def on_build_error(self, error_message: str):
        QMessageBox.critical(self, "Build Failed", error_message)

    def update_console(self, text: str):
        self.console_output.moveCursor(QTextCursor.End)
        self.console_output.insertPlainText(text + "\n")

    def _set_ui_enabled(self, enabled: bool):
        self.run_profile_button.setEnabled(enabled)
        self.run_group_button.setEnabled(enabled)
        self.run_step_button.setEnabled(enabled)
        self.describe_button.setEnabled(enabled)

    def closeEvent(self, event: QCloseEvent):
        if self.controller.is_running():
            QMessageBox.warning(self, "Build in Progress", "Please wait for the build to complete.")
            event.ignore()
        else:
            event.accept()


if __name__ == "__main__":
    # The main entry point is largely unchanged.
    app = QApplication(sys.argv)
    parser = argparse.ArgumentParser(description="LiteBuild GUI Runner")
    parser.add_argument("config", help="Name of the config file (e.g., 'LB_config.yml').")
    args = parser.parse_args()

    window = LiteBuildApp(args.config)
    window.resize(1200, 800)
    window.show()
    sys.exit(app.exec())
