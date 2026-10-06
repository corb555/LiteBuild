# litebuild.py

import argparse
import json
import sys
import threading
import time
import traceback
from typing import Dict, List, Optional

from LiteBuild.build_engine import BuildEngine, BuildResult
from LiteBuild.build_logger import BuildLogger, LogLevel
from LiteBuild.status_emitter import StatusEmitter, emit_status
from LiteBuild.status_message import (
    BuildFinish,
    BuildStart,
    BuildStopped,
    BuildStopping,
    GroupFinish,
    GroupStart,
)


class StopToken:
    """Thread-safe cooperative stop state shared across the active build."""

    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def stop_requested(self) -> bool:
        """Return whether the user has requested that no new steps start."""
        return self._event.is_set()

    def request_stop(self) -> None:
        """Prevent future step dispatch without interrupting running steps."""
        self._event.set()


def _start_control_reader(
    stop_token: StopToken,
    status_emitter: StatusEmitter | None,
) -> threading.Thread:
    """Start the daemon that receives NDJSON control messages from stdin."""
    thread = threading.Thread(
        target=_read_control_messages,
        args=(stop_token, status_emitter),
        name="litebuild-control",
        daemon=True,
    )
    thread.start()
    return thread


def _read_control_messages(
    stop_token: StopToken,
    status_emitter: StatusEmitter | None,
) -> None:
    """Read newline-delimited JSON control commands from stdin.

    Supported messages:
        {"command": "stop"}

    Malformed or unknown messages are ignored. A newly accepted stop request is
    acknowledged immediately on the structured status stream.
    """
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not isinstance(message, dict):
            continue

        if message.get("command") != "stop" or stop_token.stop_requested:
            continue

        stop_token.request_stop()
        emit_status(
            status_emitter,
            BuildStopping(status_text="Stopping"),
        )


class _GroupProfileStatusEmitter:
    """Forward  execution status while suppressing nested build envelopes."""

    def __init__(self, emitter: StatusEmitter) -> None:
        self._emitter = emitter

    def emit(self, message) -> None:
        if isinstance(message, (BuildStart, BuildStopped, BuildFinish)):
            return
        self._emitter.emit(message)


def main() -> None:
    """Run the LiteBuild command-line interface."""
    parser = argparse.ArgumentParser(
        description="LiteBuild: a lightweight, dependency-aware build system for shell commands."
    )

    parser.add_argument(
        "config_file",
        help="Path to the LiteBuild configuration file.",
    )

    target_group = parser.add_mutually_exclusive_group()
    target_group.add_argument(
        "--profile",
        help="Run one configured profile.",
    )
    target_group.add_argument(
        "--group",
        help="Run a configured profile group sequentially.",
    )
    target_group.add_argument(
        "--list-targets",
        action="store_true",
        help="Return configured profiles and profile groups as JSON.",
    )

    """ parser.add_argument(
        "--vars",
        nargs="+",
        metavar="KEY=value",
        help="Space-separated configuration overrides.",
    )"""

    parser.add_argument(
        "--step",
        help="Build only through the specified workflow step.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Maximum workers. Default: 4.",
    )
    parser.add_argument(
        "--describe",
        action="store_true",
        help="Generate a description of the workflow.",
    )
    parser.add_argument(
        "--describe-output",
        "-o",
        dest="describe_output",
        help="Write the workflow description to this file.",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress informational text logging.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable detailed diagnostic text logging.",
    )
    parser.add_argument(
        "--status-format",
        choices=("ndjson",),
        help="Emit structured build status to stderr.",
    )

    args = parser.parse_args()

    _validate_cli_args(parser, args)

    if args.list_targets:
        _list_targets(args.config_file)
        return

    """cli_vars = parse_cli_vars(args.vars)
    if cli_vars is None:
        sys.exit(1)"""
    cli_vars = ""

    log_level = _resolve_log_level(args.quiet, args.verbose)
    logger = BuildLogger(sys.stdout, log_level=log_level)
    status_emitter = StatusEmitter(sys.stderr) if args.status_format == "ndjson" else None

    try:
        engine = BuildEngine.from_file(args.config_file, cli_vars=cli_vars, max_workers=args.max_workers)
        project_cfg = engine.config["PROJECT"]

        if args.describe:
            _run_describe(engine, args.profile, args.describe_output, logger)
            return

        final_step_name = _resolve_target_step(engine, args.step)

        stop_token = StopToken()
        _start_control_reader(stop_token, status_emitter)

        if args.group:
            outcome = _run_group(
                config_file=args.config_file,
                cli_vars=cli_vars,
                engine=engine,
                group_name=args.group,
                final_step_name=final_step_name,
                logger=logger,
                status_emitter=status_emitter,
                stop_token=stop_token,
            )
        else:
            profile_name = args.profile or ""
            outcome = engine.execute(
                profile_name=profile_name,
                final_step_name=final_step_name,
                logger=logger,
                status_emitter=status_emitter,
                stop_token=stop_token,
            )

    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        logger.blank()
        logger.error(f"Configuration error: {exc}")
        sys.exit(1)

    except Exception as exc:
        logger.blank()
        logger.error(str(exc))
        logger.error(traceback.format_exc())
        sys.exit(1)

    finally:
        logger.close()

    if outcome is BuildResult.FAILED:
        sys.exit(1)


