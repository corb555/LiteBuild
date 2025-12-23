#!/usr/bin/env python
# litebuild.py


import argparse
import logging
import sys
import traceback
from typing import Dict, List, Optional

from build_engine import BuildEngine
from build_logger import BuildLogger


def main():
    """The  CLI for running LiteBuild."""
    parser = argparse.ArgumentParser(
        description="LiteBuild: A lightweight, dependency-aware build system for shell commands."
    )

    parser.add_argument("config_file", help="Path to the config.yml file (must start with 'LB_').")
    parser.add_argument("--profile", help="A named set of parameters to use for the build.")
    parser.add_argument(
        "--vars", nargs='+', metavar="KEY=value", help="Space-separated KEY=value pairs."
    )
    parser.add_argument("--step", help="If provided, build only up to this specific step.")
    parser.add_argument(
        "--describe", action='store_true', help="Generate a Markdown description of the workflow."
    )
    parser.add_argument(
        "--output", "-o", help="Path to save the description file (used with --describe)."
    )
    parser.add_argument(
        "--quiet", "-q", action='store_true', help="Suppress informational messages."
    )

    # verbose flag for debug logging
    parser.add_argument(
        "--verbose", "-v", action='store_true', help="Enable detailed debug logging."
    )

    args = parser.parse_args()

    # Configure logging based on the quiet and verbose flags
    setup_logging(args.quiet, args.verbose)

    if not args.profile and not args.vars:
        parser.error("A --profile or --vars must be provided to run a build.")
        sys.exit(1)

    cli_vars = parse_cli_vars(args.vars)
    if cli_vars is None:
        sys.exit(1)

    # --- The CLI creates a BuildLogger pointing to stdout ---
    logger = BuildLogger(sys.stdout)

    try:
        engine = BuildEngine.from_file(args.config_file, cli_vars=cli_vars)
        final_step_name = None
        if args.step:
            #  CLI argument always takes precedence.
            final_step_name = args.step
            logging.info(f"--- Using workflow step: '{final_step_name}' ---")
        elif "DEFAULT_WORKFLOW_STEP" in engine.config:
            #  Fallback to DEFAULT_WORKFLOW_STEP from config.
            final_step_name = engine.config["DEFAULT_WORKFLOW_STEP"]
            logging.info(
                f"--- Using default workflow step from config file: '{final_step_name}' ---"
                )
        else:
            #  Failure condition.
            logging.error(
                "❌ Error: No workflow step specified.\n"
                "   Please provide a final step with the --step flag, or set a\n"
                "   DEFAULT_WORKFLOW_STEP in your configuration file."
            )
            sys.exit(1)

        profile_name = args.profile if args.profile else ""

        if args.describe:
            description = engine.describe(profile_name=profile_name)
            if args.output:
                with open(args.output, 'w', encoding='utf-8') as f:
                    f.write(description)
                logging.info(f"Workflow description saved to: {args.output}")
            else:
                print(description)
        else:
            engine.execute(
                profile_name=profile_name, final_step_name=final_step_name, logger=logger
                )

    except (FileNotFoundError, ValueError) as e:
        logging.error(f"A configuration error occurred:\n{e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        logging.debug(traceback.format_exc())  # Print traceback only in debug mode
        sys.exit(1)


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


def parse_cli_vars(var_list: Optional[List[str]]) -> Optional[Dict[str, str]]:
    """Parses a list of 'KEY=value' strings into a dictionary."""
    if not var_list:
        return {}
    try:
        return dict(item.split('=', 1) for item in var_list)
    except ValueError:
        logging.error("Invalid format for --vars. Use 'KEY=value' separated by spaces.")
        return None


if __name__ == "__main__":
    main()
