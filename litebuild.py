#!/usr/bin/env python
# litebuild.py

# todo - DISPLAY PROCESS OUTPUT

import argparse
import logging
import sys
from typing import Dict, List, Optional

from build_engine import BuildEngine


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


def main():
    """The main entry point for the LiteBuild CLI."""
    parser = argparse.ArgumentParser(
        description="LiteBuild: A lightweight, dependency-aware build system for shell commands."
    )

    parser.add_argument("config_file", help="Path to the config.yml file (must start with 'LB_').")
    parser.add_argument("--target", help="The build target to execute.")
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

    # NEW: Add a verbose flag for debug logging
    parser.add_argument(
        "--verbose", "-v", action='store_true', help="Enable detailed debug logging."
        )

    args = parser.parse_args()

    # Configure logging based on the quiet and verbose flags
    setup_logging(args.quiet, args.verbose)

    if not args.target and not args.vars:
        parser.error("A --target or --vars must be provided to run a build.")
        sys.exit(1)

    cli_vars = parse_cli_vars(args.vars)
    if cli_vars is None:
        sys.exit(1)

    try:
        engine = BuildEngine.from_file(args.config_file, cli_vars=cli_vars)
        target_name = args.target if args.target else ""

        if args.describe:
            description = engine.describe(target_name=target_name)
            if args.output:
                with open(args.output, 'w', encoding='utf-8') as f:
                    f.write(description)
                logging.info(f"Workflow description saved to: {args.output}")
            else:
                print(description)
        else:
            engine.execute(target_name=target_name, final_step_name=args.step)

    except (FileNotFoundError, ValueError) as e:
        logging.error(f"A configuration error occurred:\n{e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        logging.debug(traceback.format_exc())  # Print traceback only in debug mode
        sys.exit(1)


if __name__ == "__main__":
    main()
