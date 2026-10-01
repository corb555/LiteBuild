#!/usr/bin/env python3
"""Validate project command-line tool.

validate_environment is a stand-alone CLI tool which verifies that an execution environment contains the  binaries and versions required by a
project. Requirements and version-discovery rules are declared in YAML.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import locale
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import BinaryIO, Pattern, Sequence, TextIO

try:
    import yaml
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import InvalidVersion, Version
except ModuleNotFoundError as exc:  # pragma: no cover - dependency failure path
    print(f"❌ ERROR: Missing Python dependency '{exc.name}'. "
          "Install dependencies from requirements.txt.", file=sys.stderr, )
    raise SystemExit(2) from exc

EXIT_SUCCESS = 0
EXIT_REQUIREMENT_FAILURE = 1
EXIT_CONFIGURATION_ERROR = 2

MINIMUM_CONFIG_FILES = 1
MAXIMUM_CONFIG_FILES = 3
COMMAND_TIMEOUT_SECONDS = 5.0
MAXIMUM_OUTPUT_BYTES = 64 * 1024
OUTPUT_CHUNK_BYTES = 4 * 1024
PROCESS_POLL_SECONDS = 0.01
THREAD_JOIN_SECONDS = 1.0

TOOLS_SECTION = "tools"
REQUIRES_SECTION = "requires"
VERSION_GROUP = "version"
EXPECTED_TOOL_FIELDS = frozenset({"command", "version_argument", "match_pattern"})
ALLOWED_VERSION_ARGUMENTS = frozenset({"--version", "-version", "-V", "version"})
COMMAND_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


class ConfigurationError(ValueError):
    """Raised when invocation or configuration data is invalid."""


class SystemValidationError(RuntimeError):
    """Raised when a system error prevents reliable validation."""


class UniqueKeyLoader(yaml.SafeLoader):
    """YAML loader that rejects duplicate mapping keys and merge keys."""


def _construct_unique_mapping(
        loader: UniqueKeyLoader, node: yaml.nodes.MappingNode, deep: bool = False, ) -> dict[
    object, object]:
    """Construct a YAML mapping while rejecting ambiguous keys.

    Args:
        loader: Active YAML loader.
        node: YAML mapping node to construct.
        deep: Whether nested objects should be constructed deeply.

    Returns:
        The constructed mapping.

    Raises:
        yaml.constructor.ConstructorError: If a merge or duplicate key exists.
    """
    merge_tag = "tag:yaml.org,2002:merge"
    mapping: dict[object, object] = {}

    for key_node, value_node in node.value:
        if key_node.tag == merge_tag:
            raise yaml.constructor.ConstructorError("while constructing a mapping", node.start_mark,
                "YAML merge keys are not permitted", key_node.start_mark, )

        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError("while constructing a mapping", node.start_mark,
                "mapping keys must be scalar values", key_node.start_mark, ) from exc

        if duplicate:
            raise yaml.constructor.ConstructorError("while constructing a mapping", node.start_mark,
                f"duplicate key: {key!r}", key_node.start_mark, )

        mapping[key] = loader.construct_object(value_node, deep=deep)

    return mapping


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping, )


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Validated instructions for probing one external tool.

    Attributes:
        command: Bare executable name resolved through ``PATH``.
        version_argument: Approved argument used to request a version.
        match_pattern: Compiled expression containing one ``version`` group.
    """

    command: str
    version_argument: str
    match_pattern: Pattern[str]


CHECKS_SECTION = "checks"
EXPECTED_CHECK_FIELDS = frozenset({"command", "arguments", "help"})

@dataclass(frozen=True, slots=True)
class CheckDefinition:
    """Validated probe for a capability or driver (exit code 0 test)."""
    name: str
    command: str
    arguments: tuple[str, ...]
    help: str | None = None

@dataclass(frozen=True, slots=True)
class ValidationConfig:
    """Validated tool definitions, project requirements, and capability checks."""
    tools: dict[str, ToolDefinition]
    requires: dict[str, SpecifierSet]
    checks: dict[str, CheckDefinition]  # New field


