import difflib
import hashlib
import json
import os
from pathlib import Path
import re

from platformdirs import PlatformDirs
import time
from typing import Dict, List, Optional

from YMLEditor.yaml_reader import ConfigLoader

from LiteBuild.build_executor import BuildExecutor
from LiteBuild.build_logger import BuildLogger, get_logger, setup_logger
from LiteBuild.build_planner import BuildPlanner
from LiteBuild.build_reporter import BuildReporter
from LiteBuild.schema import BUILD_SCHEMA, LiteBuildValidator, YAMLSection
from LiteBuild.status_emitter import StatusEmitter, emit_status
from LiteBuild.status_message import BuildFinish, BuildStart, ProfileFinish, ProfileStart



STATE_DIRECTORY = PlatformDirs("LiteBuild", appauthor=False).user_state_path
_STATE_PATH_HASH_LENGTH = 10


def _sanitize_state_component(value: str) -> str:
    """Return a filesystem-safe component for a LiteBuild state filename."""
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    sanitized = sanitized.strip("._-")
    return sanitized or "unnamed"


def get_state_file(config_filepath: str | Path, profile_name: str = "") -> Path:
    """Return the canonical state-file path for one config/profile context.

    State identity is defined by the canonical absolute path of the LiteBuild
    configuration plus the selected profile. A profile-less build uses the
    LiteBuild-reserved context name ``_default``.
    """
    config_path = Path(config_filepath).expanduser().resolve()
    canonical_path = os.path.normcase(str(config_path))
    path_hash = hashlib.sha256(canonical_path.encode("utf-8")).hexdigest()[
        :_STATE_PATH_HASH_LENGTH
    ]

    config_name = _sanitize_state_component(config_path.stem)

    if profile_name:
        if profile_name.startswith("_"):
            raise ValueError(
                "Profile names beginning with '_' are reserved by LiteBuild."
            )
        context_name = _sanitize_state_component(profile_name)
    else:
        context_name = "_default"

    filename = f"path_{path_hash}_{config_name}_{context_name}.json"
    return STATE_DIRECTORY / filename


