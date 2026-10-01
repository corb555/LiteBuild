from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional, Type

from PySide6.QtCore import QObject, QThread, Signal

from LiteBuild.build_engine import BuildEngine
from LiteBuild.build_logger import BuildLogger, setup_logger


class LiteBuildController(QObject):
    """Non-GUI controller connecting LiteBuild workers to the application UI.

    The controller does not interpret build logging or structured status. It
    relays the two channels independently:

    - ``log_received`` carries human-readable diagnostic text chunks.
    - ``status_update`` carries typed LiteBuild status-message objects.

    Build semantics and message formatting remain owned by LiteBuild.
    """

    build_started = Signal()
    build_finished = Signal()
    build_error = Signal(str)

    # Raw diagnostic text chunks. CR/LF are preserved by the worker.
    log_received = Signal(str)

    # Typed objects from LiteBuild.status_message.
    status_update = Signal(object)

    def __init__(
        self,
        config_name: str,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._config_name = config_name
        self._thread: Optional[QThread] = None
        self._worker: Optional[QObject] = None

        # Project configuration is part of the controller's identity, not a
        # side-effect of starting a build. Load and validate it immediately so
        # application code can bind editors and other project UI to the exact
        # configuration roots used by LiteBuild.
        self._project_engine = BuildEngine.from_file(
            self._config_name,
            cli_vars=None,
        )

        general = self._project_engine.config.get("GENERAL", {})
        config_dir = general.get("CONFIG_DIR")
        if not isinstance(config_dir, str) or not config_dir.strip():
            raise ValueError(
                "LiteBuild configuration requires GENERAL.CONFIG_DIR"
            )

        self._config_dir = Path(config_dir)

    @property
    def config_dir(self) -> Path:
        """
        Return the project configuration directory defined by LiteBuild.

        CONFIG_DIR is a required project-level contract. Applications such as
        LandWeaver and GeoVectorWeaver should use this property when binding
        editors to build configuration files so the UI and workflow operate on
        the same authored project state.
        """
        return self._config_dir

    def is_running(self) -> bool:
        """Return whether a LiteBuild worker thread is currently active."""
        return self._thread is not None and self._thread.isRunning()

    @staticmethod
    def parse_vars(vars_text: str) -> Optional[Dict[str, str]]:
        """Parse space-separated ``KEY=value`` command-line overrides."""
        vars_text = vars_text.strip()
        if not vars_text:
            return {}

        try:
            return dict(item.split("=", 1) for item in vars_text.split())
        except ValueError:
            return None

    def start_build(
        self,
        worker_class: Type[QObject],
        cli_vars: Dict,
        **kwargs,
    ) -> None:
        """Start a LiteBuild worker and relay its independent output channels."""
        if "CONFIG_DIR" in cli_vars:
            self.build_error.emit(
                "CONFIG_DIR is a project-level LiteBuild setting and cannot be "
                "overridden for an individual build."
            )
            return

        if self.is_running():
            self.build_error.emit("A build is already in progress.")
            return

        if not os.path.exists(self._config_name):
            self.build_error.emit(
                f"Configuration file not found:\n{self._config_name}"
            )
            return

        self.build_started.emit()

        self._thread = QThread()
        self._worker = worker_class(
            config_path=self._config_name,
            cli_vars=cli_vars,
            **kwargs,
        )
        self._worker.moveToThread(self._thread)

        # Human-readable diagnostic transcript.
        self._worker.log_message.connect(self.log_received)

        # Structured build state.
        self._worker.status_signal.connect(self.status_update)

        # Worker lifecycle.
        self._worker.finished.connect(self._on_build_complete)
        self._worker.error.connect(self._on_build_error)

        # Thread lifecycle.
        self._thread.started.connect(self._worker.run)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.destroyed.connect(self._on_thread_destroyed)

        self._thread.start()
        
    def get_profile_list(self):
        return self._project_engine.get_profile_list()

    def has_profile(self, profile_name: str) -> bool:
        """Return whether the configured LiteBuild project defines a profile."""
        return self._project_engine.has_profile(profile_name=profile_name)

    def describe_workflow(
        self,
        profile_name: str,
        cli_vars: Dict,
    ) -> Optional[str]:
        """Generate a workflow description without producing build log output."""
        if "CONFIG_DIR" in cli_vars:
            self.build_error.emit(
                "CONFIG_DIR is a project-level LiteBuild setting and cannot be "
                "overridden for an individual workflow."
            )
            return None

        if not os.path.exists(self._config_name):
            self.build_error.emit(
                f"Configuration file not found:\n{self._config_name}"
            )
            return None

        try:
            # Description generation is not a build. Suppress incidental
            # diagnostic output from shared planning/configuration components.
            #setup_logger(BuildLogger(None))

            engine = BuildEngine.from_file(self._config_name, cli_vars=cli_vars)
            return engine.describe(profile_name)

        except (FileNotFoundError, ValueError) as exc:
            self.build_error.emit(
                f"Failed to generate workflow description:\n{exc}"
            )
            return None

    def _on_build_complete(self) -> None:
        """Handle successful worker completion."""
        self.build_finished.emit()
        self._cleanup()

    def _on_build_error(self, exception_obj: Exception) -> None:
        """Handle worker failure without duplicating the diagnostic transcript."""
        self.build_error.emit(
            f"Build failed.\n\n{exception_obj}\n\nSee the build log for details."
        )
        self.build_finished.emit()
        self._cleanup()

    def _cleanup(self) -> None:
        """Stop the worker thread event loop.

        QObject deletion is handled by the ``deleteLater`` connections.
        """
        if self._thread and self._thread.isRunning():
            self._thread.quit()

    def _on_thread_destroyed(self) -> None:
        """Clear Python references after Qt has destroyed the worker thread."""
        self._thread = None
        self._worker = None