@dataclass(frozen=True, slots=True)
class RequirementFailure:
    """A single failed environment requirement."""

    tool_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class CommandOutput:
    """Bounded output captured from a completed version command."""

    return_code: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    output_limit_exceeded: bool = False


@dataclass(slots=True)
class _CaptureState:
    """Mutable state shared by the two output-reader threads."""

    stdout_chunks: list[bytes]
    stderr_chunks: list[bytes]
    total_bytes: int
    limit_exceeded: threading.Event
    lock: threading.Lock
    reader_errors: list[OSError]

class ConfigLoader:
    """Load and strictly validate environment configuration files."""

    def load(self, paths: Sequence[Path]) -> ValidationConfig:
        if not MINIMUM_CONFIG_FILES <= len(paths) <= MAXIMUM_CONFIG_FILES:
            raise ConfigurationError("exactly one combined configuration or two split "
                                     "configuration files are required")

        sections: dict[str, object] = {}
        section_sources: dict[str, Path] = {}
        for path in paths:
            document = self._load_document(path)
            valid_sections = {TOOLS_SECTION, REQUIRES_SECTION, CHECKS_SECTION}
            unknown_sections = set(document) - valid_sections
            if unknown_sections:
                unknown = ", ".join(sorted(map(str, unknown_sections)))
                raise ConfigurationError(f"{path}: unknown top-level section(s): {unknown}")

            if len(paths) == MAXIMUM_CONFIG_FILES and len(document) != 1:
                raise ConfigurationError(f"{path}: each split configuration file must contain "
                                         "exactly one top-level section")

            for section, value in document.items():
                if section in sections:
                    first_source = section_sources[section]
                    raise ConfigurationError(f"section '{section}' is defined more than once "
                                             f"({first_source} and {path})")
                sections[section] = value
                section_sources[section] = path

        missing_sections = {TOOLS_SECTION, REQUIRES_SECTION} - set(sections)
        if missing_sections:
            missing = ", ".join(sorted(missing_sections))
            raise ConfigurationError(f"missing required section(s): {missing}")

        tools = self._validate_tools(sections[TOOLS_SECTION])
        requires = self._validate_requirements(sections[REQUIRES_SECTION], tools)

        # Parse optional checks
        checks: dict[str, CheckDefinition] = {}
        if CHECKS_SECTION in sections:
            checks = self._validate_checks(sections[CHECKS_SECTION])

        return ValidationConfig(tools=tools, requires=requires, checks=checks)

    def _validate_checks(self, value: object) -> dict[str, CheckDefinition]:
        """Validate the optional ``checks`` mapping."""
        if not isinstance(value, dict):
            raise ConfigurationError("'checks' must be a mapping")

        checks: dict[str, CheckDefinition] = {}
        for name, definition in value.items():
            if not isinstance(name, str) or not name:
                raise ConfigurationError("every check name must be a nonempty string")
            if not isinstance(definition, dict):
                raise ConfigurationError(f"check '{name}' definition must be a mapping")

            command = definition.get("command")
            if not isinstance(command, str) or not COMMAND_NAME_PATTERN.fullmatch(command):
                raise ConfigurationError(f"check '{name}' command must be one bare executable name")

            raw_args = definition.get("arguments", [])
            if not isinstance(raw_args, list) or not all(isinstance(a, str) for a in raw_args):
                raise ConfigurationError(f"check '{name}' arguments must be a list of strings")

            help_text = definition.get("help")
            if help_text is not None and not isinstance(help_text, str):
                raise ConfigurationError(f"check '{name}' help must be a string")

            fields = set(definition)
            missing_fields = {"command"} - fields
            unknown_fields = fields - EXPECTED_CHECK_FIELDS
            if missing_fields:
                raise ConfigurationError(f"check '{name}' is missing required 'command' field")
            if unknown_fields:
                unknown = ", ".join(sorted(map(str, unknown_fields)))
                raise ConfigurationError(f"check '{name}' has unknown field(s): {unknown}")

            checks[name] = CheckDefinition(
                name=name,
                command=command,
                arguments=tuple(raw_args),
                help=help_text,
            )
        return checks

    @staticmethod
    def _load_document(path: Path) -> dict[str, object]:
        """Read one strict, single-document YAML mapping."""
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ConfigurationError(f"cannot read {path}: {exc}") from exc

        try:
            document = yaml.load(text, Loader=UniqueKeyLoader)
        except yaml.YAMLError as exc:
            detail = str(exc).strip()
            raise ConfigurationError(f"cannot parse {path}: {detail}") from exc

        if not isinstance(document, dict):
            raise ConfigurationError(f"{path}: top-level YAML value must be a mapping")
        return document

    def _validate_tools(self, value: object) -> dict[str, ToolDefinition]:
        """Validate the complete ``tools`` mapping."""
        if not isinstance(value, dict):
            raise ConfigurationError("'tools' must be a mapping")

        tools: dict[str, ToolDefinition] = {}
        for name, definition in value.items():
            if not isinstance(name, str) or not name:
                raise ConfigurationError("every tool name must be a nonempty string")
            if not isinstance(definition, dict):
                raise ConfigurationError(f"tool '{name}' definition must be a mapping")

            fields = set(definition)
            missing_fields = EXPECTED_TOOL_FIELDS - fields
            unknown_fields = fields - EXPECTED_TOOL_FIELDS
            if missing_fields:
                missing = ", ".join(sorted(missing_fields))
                raise ConfigurationError(f"tool '{name}' is missing field(s): {missing}")
            if unknown_fields:
                unknown = ", ".join(sorted(map(str, unknown_fields)))
                raise ConfigurationError(f"tool '{name}' has unknown field(s): {unknown}")

            tools[name] = self._build_tool_definition(name, definition)
        return tools

    @staticmethod
    def _build_tool_definition(
            name: str, definition: dict[object, object], ) -> ToolDefinition:
        """Validate and construct one tool definition."""
        command = definition["command"]
        version_argument = definition["version_argument"]
        pattern_text = definition["match_pattern"]

        if not isinstance(command, str) or not COMMAND_NAME_PATTERN.fullmatch(command):
            raise ConfigurationError(f"tool '{name}' command must be one bare executable name")
        if (not isinstance(version_argument,
                           str) or version_argument not in ALLOWED_VERSION_ARGUMENTS):
            allowed = ", ".join(sorted(ALLOWED_VERSION_ARGUMENTS))
            raise ConfigurationError(f"tool '{name}' version_argument must be one of: {allowed}")
        if not isinstance(pattern_text, str) or not pattern_text:
            raise ConfigurationError(f"tool '{name}' match_pattern must be a nonempty string")

        try:
            pattern = re.compile(pattern_text)
        except re.error as exc:
            raise ConfigurationError(f"tool '{name}' has an invalid match_pattern: {exc}") from exc

        if pattern.groups != 1 or pattern.groupindex != {VERSION_GROUP: 1}:
            raise ConfigurationError(f"tool '{name}' match_pattern must contain exactly one "
                                     f"named group '(?P<{VERSION_GROUP}>...)' and no other "
                                     "capture groups")

        return ToolDefinition(command=command, version_argument=str(version_argument),
            match_pattern=pattern, )

    @staticmethod
    def _validate_requirements(
            value: object, tools: dict[str, ToolDefinition], ) -> dict[str, SpecifierSet]:
        """Validate the complete ``requires`` mapping."""
        if not isinstance(value, dict):
            raise ConfigurationError("'requires' must be a mapping")

        requires: dict[str, SpecifierSet] = {}
        for name, constraint in value.items():
            if not isinstance(name, str) or not name:
                raise ConfigurationError("every requirement name must be a nonempty string")
            if name not in tools:
                raise ConfigurationError(f"required tool '{name}' has no definition in 'tools'")
            if not isinstance(constraint, str) or not constraint.strip():
                raise ConfigurationError(f"requirement for '{name}' must be a nonempty string")
            try:
                requires[name] = SpecifierSet(constraint)
            except InvalidSpecifier as exc:
                raise ConfigurationError(
                    f"requirement for '{name}' is not valid PEP 440: {constraint!r}") from exc
        return requires