def _validate_cli_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Validate combinations that argparse cannot express directly."""
    if args.list_targets:
        if args.describe:
            parser.error("--list-targets cannot be used with --describe.")
        """if args.vars:
            parser.error("--list-targets does not support --vars.")"""
        if args.step:
            parser.error("--list-targets does not support --step.")
        if args.describe_output:
            parser.error("--list-targets does not support --describe-output.")
        if args.status_format:
            parser.error("--list-targets does not emit build status.")
        return

    if args.describe:
        if args.group:
            parser.error("--describe does not support --group.")
        if not args.profile:
            parser.error("--describe requires --profile.")
        if args.status_format:
            parser.error("--describe does not emit build status.")
        return

    if args.group and args.step:
        parser.error("--step is supported for profile builds, not group builds.")


def _list_targets(config_file: str) -> None:
    """Write configured profile and group names as JSON."""
    try:
        engine = BuildEngine.from_file(config_file)
        result = {
            "ok": True,
            "profiles": list(engine.get_profile_list().keys()),
            "groups": list(engine.get_group_list().keys()),
        }
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        result = {
            "ok": False,
            "error": "configuration_error",
            "message": str(exc),
        }
    except Exception as exc:
        result = {
            "ok": False,
            "error": "internal_error",
            "message": str(exc),
        }

    print(json.dumps(result, indent=2))
    if not result["ok"]:
        sys.exit(1)


def _run_describe(
    engine: BuildEngine,
    profile_name: str,
    output_path: Optional[str],
    logger: BuildLogger,
) -> None:
    """Generate or save a workflow description."""
    description = engine.describe(profile_name=profile_name)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(description)
        logger.info(f"Workflow description saved: {output_path}")
    else:
        print(description)


def _resolve_target_step(engine: BuildEngine, explicit_step: Optional[str]) -> str:
    """Resolve the requested final workflow step."""
    if explicit_step:
        return explicit_step

    project_cfg = engine.config["PROJECT"]
    final_step_name = project_cfg["DEFAULT_WORKFLOW_STEP"]
    return final_step_name


def _run_group(
    *,
    config_file: str,
    cli_vars: Dict[str, str],
    engine: BuildEngine,
    group_name: str,
    final_step_name: str,
    logger: BuildLogger,
    status_emitter: Optional[StatusEmitter],
    stop_token: StopToken,
) -> BuildResult:
    """Run a configured profile group sequentially."""
    profiles = engine.resolve_profile_group(group_name)
    total_profiles = len(profiles)
    group_start = time.perf_counter()

    logger.blank()
    logger.info("🔵 BUILD")
    logger.info(f"   Target: {final_step_name}")
    logger.info(f"ℹ️  GROUP  {group_name}")
    logger.info(f"   Profiles: {', '.join(profiles)}")
    logger.blank()

    emit_status(
        status_emitter,
        BuildStart(target=final_step_name),
    )
    emit_status(
        status_emitter,
        GroupStart(
            name=group_name,
            index=1,
            total=1,
            profiles=tuple(profiles),
        ),
    )

    outcome = BuildResult.COMPLETED
    failure_text = ""

    for index, profile_name in enumerate(profiles, start=1):
        if stop_token.stop_requested:
            outcome = BuildResult.STOPPED
            break

        profile_start = time.perf_counter()

        logger.blank()
        logger.info(f"   PROFILE  {profile_name} [{index}/{total_profiles}]")
        logger.blank()

        # Profile lifecycle status is emitted after planning, where the
        # authoritative step counts are available.

        profile_engine = BuildEngine.from_file(
            config_file,
            max_workers=engine.max_workers,
            cli_vars=cli_vars,
        )
        profile_status_emitter = (
            _GroupProfileStatusEmitter(status_emitter)
            if status_emitter
            else None
        )

        profile_success = profile_engine.execute(
            profile_name=profile_name,
            final_step_name=final_step_name,
            logger=logger,
            status_emitter=profile_status_emitter,
            profile_index=index,
            profile_total=total_profiles,
            stop_token=stop_token,
        )

        profile_elapsed = time.perf_counter() - profile_start
        profile_outcome = profile_success

        if profile_outcome is BuildResult.COMPLETED:
            logger.blank()
            logger.info(f"✅ PROFILE COMPLETE  {profile_name}  {profile_elapsed:.2f}s")
        elif profile_outcome is BuildResult.STOPPED:
            outcome = BuildResult.STOPPED
            logger.blank()
            logger.info(f"■ PROFILE STOPPED  {profile_name}  {profile_elapsed:.2f}s")
            break
        else:
            outcome = BuildResult.FAILED
            failure_text = f"Profile '{profile_name}' failed"
            logger.blank()
            logger.info(f"🔴 PROFILE FAILED  {profile_name}  {profile_elapsed:.2f}s")
            break

    group_elapsed = time.perf_counter() - group_start

    logger.blank()
    if outcome is BuildResult.COMPLETED:
        logger.info(f"✅ GROUP COMPLETE  {group_name}  {group_elapsed:.2f}s")
        group_status_text = "Done"
        status_success = True
    elif outcome is BuildResult.STOPPED:
        logger.info(f"■ GROUP STOPPED  {group_name}  {group_elapsed:.2f}s")
        group_status_text = "Stopped"
        # The current status protocol has only success/failure. Step 4 adds
        # explicit stopping/stopped messages; until then, stopped is not failure.
        status_success = True
    else:
        logger.info(f"🔴 GROUP FAILED  {group_name}  {group_elapsed:.2f}s")
        group_status_text = failure_text or "Group failed"
        status_success = False

    emit_status(
        status_emitter,
        GroupFinish(
            name=group_name,
            success=status_success,
            elapsed_s=group_elapsed,
            status_text=group_status_text,
        ),
    )
    if outcome is BuildResult.STOPPED:
        emit_status(
            status_emitter,
            BuildStopped(
                elapsed_s=group_elapsed,
                status_text="Stopped",
            ),
        )

    emit_status(
        status_emitter,
        BuildFinish(
            success=status_success,
            elapsed_s=group_elapsed,
            status_text=group_status_text,
        ),
    )

    return outcome


def _resolve_log_level(quiet: bool, verbose: bool) -> LogLevel:
    """Resolve the requested human-readable logging level."""
    if verbose:
        return LogLevel.DEBUG
    if quiet:
        return LogLevel.WARNING
    return LogLevel.INFO


def parse_cli_vars(var_list: Optional[List[str]]) -> Optional[Dict[str, str]]:
    """Parse command-line KEY=value configuration overrides."""
    if not var_list:
        return {}

    try:
        return dict(item.split("=", 1) for item in var_list)
    except ValueError:
        print(
            "Invalid --vars format. Use space-separated KEY=value pairs.",
            file=sys.stderr,
        )
        return None


if __name__ == "__main__":
    main()