class BuildEngine:
    """High-level facade for planning and executing LiteBuild workflows."""

    def __init__(
        self,
        config_data: dict,
        config_filepath: str | Path | None = None,
        max_workers: int = 4,
        cli_vars: Optional[Dict] = None,
    ):
        """Initialize the build engine and apply command-line overrides."""
        if cli_vars:
            if YAMLSection.GENERAL not in config_data:
                config_data[YAMLSection.GENERAL] = {}
            config_data[YAMLSection.GENERAL].update(cli_vars)

        self.max_workers = max_workers
        self.config = config_data
        self.config_filepath = (
            Path(config_filepath).expanduser().resolve()
            if config_filepath is not None
            else None
        )

        input_dir = config_data.get(YAMLSection.GENERAL, {}).get("INPUT_DIRECTORY")
        if input_dir:
            path = Path(input_dir)
            if not path.exists():
                raise FileNotFoundError(
                    "Configuration error: INPUT_DIRECTORY does not exist.\n"
                    f"  Path: {path.absolute()}"
                )
            if not path.is_dir():
                raise FileNotFoundError(
                    "Configuration error: INPUT_DIRECTORY is not a directory.\n"
                    f"  Path: {path.absolute()}"
                )

    @classmethod
    def from_file(
        cls,
        config_filepath: str,
        max_workers: int | None = None,
        cli_vars: Optional[Dict] = None,
    ):
        """Create a BuildEngine from a validated LiteBuild configuration file.

        ``max_workers`` is optional for read-only operations such as profile
        discovery and workflow description. Build execution requires it.
        """
        loader = ConfigLoader(BUILD_SCHEMA, validator_class=LiteBuildValidator)
        config_data = loader.read(config_file=Path(config_filepath), normalize=True)
        return cls(
            config_data,
            config_filepath=config_filepath,
            max_workers=max_workers,
            cli_vars=cli_vars,
        )

    def execute(
        self,
        final_step_name: str,
        profile_name: str = "",
        logger: Optional[BuildLogger] = None,
        status_emitter: Optional[StatusEmitter] = None,
        force_rebuild: bool = False,
        profile_index: int = 1,
        profile_total: int = 1,
    ) -> bool:
        """Plan and execute one LiteBuild build.

        Human-readable diagnostic output is written through ``BuildLogger``.
        Structured build state is emitted independently through ``StatusEmitter``.

        Profile/group lifecycle messages are owned by the higher-level
        orchestration layer. This method owns the build envelope and delegates
        step-level status to ``BuildExecutor``.

        Raises:
            RuntimeError: If execution is requested without ``max_workers``.
        """
        if self.max_workers is None:
            self.max_workers = 4

        if logger is None:
            logger = get_logger()
        setup_logger(logger)

        build_start = time.perf_counter()

        logger.blank()
        logger.info("🔵 BUILD")
        logger.info(f"   Target: {final_step_name}")
        if profile_name:
            logger.info(f"   PROFILE  {profile_name}")
        logger.blank()

        emit_status(
            status_emitter,
            BuildStart(target=final_step_name),
        )

        success = False
        failure_text = ""

        try:
            if self.config_filepath is None:
                raise ValueError(
                    "Build execution requires the LiteBuild configuration file path "
                    "so a unique state file can be resolved."
                )

            state_file = get_state_file(self.config_filepath, profile_name)
            state_manager = BuildStateManager(state_file)
            planner = BuildPlanner(self.config, state_manager.load_state())
            plan = planner.plan_build(
                profile_name,
                final_step_name,
                force_rebuild=force_rebuild,
            )

            steps_to_run = tuple(step.node_name for step in plan.steps_to_run)
            emit_status(
                status_emitter,
                ProfileStart(
                    name=profile_name,
                    index=profile_index,
                    total=profile_total,
                    step_total=len(plan.steps_to_run) + len(plan.steps_to_skip),
                    step_run_total=len(plan.steps_to_run),
                    steps_to_run=steps_to_run,
                ),
            )

            executor = BuildExecutor(state_manager, self.config, max_workers=self.max_workers)
            success = executor.execute_plan(
                plan,
                logger,
                status_emitter=status_emitter,
            )

            if not success:
                failure_text = f"Build failed for target '{final_step_name}'"

        except (FileNotFoundError, ValueError) as exc:
            failure_text = str(exc)
            logger.blank()
            logger.error(failure_text)
            success = False

        elapsed_s = time.perf_counter() - build_start

        logger.blank()
        if success:
            logger.info(f"✅ BUILD COMPLETE  {elapsed_s:.2f}s")
            status_text = "Done"
        else:
            status_text = failure_text or "Build failed"
            logger.info(f"🔴 BUILD FAILED  {elapsed_s:.2f}s")
            if failure_text:
                logger.info(f"   {failure_text}")

        emit_status(
            status_emitter,
            ProfileFinish(
                name=profile_name,
                success=success,
                elapsed_s=elapsed_s,
                status_text=status_text,
            ),
        )

        emit_status(
            status_emitter,
            BuildFinish(
                success=success,
                elapsed_s=elapsed_s,
                status_text=status_text,
            ),
        )

        return success

    def get_group_list(self) -> dict:
        """Return configured profile groups."""
        return self.config.get(YAMLSection.PROFILE_GROUPS, {})

    def get_profile_list(self) -> dict:
        """Return configured profiles."""
        return self.config.get(YAMLSection.PROFILES, {})

    def has_profile(self, profile_name: str) -> bool:
        """Return whether a profile exists."""
        return profile_name in self.get_profile_list()

    def resolve_profile_group(self, group_name: str) -> List[str]:
        """Resolve a profile group to its ordered profile names.

        Raises:
            ValueError: If the group is missing, empty, or references an
                undefined profile.
        """
        profile_groups = self.get_group_list()

        if group_name not in profile_groups:
            available = list(profile_groups.keys())
            matches = difflib.get_close_matches(group_name, available, n=1, cutoff=0.6)
            hint = f"\n  Did you mean '{matches[0]}'?" if matches else ""

            if available:
                available_text = "\n  - ".join(available)
                available_message = f"\nAvailable profile groups:\n  - {available_text}"
            else:
                available_message = "\nNo profile groups are configured."

            raise ValueError(
                f"Profile group '{group_name}' not found.{hint}{available_message}"
            )

        profiles = list(profile_groups[group_name])
        if not profiles:
            raise ValueError(f"Profile group '{group_name}' does not contain any profiles.")

        configured_profiles = self.get_profile_list()
        missing_profiles = [
            profile_name
            for profile_name in profiles
            if profile_name not in configured_profiles
        ]

        if missing_profiles:
            missing_text = "\n  - ".join(missing_profiles)
            raise ValueError(
                f"Profile group '{group_name}' references undefined profiles:"
                f"\n  - {missing_text}"
            )

        return profiles

    def describe(self, profile_name: str) -> str:
        """Generate a Markdown description of the workflow for a profile."""
        reporter = BuildReporter(self.config)
        return reporter.describe_workflow(profile_name)


class BuildStateManager:
    """Load and save LiteBuild incremental-build state."""

    def __init__(self, state_file_path: str | Path):
        self.state_file_path = Path(state_file_path)

    def load_state(self) -> Dict:
        """Load build state, returning an empty state if none is usable."""
        if not os.path.exists(self.state_file_path):
            return {}

        try:
            with open(self.state_file_path, "r", encoding="utf-8") as file_obj:
                return json.load(file_obj)
        except (OSError, json.JSONDecodeError):
            return {}

    def save_state(self, state: Dict) -> None:
        """Persist build state."""
        try:
            self.state_file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file_path, "w", encoding="utf-8") as file_obj:
                json.dump(state, file_obj, indent=2)
        except OSError as exc:
            raise OSError(
                f"Could not write to state file '{self.state_file_path}': {exc}"
            ) from exc


def setup_worker_logger(logger: BuildLogger) -> None:
    """Set the process-local logger for a worker process."""
    setup_logger(logger)