class MarkerFile:
    """Manage the validation marker file."""

    def __init__(self, path: Path | None) -> None:
        """Initialize marker management.

        Args:
            path: Requested marker path, or ``None`` when disabled.
        """
        if path is not None:
            self._validate_path(path)
        self._path = path

    @staticmethod
    def _validate_path(path: Path) -> None:
        """Enforce naming rules for marker files."""
        name = path.name
        if not (name.startswith(".") and name.endswith(".marker") and len(name) > len(".marker")):
            raise ValueError(f"'{name}' is invalid; marker filename must start with '.' "
                             "and end with '.marker' (e.g., '.validation.marker')")

    @classmethod
    def path_type(cls, value: str) -> Path:
        """Argparse-compatible type converter for marker file paths."""
        path = Path(value)
        try:
            cls._validate_path(path)
        except ValueError as exc:
            # Raising ArgumentTypeError gives standard argparse formatting
            raise argparse.ArgumentTypeError(str(exc)) from exc
        return path

    @property
    def path(self) -> Path | None:
        """Return the requested marker path."""
        return self._path

    def remove_existing(self) -> None:
        """Delete a previous marker before any configuration work begins.

        Raises:
            SystemValidationError: If the path is unsafe or cannot be removed.
        """
        if self._path is None:
            return

        try:
            metadata = self._path.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise SystemValidationError(f"cannot inspect marker {self._path}: {exc}") from exc

        if stat.S_ISLNK(metadata.st_mode):
            raise SystemValidationError(
                f"refusing to remove marker {self._path}: path is a symbolic link")
        if not stat.S_ISREG(metadata.st_mode):
            raise SystemValidationError(f"refusing to remove marker {self._path}: "
                                        "path is not a regular file")
        if metadata.st_size != 0:
            raise SystemValidationError(f"refusing to remove marker {self._path}: "
                                        f"file is not empty ({metadata.st_size} bytes)")

        try:
            self._path.unlink()
        except OSError as exc:
            raise SystemValidationError(f"cannot remove marker {self._path}: {exc}") from exc

    def create_atomic(self) -> None:
        """Atomically create an empty marker after successful validation."""
        if self._path is None:
            return

        parent = self._path.parent
        if not parent.is_dir():
            raise SystemValidationError(f"marker directory does not exist: {parent}")

        try:
            descriptor = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, )
        except FileExistsError as exc:
            raise SystemValidationError(
                f"refusing to create marker {self._path}: path already exists") from exc
        except OSError as exc:
            raise SystemValidationError(f"cannot create marker {self._path}: {exc}") from exc

        try:
            os.close(descriptor)
        except OSError as exc:
            self._path.unlink(missing_ok=True)
            raise SystemValidationError(f"cannot finalize marker {self._path}: {exc}") from exc


