# litebuild.py

import argparse
import json
import sys
import time
import traceback
from typing import Dict, List, Optional

from LiteBuild.build_engine import BuildEngine
from LiteBuild.build_logger import BuildLogger, LogLevel
from LiteBuild.status_emitter import StatusEmitter, emit_status
from LiteBuild.status_message import (
    BuildFinish,
    BuildStart,
    GroupFinish,
    GroupStart,
)


class _GroupProfileStatusEmitter:
    """Forward profile execution status while suppressing nested build envelopes."""

    def __init__(self, emitter: StatusEmitter) -> None:
        self._emitter = emitter

    def emit(self, message) -> None:
        if isinstance(message, (BuildStart, BuildFinish)):
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

        if args.group:
            success = _run_group(
                config_file=args.config_file,
                cli_vars=cli_vars,
                engine=engine,
                group_name=args.group,
                final_step_name=final_step_name,
                logger=logger,
                status_emitter=status_emitter,
            )
        else:
            profile_name = args.profile or ""
            success = engine.execute(
                profile_name=profile_name,
                final_step_name=final_step_name,
                logger=logger,
                status_emitter=status_emitter,
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

    if not success:
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

    """if not args.profile and not args.group and not args.vars:
        parser.error("A --profile, --group, or --vars build target must be provided.")"""

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
) -> bool:
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

    success = True
    failure_text = ""

    for index, profile_name in enumerate(profiles, start=1):
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
        )

        profile_elapsed = time.perf_counter() - profile_start

        if profile_success:
            logger.blank()
            logger.info(f"✅ PROFILE COMPLETE  {profile_name}  {profile_elapsed:.2f}s")
        else:
            success = False
            failure_text = f"Profile '{profile_name}' failed"
            logger.blank()
            logger.info(f"🔴 PROFILE FAILED  {profile_name}  {profile_elapsed:.2f}s")
            break

    group_elapsed = time.perf_counter() - group_start

    logger.blank()
    if success:
        logger.info(f"✅ GROUP COMPLETE  {group_name}  {group_elapsed:.2f}s")
        group_status_text = "Done"
    else:
        logger.info(f"🔴 GROUP FAILED  {group_name}  {group_elapsed:.2f}s")
        group_status_text = failure_text or "Group failed"

    emit_status(
        status_emitter,
        GroupFinish(
            name=group_name,
            success=success,
            elapsed_s=group_elapsed,
            status_text=group_status_text,
        ),
    )
    emit_status(
        status_emitter,
        BuildFinish(
            success=success,
            elapsed_s=group_elapsed,
            status_text="Done" if success else group_status_text,
        ),
    )

    return success


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
