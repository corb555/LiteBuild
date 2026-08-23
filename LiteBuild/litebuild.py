# litebuild.py

import argparse
import json
import logging
import sys
import traceback
from typing import Dict, List, Optional

from LiteBuild.build_engine import BuildEngine
from LiteBuild.build_logger import BuildLogger


def main():
    """The CLI for running LiteBuild."""
    parser = argparse.ArgumentParser(
        description="LiteBuild: A lightweight, dependency-aware build system for shell commands."
    )

    parser.add_argument(
        "config_file",
        help="Path to the config.yml file (must start with 'BUILD_').",
    )

    target_group = parser.add_mutually_exclusive_group()
    target_group.add_argument(
        "--profile",
        help="A named set of parameters to use for the build.",
    )
    target_group.add_argument(
        "--group",
        help="A profile group to run sequentially.",
    )
    target_group.add_argument(
        "--list-targets",
        action="store_true",
        help="Return configured profiles and profile groups as JSON.",
    )

    parser.add_argument(
        "--vars",
        nargs="+",
        metavar="KEY=value",
        help="Space-separated KEY=value pairs.",
    )
    parser.add_argument(
        "--step",
        help="If provided, build only up to this specific step.",
    )
    parser.add_argument(
        "--describe",
        action="store_true",
        help="Generate a Markdown description of the workflow.",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Path to save the description file (used with --describe).",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress informational messages.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable detailed debug logging.",
    )

    args = parser.parse_args()

    if args.list_targets:
        try:
            if args.describe:
                raise ValueError("--list-targets cannot be used with --describe.")
            if args.vars:
                raise ValueError("--list-targets does not support --vars.")
            if args.step:
                raise ValueError("--list-targets does not support --step.")
            if args.output:
                raise ValueError("--list-targets does not support --output.")

            engine = BuildEngine.from_file(args.config_file)

            result = {
                "ok": True,
                "profiles": list(engine.get_profile_list().keys()),
                "groups": list(engine.get_group_list().keys()),
            }

            print(json.dumps(result, indent=2))
            return

        except (FileNotFoundError, ValueError) as exc:
            result = {
                "ok": False,
                "error": "configuration_error",
                "message": str(exc),
            }
            print(json.dumps(result, indent=2))
            sys.exit(1)

        except Exception as exc:
            result = {
                "ok": False,
                "error": "internal_error",
                "message": str(exc),
            }
            print(json.dumps(result, indent=2))
            sys.exit(1)

    setup_logging(args.quiet, args.verbose)

    if args.describe:
        if args.group:
            parser.error("--describe does not support --group.")
        if not args.profile:
            parser.error("--describe requires --profile.")
    else:
        if not args.profile and not args.group and not args.vars:
            parser.error("A --profile, --group, or --vars must be provided to run a build.")

    if args.group and args.step:
        parser.error("--step is only supported with --profile, not --group.")

    cli_vars = parse_cli_vars(args.vars)
    if cli_vars is None:
        sys.exit(1)

    logger = BuildLogger(sys.stdout)
    success = False

    try:
        engine = BuildEngine.from_file(args.config_file, cli_vars=cli_vars)
        project_cfg = engine.config.get("PROJECT")

        if args.describe:
            description = engine.describe(profile_name=args.profile)

            if args.output:
                with open(args.output, "w", encoding="utf-8") as file_obj:
                    file_obj.write(description)
                logging.info(f"Workflow description saved to: {args.output}")
            else:
                print(description)

            success = True

        elif args.group:
            profiles_to_run = engine.resolve_profile_group(args.group)

            final_step_name = project_cfg.get("DEFAULT_WORKFLOW_STEP")
            if not final_step_name:
                raise ValueError(
                    "No DEFAULT_WORKFLOW_STEP found in config for group execution."
                )

            total_profiles = len(profiles_to_run)

            logging.info(f"Starting Profile Group: {args.group}")
            logging.info(f"Profiles to run: {', '.join(profiles_to_run)}")

            success = True

            for index, profile_name in enumerate(profiles_to_run, start=1):
                logging.info("")
                logging.info("=" * 80)
                logging.info(
                    f"({index}/{total_profiles}) Running Profile: {profile_name}\n"
                )

                profile_engine = BuildEngine.from_file(
                    args.config_file,
                    cli_vars=cli_vars,
                )

                profile_success = profile_engine.execute(
                    profile_name=profile_name,
                    final_step_name=final_step_name,
                    logger=logger,
                )

                if not profile_success:
                    logging.error(
                        f"Profile '{profile_name}' failed. "
                        f"Stopping Profile Group '{args.group}'."
                    )
                    success = False
                    break

                logging.info(f"✅ Profile '{profile_name}' finished.")

            if success:
                logging.info("")
                logging.info("=" * 80)
                logging.info(
                    f"✅ Profile Group '{args.group}' finished successfully."
                )
        else:
            final_step_name = None

            if args.step:
                final_step_name = args.step
                logging.info(f"Using workflow step: '{final_step_name}'")
            elif "DEFAULT_WORKFLOW_STEP" in engine.config:
                final_step_name = project_cfg.get("DEFAULT_WORKFLOW_STEP")
                logging.info(
                    f"Target step: '{final_step_name}' (from config file)"
                )
            else:
                raise ValueError(
                    "No workflow step specified. "
                    "Please provide a final step with the --step flag, or set a "
                    "DEFAULT_WORKFLOW_STEP in the PROJECT section of your configuration file."
                )

            profile_name = args.profile if args.profile else ""

            success = engine.execute(
                profile_name=profile_name,
                final_step_name=final_step_name,
                logger=logger,
            )

    except (FileNotFoundError, ValueError) as exc:
        logging.error(f"A configuration error occurred:\n{exc}")
        sys.exit(1)

    except Exception as exc:
        logging.error(f"{exc}")
        logging.debug(traceback.format_exc())
        sys.exit(1)

    if not success:
        sys.exit(1)


def setup_logging(quiet: bool = False, verbose: bool = False):
    """Configures the root logger for the application."""
    level = logging.INFO

    if quiet:
        level = logging.WARNING
    if verbose:
        level = logging.DEBUG

    logging.basicConfig(
        level=level,
        format="%(message)s",
        stream=sys.stdout,
    )


def parse_cli_vars(
    var_list: Optional[List[str]],
) -> Optional[Dict[str, str]]:
    """Parses a list of 'KEY=value' strings into a dictionary."""
    if not var_list:
        return {}

    try:
        return dict(item.split("=", 1) for item in var_list)
    except ValueError:
        logging.error(
            "Invalid format for --vars. Use 'KEY=value' separated by spaces."
        )
        return None


if __name__ == "__main__":
    main()