class BoundedCommandRunner:
    """Run version probes with a timeout and a combined output limit."""

    def run(self, executable: str, arguments: Sequence[str]) -> CommandOutput:

        """Execute one bounded two-token version command.

        Args:
            executable: Resolved executable path.
            argument: Approved version argument.

        Returns:
            Captured command status and bounded output.

        Raises:
            SystemValidationError: If process management fails unexpectedly.
        """
        try:
            cmd = [executable, *arguments]
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
                start_new_session=os.name == "posix",
            )
        except OSError as exc:
            raise SystemValidationError(
                f"cannot execute resolved command {executable!r}: {exc}"
            ) from exc

        if process.stdout is None or process.stderr is None:  # pragma: no cover
            self._terminate_process(process)
            raise SystemValidationError("failed to capture command output")

        state = _CaptureState(stdout_chunks=[], stderr_chunks=[], total_bytes=0,
            limit_exceeded=threading.Event(), lock=threading.Lock(), reader_errors=[], )
        stdout_thread = self._start_reader(process.stdout, state.stdout_chunks, state, )
        stderr_thread = self._start_reader(process.stderr, state.stderr_chunks, state, )

        deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
        timed_out = False
        try:
            while process.poll() is None:
                if state.limit_exceeded.is_set():
                    self._terminate_process(process)
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    self._terminate_process(process)
                    break
                time.sleep(PROCESS_POLL_SECONDS)
            return_code = process.wait()
        except OSError as exc:
            self._terminate_process(process)
            raise SystemValidationError(f"cannot manage command {executable!r}: {exc}") from exc
        finally:
            stdout_thread.join(THREAD_JOIN_SECONDS)
            stderr_thread.join(THREAD_JOIN_SECONDS)
            process.stdout.close()
            process.stderr.close()

        if stdout_thread.is_alive() or stderr_thread.is_alive():
            raise SystemValidationError(
                f"output readers did not terminate for command {executable!r}")
        if state.reader_errors:
            raise SystemValidationError(f"cannot read output from command {executable!r}: "
                                        f"{state.reader_errors[0]}")

        return CommandOutput(return_code=return_code, stdout=b"".join(state.stdout_chunks),
            stderr=b"".join(state.stderr_chunks), timed_out=timed_out,
            output_limit_exceeded=state.limit_exceeded.is_set(), )

    @staticmethod
    def _start_reader(
            stream: BinaryIO, chunks: list[bytes], state: _CaptureState, ) -> threading.Thread:
        """Start a daemon thread that drains one process output stream."""

        def read_stream() -> None:
            try:
                while chunk := stream.read(OUTPUT_CHUNK_BYTES):
                    with state.lock:
                        remaining = MAXIMUM_OUTPUT_BYTES - state.total_bytes
                        if remaining > 0:
                            kept = chunk[:remaining]
                            chunks.append(kept)
                            state.total_bytes += len(kept)
                        if len(chunk) > remaining:
                            state.limit_exceeded.set()
            except OSError as exc:
                state.reader_errors.append(exc)

        thread = threading.Thread(target=read_stream, daemon=True)
        thread.start()
        return thread

    @staticmethod
    def _terminate_process(process: subprocess.Popen[bytes]) -> None:
        """Best-effort termination of a probe and its child processes."""
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:  # pragma: no cover - Windows-specific fallback
                process.kill()
            process.wait(timeout=THREAD_JOIN_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            pass


class EnvironmentValidator:
    """Probe and validate every configured environment requirement."""

    def __init__(
            self, *, verbose: bool = False, output: TextIO = sys.stdout,
            runner: BoundedCommandRunner | None = None, ) -> None:
        """Initialize environment validation.

        Args:
            verbose: Whether to print successful probe details.
            output: Destination for verbose progress.
            runner: Optional command runner supplied for testing.
        """
        self._verbose = verbose
        self._output = output
        self._runner = runner or BoundedCommandRunner()

    def validate(self, config: ValidationConfig) -> list[RequirementFailure]:
        failures: list[RequirementFailure] = []

        # 1. Path A: Version-constrained tools
        for name, constraint in config.requires.items():
            tool = config.tools[name]
            self._verbose_line(f"[*] Checking tool '{name}' (version {constraint})...")
            failure = self._validate_tool(name, tool, constraint)
            if failure is not None:
                failures.append(failure)
                self._verbose_line(f"    -> Failure: {failure.reason}")

        # 2. Path B: Capability / Driver checks (exit code 0)
        for name, check in config.checks.items():
            self._verbose_line(f"[*] Running check '{name}' ({check.command} {' '.join(check.arguments)})...")
            failure = self._validate_check(name, check)
            if failure is not None:
                failures.append(failure)
                self._verbose_line(f"    -> Failure: {failure.reason}")

        return failures

    def _validate_check(self, name: str, check: CheckDefinition) -> RequirementFailure | None:
        resolved = shutil.which(check.command)
        if resolved is None:
            return RequirementFailure(name, f"Command '{check.command}' not found in PATH.")

        result = self._runner.run(resolved, check.arguments)
        if result.timed_out:
            return RequirementFailure(name, f"timed out after {COMMAND_TIMEOUT_SECONDS:g} seconds")
        if result.output_limit_exceeded:
            return RequirementFailure(name, f"output exceeded {MAXIMUM_OUTPUT_BYTES} bytes")
        if result.return_code != 0:
            detail = f"exit code {result.return_code}"
            if check.help:
                detail += f" ({check.help})"
            return RequirementFailure(name, detail)

        self._verbose_line("    -> Success")
        return None

    def _validate_tool(
            self, name: str, tool: ToolDefinition,
            constraint: SpecifierSet, ) -> RequirementFailure | None:
        """Validate one tool and return its ordinary failure, if any."""
        resolved = shutil.which(tool.command)
        if resolved is None:
            return RequirementFailure(name, "Not found. Make sure it is installed properly.")

        self._verbose_line(f"    -> Resolved: {resolved}")
        result = self._runner.run(resolved, [tool.version_argument])
        if result.timed_out:
            return RequirementFailure(name,
                f"version command timed out after {COMMAND_TIMEOUT_SECONDS:g} seconds", )
        if result.output_limit_exceeded:
            return RequirementFailure(name,
                f"version output exceeded {MAXIMUM_OUTPUT_BYTES} bytes", )
        if result.return_code != 0:
            return RequirementFailure(name,
                f"version command returned exit code {result.return_code}", )

        encoding = locale.getpreferredencoding(False) or "utf-8"
        try:
            stdout = result.stdout.decode(encoding)
            stderr = result.stderr.decode(encoding)
        except UnicodeDecodeError:
            return RequirementFailure(name, f"version output is not valid {encoding} text", )

        matches = [*tool.match_pattern.finditer(stdout), *tool.match_pattern.finditer(stderr), ]
        if not matches:
            return RequirementFailure(name, "version output did not match the configured pattern", )
        if len(matches) > 1:
            return RequirementFailure(name,
                "version output matched the configured pattern more than once", )

        extracted = matches[0].group(VERSION_GROUP)
        try:
            version = Version(extracted)
        except InvalidVersion:
            return RequirementFailure(name,
                f"extracted version {extracted!r} is not valid PEP 440", )

        self._verbose_line(f"    -> Extracted version: {version}")
        self._verbose_line(f"    -> Required: {constraint}")
        if version not in constraint:
            return RequirementFailure(name,
                f"Found version {version}. We require version {constraint}", )

        self._verbose_line("    -> Success")
        return None

    def _verbose_line(self, message: str) -> None:
        """Print one progress line only when verbose mode is enabled."""
        if self._verbose:
            print(message, file=self._output)

def _build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(description="Validate required external tools and versions.", )
    parser.add_argument("-c", "--config", required=True, nargs="+", type=Path,
        help="one combined YAML config or two split YAML configs", )
    parser.add_argument(
    "-m", "--marker",
    type=MarkerFile.path_type,  # <-- Catches ValueError and prints clean CLI error
    help="optional empty marker created only after complete success",
)
    parser.add_argument("-v", "--verbose", action="store_true", help="print probe details", )
    return parser


def _print_requirement_failures(
        failures: Sequence[RequirementFailure], error_output: TextIO, ) -> None:
    """Print the ordered collection of environment failures."""
    print("❌ ERROR: Environment validation failed. Update or install the following:", file=error_output)
    for failure in failures:
        print(f"-  {failure.tool_name}: {failure.reason}", file=error_output)


def main(
        argv: Sequence[str] | None = None, *, output: TextIO = sys.stdout,
        error_output: TextIO = sys.stderr, ) -> int:
    """Run the environment validator command.

    Args:
        argv: Optional arguments excluding the program name.
        output: Destination for verbose output.
        error_output: Destination for errors.

    Returns:
        ``0`` for success, ``1`` for collected requirement failures, or ``2``
        for invalid invocation, invalid configuration, or a system error.
    """
    parser = _build_parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)

    marker = MarkerFile(arguments.marker)
    try:
        marker.remove_existing()
        config = ConfigLoader().load(arguments.config)
        validator = EnvironmentValidator(verbose=arguments.verbose, output=output, )
        failures = validator.validate(config)
        if failures:
            _print_requirement_failures(failures, error_output)
            return EXIT_REQUIREMENT_FAILURE

        marker.create_atomic()
    except (ConfigurationError, SystemValidationError) as exc:
        print(f"❌ ERROR: {exc}", file=error_output)
        return EXIT_CONFIGURATION_ERROR

    if arguments.verbose:
        suffix = (f" Created marker: {marker.path}" if marker.path is not None else "")
        print(f"[+] Environment validated.{suffix}", file=output)
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
