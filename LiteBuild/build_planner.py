import difflib
import os
import re
import time
from pathlib import Path
from typing import Dict, Tuple

import networkx as nx

from LiteBuild.build_logger import LogLevel, get_logger
from LiteBuild.command_generator import CommandGenerator
from LiteBuild.common import BuildPlan, BuildStep, ReasonCode
from LiteBuild.dependency_graph import DependencyGraph
from LiteBuild.provenance import ProvenanceAnalysis, analyze_provenance
from LiteBuild.schema import YAMLSection


class BuildPlanner:
    """Analyze per-context workflow state and produce an incremental build plan."""

    def __init__(self, config: Dict, build_state: Dict):
        self.config = config
        self.build_state = build_state
        self.logger = get_logger()
        self._special_input_resolution: dict[str, dict[str, str]] = {}

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Return a stable absolute path for DAG input/output comparison."""
        return os.path.normcase(
            os.path.abspath(
                os.path.normpath(
                    os.fspath(path)
                )
            )
        )


    @staticmethod
    def _validate_profile_name(profile_name: str) -> None:
        """Validate the user profile namespace used by LiteBuild state files."""
        if not profile_name:
            return

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", profile_name):
            raise ValueError(
                f"Invalid profile name '{profile_name}'. Profile names must begin "
                "with a letter or digit and may contain only letters, digits, "
                "'.', '-', and '_'. Names beginning with '_' are reserved by LiteBuild."
            )

    def _check_inputs_against_mtime(
        self,
        command: Dict,
        produced_outputs: set[str],
        reference_mtime: float,
    ) -> Tuple[ReasonCode, str]:
        """Compare step inputs with a reference output/build timestamp."""
        for input_file in command["input_files"]:
            self.logger.debug(f"    input: {input_file}")

            if not os.path.exists(input_file):
                normalized_input = self._normalize_path(input_file)

                if normalized_input in produced_outputs:
                    self.logger.debug(
                        "      exists: no; produced by current DAG"
                    )
                    continue

                self.logger.debug(
                    "      exists: no; not produced by current DAG"
                )
                self._debug_decision(
                    ReasonCode.MISSING_INPUT,
                    input_file,
                )
                return ReasonCode.MISSING_INPUT, input_file

            input_mtime = os.path.getmtime(input_file)
            self.logger.debug(
                "      mtime: "
                f"{input_mtime:.6f} ({time.ctime(input_mtime)})"
            )

            if input_mtime > reference_mtime:
                context = os.path.basename(input_file)
                self.logger.debug("      newer than reference: yes")
                self._debug_decision(ReasonCode.NEWER_INPUT, context)
                return ReasonCode.NEWER_INPUT, context

            self.logger.debug("      newer than reference: no")

        return ReasonCode.UP_TO_DATE, ""

    @staticmethod
    def _first_missing_path_component(path: str) -> str | None:
        """Return the first cumulative path component that does not exist."""
        candidate = Path(path)

        if candidate.is_absolute():
            current = Path(candidate.anchor)
            parts = candidate.parts[1:]
        else:
            current = Path()
            parts = candidate.parts

        for part in parts:
            current = current / part
            if not current.exists():
                return os.fspath(current)

        return None

    @staticmethod
    def _case_insensitive_match(path: str) -> str | None:
        """Return a sibling path that differs only by case, if one exists."""
        candidate = Path(path)
        parent = candidate.parent if os.fspath(candidate.parent) else Path(".")

        if not parent.is_dir():
            return None

        target_name = candidate.name.casefold()
        for child in parent.iterdir():
            if child.name.casefold() == target_name and child.name != candidate.name:
                return os.fspath(child)

        return None

    @staticmethod
    def _nearby_name_suggestion(path: str) -> str | None:
        """Suggest a close sibling for the first missing path component."""
        missing_component = BuildPlanner._first_missing_path_component(path)
        if missing_component is None:
            return None

        candidate = Path(missing_component)
        parent = candidate.parent if os.fspath(candidate.parent) else Path(".")

        if not parent.is_dir():
            return None

        matches = difflib.get_close_matches(
            candidate.name,
            [child.name for child in parent.iterdir()],
            n=1,
            cutoff=0.72,
        )
        if not matches:
            return None

        return os.fspath(parent / matches[0])

    @staticmethod
    def _duplicate_input_directory_hint(
        input_directory: str,
        original_input: str,
    ) -> str | None:
        """Return a likely corrected INPUT_FILES value for a duplicate prefix."""
        directory_name = Path(os.path.normpath(input_directory)).name
        original_parts = Path(original_input).parts

        if not directory_name or not original_parts:
            return None

        if os.path.normcase(original_parts[0]) != os.path.normcase(directory_name):
            return None

        remaining = original_parts[1:]
        return os.path.join(*remaining) if remaining else ""

    def _format_missing_input_error(self, node_name: str, input_file: str) -> str:
        """Return a focused diagnostic for a missing source input."""
        absolute_path = os.path.abspath(input_file)
        lines = [
            f"Cannot build step '{node_name}': required input was not found.",
            "",
            "Resolved path:",
            f"  {input_file}",
        ]

        if os.path.normpath(absolute_path) != os.path.normpath(input_file):
            lines.extend([
                "",
                "Resolved from current working directory as:",
                f"  {absolute_path}",
            ])

        special = self._special_input_resolution.get(
            self._normalize_path(input_file)
        )
        if special is not None:
            input_directory = special["input_directory"]
            original_input = special["original_input"]

            lines.extend([
                "",
                "This input came from INPUT_FILES. LiteBuild automatically prepends",
                "INPUT_DIRECTORY to INPUT_FILES entries.",
                "",
                f"  INPUT_DIRECTORY: {input_directory}",
                f"  INPUT_FILES:     {original_input}",
            ])

            corrected = self._duplicate_input_directory_hint(
                input_directory,
                original_input,
            )
            if corrected is not None:
                directory_name = Path(os.path.normpath(input_directory)).name
                lines.extend([
                    "",
                    "Possible duplicate input directory:",
                    f"  INPUT_DIRECTORY already ends in '{directory_name}',",
                    f"  and INPUT_FILES also begins with '{directory_name}'.",
                    "",
                    "Did you mean:",
                    f"  {corrected or '.'}",
                ])

        missing_component = self._first_missing_path_component(input_file)
        if missing_component is not None:
            lines.extend([
                "",
                "First missing path component:",
                f"  {missing_component}",
            ])

            suggestion = self._nearby_name_suggestion(input_file)
            if suggestion is not None:
                lines.extend([
                    "",
                    "Did you mean:",
                    f"  {suggestion}",
                ])
        else:
            case_match = self._case_insensitive_match(input_file)
            if case_match is not None:
                lines.extend([
                    "",
                    "A path differing only by letter case exists:",
                    f"  {case_match}",
                ])

        return "\n".join(lines)

    def _is_step_outdated(
        self,
        command: Dict,
        produced_outputs: set[str],
        force_rebuild: bool = False,
    ) -> Tuple[ReasonCode, str]:
        """Return LiteBuild's freshness decision for one workflow step.

        INFO-level logging reports the final plan elsewhere. This method writes
        detailed freshness diagnostics only at DEBUG level.
        """
        output_path = command["output"]
        node_name = command["node_name"]

        self.logger.blank(level=LogLevel.DEBUG)
        self.logger.debug(f"PLAN  {node_name}")
        self.logger.debug(f"    output: {output_path}")

        if force_rebuild:
            self._debug_decision(ReasonCode.FORCED)
            return ReasonCode.FORCED, ""

        if not os.path.exists(output_path):
            self.logger.debug("    output exists: no")
            self._debug_decision(ReasonCode.MISSING_OUTPUT)
            return ReasonCode.MISSING_OUTPUT, os.path.basename(output_path)

        self.logger.debug("    output exists: yes")

        stored_state = self.build_state.get(node_name)
        if not stored_state:
            self.logger.debug("    tracked: no")

            output_mtime = os.path.getmtime(output_path)
            self.logger.debug(
                "    bootstrap output mtime: "
                f"{output_mtime:.6f} ({time.ctime(output_mtime)})"
            )

            reason_code, context = self._check_inputs_against_mtime(
                command,
                produced_outputs,
                output_mtime,
            )

            if reason_code != ReasonCode.UP_TO_DATE:
                return reason_code, context

            # The existing output is current by normal timestamp rules. Do not
            # rebuild it merely because this state namespace has no prior record.
            # BuildExecutor will persist state for this skipped step.
            command["adopt_state"] = True
            self.logger.debug("    state: adopt existing output")
            self._debug_decision(ReasonCode.UP_TO_DATE)
            return ReasonCode.UP_TO_DATE, ""

        self.logger.debug("    tracked: yes")

        stored_output = stored_state.get("output")
        if stored_output != output_path:
            self.logger.debug(
                "    output path: changed "
                f"({stored_output!r} -> {output_path!r})"
            )
            self._debug_decision(ReasonCode.OUTPUT_CHANGED)
            return ReasonCode.OUTPUT_CHANGED, ""

        self.logger.debug("    output path: match")

        stored_hashes = stored_state.get("hashes", {})
        current_hashes = command["hashes"]

        if stored_hashes.get("command") != current_hashes.get("command"):
            self.logger.debug("    command hash: changed")
            self._debug_decision(ReasonCode.COMMAND_CHANGED)
            return ReasonCode.COMMAND_CHANGED, ""

        self.logger.debug("    command hash: match")

        if stored_hashes.get("inputs") != current_hashes.get("inputs"):
            self.logger.debug("    input-list hash: changed")
            self._debug_decision(ReasonCode.INPUTS_CHANGED)
            return ReasonCode.INPUTS_CHANGED, ""

        self.logger.debug("    input-list hash: match")

        if stored_hashes.get("params") != current_hashes.get("params"):
            self.logger.debug("    parameter hash: changed")
            self._debug_decision(ReasonCode.PARAMS_CHANGED)
            return ReasonCode.PARAMS_CHANGED, ""

        self.logger.debug("    parameter hash: match")

        # Preserve the existing small pause before filesystem timestamp checks.
        time.sleep(0.1)

        last_build_mtime = stored_state["mtime"]
        self.logger.debug(
            "    output mtime: "
            f"{last_build_mtime:.6f} ({time.ctime(last_build_mtime)})"
        )

        reason_code, context = self._check_inputs_against_mtime(
            command,
            produced_outputs,
            last_build_mtime,
        )

        if reason_code != ReasonCode.UP_TO_DATE:
            return reason_code, context

        self._debug_decision(ReasonCode.UP_TO_DATE)
        return ReasonCode.UP_TO_DATE, ""

    def _debug_decision(
        self,
        reason_code: ReasonCode,
        context: str = "",
    ) -> None:
        """Write one canonical DEBUG freshness decision."""
        decision = (
            "up to date"
            if reason_code == ReasonCode.UP_TO_DATE
            else "rebuild"
        )
        self.logger.debug(f"    decision: {decision}")
        self.logger.debug(
            f"    reason: {reason_code.status_text(context)}"
        )

    def _check_provenance(
        self,
        command_map: Dict,
        context: Dict,
    ) -> ProvenanceAnalysis:
        """Analyze provenance and enforce the PROJECT provenance policy.

        Provenance ambiguity is always invalid because it represents an
        ambiguous configuration. Missing provenance is controlled by
        PROJECT/PROVENANCE_CHECK: off, warn, or fail.
        """
        project_config = self.config.get(YAMLSection.PROJECT, {})
        mode = project_config.get("PROVENANCE_CHECK", "off")

        if isinstance(mode, bool):
            raise ValueError(
                "PROJECT/PROVENANCE_CHECK must be one of: off, warn, fail."
            )

        mode = str(mode).strip().lower()
        if mode not in {"off", "warn", "fail"}:
            raise ValueError(
                "PROJECT/PROVENANCE_CHECK must be one of: off, warn, fail."
            )

        provenance_config = self.config.get("PROVENANCE", {})
        analysis = analyze_provenance(
            command_map,
            provenance_config,
            context,
        )

        if analysis.ambiguous:
            details = []
            for ambiguity in analysis.ambiguous:
                names = ", ".join(
                    entry.name for entry in ambiguity.matches
                )
                details.append(
                    f"  {ambiguity.path}: {names}"
                )

            raise ValueError(
                "Ambiguous provenance declarations:\n"
                + "\n".join(details)
            )

        if analysis.missing and mode == "warn":
            self.logger.warning("Missing provenance:")
            for source_file in analysis.missing:
                self.logger.warning(
                    f"    {source_file}",
                    show_level=False,
                )

        if analysis.missing and mode == "fail":
            details = "\n".join(
                f"  {source_file}"
                for source_file in analysis.missing
            )
            raise ValueError(
                "Missing provenance for source files:\n"
                + details
            )

        return analysis

    def plan_build(
        self,
        profile_name: str,
        final_step_name: str | None = None,
        force_rebuild: bool = False,
    ) -> BuildPlan:
        """Create the incremental build plan for one profile."""
        command_map, execution_graph, context = (
            self._generate_command_map_graph_and_context(
                profile_name,
                final_step_name,
            )
        )

        self._check_provenance(command_map, context)

        for node_name, command in command_map.items():
            command["node_name"] = node_name

        output_owners: dict[str, str] = {}
        for node_name, command in command_map.items():
            normalized_output = self._normalize_path(command["output"])
            existing_owner = output_owners.get(normalized_output)

            if existing_owner is not None:
                raise ValueError(
                    f"Workflow steps '{existing_owner}' and '{node_name}' "
                    f"produce the same output: {command['output']}"
                )

            output_owners[normalized_output] = node_name

        produced_outputs = set(output_owners)

        initially_outdated: dict[str, tuple[ReasonCode, str]] = {}
        build_order = list(nx.topological_sort(execution_graph))

        for node_name in build_order:
            command = command_map[node_name]
            reason_code, context = self._is_step_outdated(
                command,
                produced_outputs,
                force_rebuild=force_rebuild,
            )

            if reason_code == ReasonCode.MISSING_INPUT:
                raise FileNotFoundError(
                    self._format_missing_input_error(node_name, context)
                )

            if reason_code != ReasonCode.UP_TO_DATE:
                initially_outdated[node_name] = (reason_code, context)

        all_nodes_to_run = set(initially_outdated)
        for node_name in initially_outdated:
            all_nodes_to_run.update(
                nx.descendants(execution_graph, node_name)
            )

        steps_to_run: list[BuildStep] = []
        steps_to_skip: list[BuildStep] = []

        for node_name in build_order:
            step_command = command_map[node_name]
            description = execution_graph.nodes[node_name].get("DESCRIPTION", "")

            if node_name in all_nodes_to_run:
                reason_code, context = initially_outdated.get(
                    node_name,
                    (ReasonCode.STALE_TARGET, ""),
                )
                steps_to_run.append(
                    BuildStep(
                        node_name=node_name,
                        description=description,
                        command=step_command,
                        reason_code=reason_code,
                        context=context,
                    )
                )
            else:
                steps_to_skip.append(
                    BuildStep(
                        node_name=node_name,
                        description=description,
                        command=step_command,
                        reason_code=ReasonCode.UP_TO_DATE,
                        context="",
                    )
                )

        self._log_plan_summary(
            profile_name=profile_name,
            final_step_name=final_step_name,
            steps_to_run=steps_to_run,
            steps_to_skip=steps_to_skip,
        )

        return BuildPlan(
            steps_to_run,
            steps_to_skip,
            command_map,
            execution_graph,
        )

    def _log_plan_summary(
        self,
        *,
        profile_name: str,
        final_step_name: str | None,
        steps_to_run: list[BuildStep],
        steps_to_skip: list[BuildStep],
    ) -> None:
        """Write a concise INFO summary of the completed build plan."""
        profile_text = profile_name or "<no profile>"
        target_text = final_step_name or "<default target>"

        self.logger.info("🔵 STEPS")
        self.logger.info(f"    Target:  {target_text}")
        self.logger.info(f"    Profile: {profile_text}")

        self.logger.info(
            f"    Steps:   {len(steps_to_run)} to run, "
            f"{len(steps_to_skip)} up to date"
        )
        self.logger.blank()

    @staticmethod
    def get_suggestion(
        invalid_key: str,
        valid_options: list[str],
    ) -> str:
        """Return a 'Did you mean ...?' hint for a close match."""
        matches = difflib.get_close_matches(
            invalid_key,
            valid_options,
            n=1,
            cutoff=0.6,
        )
        if matches:
            return f"\n  Did you mean '{matches[0]}'?"
        return ""

    def _generate_command_map_and_graph(
        self,
        profile_name: str,
        final_step_name: str | None = None,
    ) -> Tuple[Dict, nx.DiGraph]:
        """Generate commands and the selected execution DAG."""
        command_map, execution_graph, _ = (
            self._generate_command_map_graph_and_context(
                profile_name,
                final_step_name,
            )
        )
        return command_map, execution_graph

    def _generate_command_map_graph_and_context(
        self,
        profile_name: str,
        final_step_name: str | None = None,
    ) -> tuple[Dict, nx.DiGraph, Dict]:
        """Generate commands, the selected execution DAG, and resolved context."""
        self._validate_profile_name(profile_name)

        all_profiles = self.config.get(YAMLSection.PROFILES, {})
        profile_config = {}

        if profile_name:
            if profile_name in all_profiles:
                profile_config = all_profiles[profile_name]
            else:
                available = list(all_profiles.keys())
                hint = self.get_suggestion(profile_name, available)

                if available:
                    available_text = "\n  - ".join(available)
                    available_message = (
                        f"\nAvailable profiles:\n  - {available_text}"
                    )
                else:
                    available_message = "\nNo profiles are configured."

                raise ValueError(
                    f"Profile '{profile_name}' not found."
                    f"{hint}{available_message}"
                )

        workflow_config = self.config[YAMLSection.WORKFLOW]
        graph_manager = DependencyGraph(workflow_config)
        execution_graph = graph_manager.get_execution_subgraph(
            final_step_name
        )

        general_config = self.config[YAMLSection.GENERAL]
        parameters_config = self.config[YAMLSection.PARAMETERS]
        command_gen = CommandGenerator(
            parameters_config,
            profile_config,
        )

        context = {
            "profile_name": profile_name,
            **general_config,
            **profile_config,
        }

        input_basenames = context.get("INPUT_FILES")
        input_dir = context.get("INPUT_DIRECTORY")

        self._special_input_resolution = {}

        if input_dir and input_basenames:
            resolved_input_files = []
            for filename in input_basenames:
                resolved_path = os.path.join(input_dir, filename)
                resolved_input_files.append(resolved_path)
                self._special_input_resolution[
                    self._normalize_path(resolved_path)
                ] = {
                    "input_directory": os.fspath(input_dir),
                    "original_input": os.fspath(filename),
                }

            context["INPUT_FILES"] = resolved_input_files

        command_map: dict = {}
        resolved_outputs: dict = {}

        for node_name in nx.topological_sort(execution_graph):
            node_data = execution_graph.nodes[node_name]

            try:
                command_map[node_name] = command_gen.generate_for_node(
                    node_name,
                    node_data,
                    context,
                    resolved_outputs,
                )
            except (FileNotFoundError, ValueError) as exc:
                raise ValueError(
                    f"Error generating command for step '{node_name}': {exc}"
                ) from exc

        return command_map, execution_graph, context
