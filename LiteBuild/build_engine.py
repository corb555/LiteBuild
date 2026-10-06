import difflib
from enum import Enum, auto
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from platformdirs import PlatformDirs
import time
from typing import Dict, List, Optional, Protocol

from YMLEditor.yaml_reader import ConfigLoader

from LiteBuild.build_executor import BuildExecutor
from LiteBuild.build_logger import BuildLogger, get_logger, setup_logger
from LiteBuild.build_planner import BuildPlanner
from LiteBuild.build_reporter import BuildReporter
from LiteBuild.schema import BUILD_SCHEMA, LiteBuildValidator, YAMLSection
from LiteBuild.status_emitter import StatusEmitter, emit_status
from LiteBuild.status_message import BuildFinish, BuildStart, BuildStopped, ProfileFinish, ProfileStart



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
        print("[BUILD_ENGINE] WARNING - no profile name")

    filename = f"path_{path_hash}_{config_name}_{context_name}.json"
    return STATE_DIRECTORY / filename


class StopTokenProtocol(Protocol):
    """Minimal cooperative-stop contract consumed by the build engine."""

    @property
    def stop_requested(self) -> bool:
        """Return whether no additional build steps should be started."""
        ...


class BuildResult(Enum):
    """Outcome of one LiteBuild profile build."""

    COMPLETED = auto()
    STOPPED = auto()
    FAILED = auto()



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
        stop_token: StopTokenProtocol | None = None,
    ) -> BuildResult:
        """Plan and execute one LiteBuild build.

        Human-readable diagnostic output is written through ``BuildLogger``.
        Structured build state is emitted independently through ``StatusEmitter``.

        Profile/group lifecycle messages are owned by the higher-level
        orchestration layer. This method owns the build envelope and delegates
        step-level status to ``BuildExecutor``.

        A cooperative stop request prevents the executor from dispatching new
        steps while allowing already-running steps to finish. The returned
        ``BuildResult`` distinguishes that condition from build failure.

        Args:
            stop_token: Optional cooperative-stop state shared with the caller
                and ``BuildExecutor``.

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

        result = BuildResult.FAILED
        failure_text = ""

        try:
            if self.config_filepath is None:
                raise ValueError(
                    "Build execution requires the LiteBuild configuration file path "
                    "so a unique state file can be resolved."
                )

            state_file = get_state_file(self.config_filepath, profile_name)
            state_manager = BuildStateManager(state_file)
            build_state = state_manager.load_state()

            planner = BuildPlanner(self.config, build_state)
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

            executor = BuildExecutor(
                state_manager,
                build_state,
                self.config,
                max_workers=self.max_workers,
                stop_token=stop_token,
            )
            execution_succeeded = executor.execute_plan(
                plan,
                logger,
                status_emitter=status_emitter,
            )

            if not execution_succeeded:
                result = BuildResult.FAILED
                failure_text = f"Build failed for target '{final_step_name}'"
            elif executor.stopped:
                result = BuildResult.STOPPED
            else:
                result = BuildResult.COMPLETED

        except (FileNotFoundError, ValueError) as exc:
            failure_text = str(exc)
            logger.blank()
            logger.error(failure_text)
            result = BuildResult.FAILED

        elapsed_s = time.perf_counter() - build_start

        logger.blank()
        if result is BuildResult.COMPLETED:
            logger.info(f"✅ BUILD COMPLETE  {elapsed_s:.2f}s")
            status_text = "Done"
            status_success = True
        elif result is BuildResult.STOPPED:
            logger.info(f"■ BUILD STOPPED  {elapsed_s:.2f}s")
            status_text = "Stopped"
            # The current status messages still expose only a success boolean.
            # Step 4 adds explicit stopping/stopped protocol messages, so stopped
            # must not be represented as a failure in the interim.
            status_success = True
        else:
            status_text = failure_text or "Build failed"
            logger.info(f"🔴 BUILD FAILED  {elapsed_s:.2f}s")
            if failure_text:
                logger.info(f"   {failure_text}")
            status_success = False

        emit_status(
            status_emitter,
            ProfileFinish(
                name=profile_name,
                success=status_success,
                elapsed_s=elapsed_s,
                status_text=status_text,
            ),
        )

        if result is BuildResult.STOPPED:
            emit_status(
                status_emitter,
                BuildStopped(
                    elapsed_s=elapsed_s,
                    status_text="Stopped",
                ),
            )

        emit_status(
            status_emitter,
            BuildFinish(
                success=status_success,
                elapsed_s=elapsed_s,
                status_text=status_text,
            ),
        )

        return result

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
    """Load, validate, and atomically save LiteBuild incremental-build state."""

    def __init__(self, state_file_path: str | Path):
        self.state_file_path = Path(state_file_path)

    def load_state(self) -> Dict:
        """Load and validate build state.

        A missing state file is the normal first-run case for a build/profile and
        returns an empty state. Existing files must be readable, valid JSON, and
        conform to the current state structure; failures are never silently
        treated as an empty state.
        """
        if not self.state_file_path.exists():
            return {}

        try:
            with self.state_file_path.open("r", encoding="utf-8") as file_obj:
                state = json.load(file_obj)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid LiteBuild state file '{self.state_file_path}': "
                f"malformed JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
            ) from exc
        except OSError as exc:
            raise OSError(
                f"Could not read LiteBuild state file '{self.state_file_path}': {exc}"
            ) from exc

        self._validate_state(state)
        return state

    def _validate_state(self, state: object) -> None:
        """Validate the existing state-file structure without changing its format."""
        if not isinstance(state, dict):
            raise ValueError(
                f"Invalid LiteBuild state file '{self.state_file_path}': "
                "top-level JSON value must be an object."
            )

        for node_name, record in state.items():
            if not isinstance(node_name, str) or not node_name:
                raise ValueError(
                    f"Invalid LiteBuild state file '{self.state_file_path}': "
                    "every state key must be a non-empty step name."
                )

            if not isinstance(record, dict):
                raise ValueError(
                    f"Invalid LiteBuild state for step '{node_name}' in "
                    f"'{self.state_file_path}': state record must be an object."
                )

            missing = [
                field for field in ("output", "hashes", "mtime")
                if field not in record
            ]
            if missing:
                raise ValueError(
                    f"Invalid LiteBuild state for step '{node_name}' in "
                    f"'{self.state_file_path}': missing required field(s): "
                    + ", ".join(missing)
                )

            if not isinstance(record["output"], str) or not record["output"]:
                raise ValueError(
                    f"Invalid LiteBuild state for step '{node_name}' in "
                    f"'{self.state_file_path}': 'output' must be a non-empty string."
                )

            hashes = record["hashes"]
            if not isinstance(hashes, dict):
                raise ValueError(
                    f"Invalid LiteBuild state for step '{node_name}' in "
                    f"'{self.state_file_path}': 'hashes' must be an object."
                )

            for hash_name in ("command", "inputs", "params"):
                if hash_name not in hashes:
                    raise ValueError(
                        f"Invalid LiteBuild state for step '{node_name}' in "
                        f"'{self.state_file_path}': missing hash '{hash_name}'."
                    )
                if not isinstance(hashes[hash_name], str) or not hashes[hash_name]:
                    raise ValueError(
                        f"Invalid LiteBuild state for step '{node_name}' in "
                        f"'{self.state_file_path}': hash '{hash_name}' must be "
                        "a non-empty string."
                    )

            mtime = record["mtime"]
            if isinstance(mtime, bool) or not isinstance(mtime, (int, float)):
                raise ValueError(
                    f"Invalid LiteBuild state for step '{node_name}' in "
                    f"'{self.state_file_path}': 'mtime' must be numeric."
                )

    def save_state(self, state: Dict) -> None:
        """Atomically persist build state using the existing JSON format."""
        self._validate_state(state)
        self.state_file_path.parent.mkdir(parents=True, exist_ok=True)

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.state_file_path.parent,
                prefix=f".{self.state_file_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as file_obj:
                temp_path = Path(file_obj.name)
                json.dump(state, file_obj, indent=2)
                file_obj.flush()
                os.fsync(file_obj.fileno())

            os.replace(temp_path, self.state_file_path)
            temp_path = None
        except OSError as exc:
            raise OSError(
                f"Could not write LiteBuild state file '{self.state_file_path}': {exc}"
            ) from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass


def setup_worker_logger(logger: BuildLogger) -> None:
    """Set the process-local logger for a worker process."""
    setup_logger(logger)
