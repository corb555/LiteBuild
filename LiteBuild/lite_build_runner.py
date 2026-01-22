# lite_build_runner.py
import argparse
import sys

from PySide6.QtGui import QTextCursor, QCloseEvent, Qt
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QVBoxLayout,
                               QPushButton, QMessageBox, QTextEdit, QFileDialog, QHBoxLayout,
                               QCheckBox, QProgressBar, QGroupBox, QGridLayout)

from LiteBuild.build_workers import BuildWorker, BuildGroupWorker
from LiteBuild.lite_build_controller import LiteBuildController

class LiteBuildApp(QMainWindow):
    """The GUI application (View) for running LiteBuild."""

    def __init__(self, config_name):
        super().__init__()
        self.setWindowTitle(f"LiteBuild: {config_name}")

        self.controller = LiteBuildController(config_name)

        # --- State Tracking for Status Line ---
        self._status_profile_msg = ""
        self._status_step_msg = ""
        self.profile_total = 1
        self.profile_current = 1
        self.step_total = 6
        self.step_current = 0

        # Console
        self.console_output = QTextEdit()
        self.console_output.setReadOnly(True)
        self.console_output.setStyleSheet(
            "QTextEdit { background-color: #2b2b2b; color: #f0f0f0; font-family: monospace; }"
        )

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        # Main container with some margin
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # --- 1. Input Section (Grid Layout) ---
        input_group = QGroupBox(" ")
        grid = QGridLayout()
        grid.setVerticalSpacing(10) # Space between rows

        # Define widgets
        self.profile_input = QLineEdit()
        self.group_input = QLineEdit()
        self.vars_input = QLineEdit()
        self.step_input = QLineEdit()

        # Set placeholder text (UX best practice)
        self.profile_input.setPlaceholderText("e.g. Europe")
        self.group_input.setPlaceholderText("e.g. ALL")
        self.vars_input.setPlaceholderText("e.g. PREVIEW=_prv")
        self.step_input.setPlaceholderText("Optional target step")

        # "Run" buttons
        action_button_style = """
            QPushButton {
                background-color: #336B52;
                border-radius: 5px;
                color: white;
                padding: 5px;
            }
            QPushButton:hover {
                background-color: #3E8263; /* Slightly lighter on hover */
            }
            QPushButton:pressed {
                background-color: #2A5844; /* Darker when clicked */
            }
        """
        self.run_profile_button = QPushButton("▶ Run Profile")
        self.run_group_button = QPushButton("▶ Run Group")
        self.run_step_button = QPushButton("▶ Run Step")

        #self.run_profile_button.setStyleSheet(action_button_style)
        #self.run_group_button.setStyleSheet(action_button_style)
        #self.run_step_button.setStyleSheet(action_button_style)

        # Add to Grid: addWidget(widget, row, col)

        # Row 0: Profile
        grid.addWidget(QLabel("Profile:"), 0, 0)
        grid.addWidget(self.profile_input, 0, 1)
        grid.addWidget(self.run_profile_button, 0, 2)

        # Row 1: Group
        grid.addWidget(QLabel("Profile Group:"), 1, 0)
        grid.addWidget(self.group_input, 1, 1)
        grid.addWidget(self.run_group_button, 1, 2)

        # Row 2: Parameters
        grid.addWidget(QLabel("Build Parameters:"), 2, 0)
        grid.addWidget(self.vars_input, 2, 1)
        # Empty space in col 2 for this row (or add a Clear button?)

        # Row 3: Step
        grid.addWidget(QLabel("Step:"), 3, 0)
        grid.addWidget(self.step_input, 3, 1)
        grid.addWidget(self.run_step_button, 3, 2)

        # Set Column Stretch
        # Col 0 (Labels): 0 (Minimum needed)
        # Col 1 (Inputs): 1 (Takes all extra space)
        # Col 2 (Buttons): 0 (Minimum needed)
        grid.setColumnStretch(1, 1)

        input_group.setLayout(grid)
        main_layout.addWidget(input_group)

        # --- 2. Options Section ---
        info_button_style = """
            QPushButton {
                background-color: #475d70; /* Slate Blue Base */
                border-radius: 5px;
                color: white;
                padding: 5px;
            }
            QPushButton:hover {
                background-color: #6D8FAB; /* Lighter/Airier on hover */
            }
            QPushButton:pressed {
                background-color: #4A657D; /* Darker/Steely on click */
            }
        """
        options_layout = QHBoxLayout()

        self.prevent_sleep_chk = QCheckBox("Prevent System Sleep")
        self.prevent_sleep_chk.setChecked(True)
        self.prevent_sleep_chk.setToolTip("Keeps the computer awake while the build is running.")

        self.describe_button = QPushButton("Describe Workflow")
        #self.describe_button.setStyleSheet(info_button_style)

        options_layout.addWidget(self.prevent_sleep_chk)
        options_layout.addStretch()
        options_layout.addWidget(self.describe_button)

        main_layout.addLayout(options_layout)

        # --- 3. Progress & Status ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        main_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Ready")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-weight: bold; color: #DDD;")
        main_layout.addWidget(self.status_label)

        # --- 4. Console Log ---
        #main_layout.addWidget(QLabel("Build Log:"))
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

        # Status Connection
        # Note: We assume the controller will emit this signal in Phase 2
        self.controller.status_update.connect(self.update_status)

    def update_status(self, context_type: str, current: int, total: int, status_code: str):
        if context_type == "profile":
            self.profile_total = total
            self.profile_current = current
            self._status_profile_msg = f"Profile {current} of {total}"
            if status_code == "started":
                self._status_step_msg = "Initializing steps..."
                self.step_current = 0

        elif context_type == "step":
            if not self._status_profile_msg:
                self._status_profile_msg = "Single Run"
            self.step_total = max(1, total)
            self.step_current = current
            self._status_step_msg = f"Step {current} of {total} {status_code}"

        # 1. Estimate total steps
        estimated_total_steps = max(1, self.profile_total * self.step_total)

        # 2. Calculate completed steps
        profiles_finished = max(0, self.profile_current - 1)
        done_steps = (profiles_finished * self.step_total) + self.step_current

        # 3. Calculate Percentage
        percent_done = int((done_steps / estimated_total_steps) * 100)
        percent_done = max(1, min(100, percent_done))

        # Update Text
        full_text = f"{self._status_profile_msg}:   {self._status_step_msg}"
        self.status_label.setText(full_text)

        # Update Progress Bar
        self.progress_bar.setValue(percent_done)

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
        # Reset Status
        self.status_label.setText("Starting Build...")
        self._status_profile_msg = ""
        self._status_step_msg = ""

    def on_build_finished(self):
        self._set_ui_enabled(True)
        self.status_label.setText(f"[Done] {self._status_profile_msg}")

    def on_build_error(self, error_message: str):
        self.status_label.setText("Build stopped due to error.")
        QMessageBox.critical(self, "Build Failed", error_message)

    def update_console(self, text: str):
        self.console_output.moveCursor(QTextCursor.End)
        self.console_output.insertPlainText(text + "\n")

    def _set_ui_enabled(self, enabled: bool):
        self.run_profile_button.setEnabled(enabled)
        self.run_group_button.setEnabled(enabled)
        self.run_step_button.setEnabled(enabled)
        self.describe_button.setEnabled(enabled)
        self.profile_input.setEnabled(enabled)
        self.group_input.setEnabled(enabled)
        self.step_input.setEnabled(enabled)
        self.vars_input.setEnabled(enabled)

    def closeEvent(self, event: QCloseEvent):
        if self.controller.is_running():
            QMessageBox.warning(self, "Build in Progress", "Please wait for the build to complete.")
            event.ignore()
        else:
            event.accept()
if __name__ == "__main__":
    app = QApplication(sys.argv)
    parser = argparse.ArgumentParser(description="LiteBuild GUI Runner")
    parser.add_argument("config", help="Name of the config file (e.g., 'LB_config.yml').")
    args = parser.parse_args()

    window = LiteBuildApp(args.config)
    window.resize(1200, 800)
    window.show()
    sys.exit(app.exec())
