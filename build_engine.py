# build_engine.py
from concurrent.futures import ProcessPoolExecutor
from enum import IntEnum
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import traceback
from typing import List, Dict, Tuple, NamedTuple, Optional, Callable

import networkx as nx
# --- MODIFIED: Use the new generic ConfigLoader ---
# Assuming the generic loader is available from this path in your environment.
from YMLEditor.yaml_reader import ConfigLoader

from command_generator import CommandGenerator
from dependency_graph import DependencyGraph
# Import LiteBuild's specific schema and custom validator
from schema import BUILD_SCHEMA, LiteBuildValidator

# Use the standard Python logging library
log = logging.getLogger(__name__)


class UpdateCode(IntEnum):
    """Enumeration for why a build step is considered outdated."""
    UP_TO_DATE = 0
    MISSING_OUTPUT = 1
    NOT_TRACKED = 2
    COMMAND_CHANGED = 3
    INPUTS_CHANGED = 4
    PARAMS_CHANGED = 5
    NEWER_INPUT = 6
    MISSING_INPUT = 7
    DEPENDENCY_CHANGED = 8


class BuildStep(NamedTuple):
    """Represents a single step in the build plan."""
    node_name: str
    command: Dict
    update_code: UpdateCode
    context: str


class BuildPlan(NamedTuple):
    """Contains the full plan for an incremental build."""
    steps_to_run: List[BuildStep]
    steps_to_skip: List[BuildStep]
    command_map: Dict
    execution_graph: nx.DiGraph


class BuildEngine:
    """High-level facade for orchestrating the entire build system."""

    def __init__(
            self, config_data: dict, cli_vars: Optional[Dict] = None,
            state_file: str = ".build_state.json"
    ):
        """Initializes the BuildEngine, merging command-line variables into the config."""
        if cli_vars:
            if "GENERAL" not in config_data:
                config_data["GENERAL"] = {}
            config_data["GENERAL"].update(cli_vars)

        self.config = config_data
        self.state_file = state_file

    @classmethod
    def from_file(
            cls, config_filepath: str, cli_vars: Optional[Dict] = None,
            state_file: str = ".build_state.json"
    ):
        """Creates a BuildEngine instance from a configuration file using the generic loader."""
        try:
            # 1. Instantiate the loader with LiteBuild's specific components.
            loader = ConfigLoader(BUILD_SCHEMA, validator_class=LiteBuildValidator)

            # 2. Read the file, requesting normalization to get default values.
            config_data = loader.read(
                config_file=Path(config_filepath), normalize=True
            )
            return cls(config_data, cli_vars=cli_vars, state_file=state_file)
        except (FileNotFoundError, ValueError) as e:
            # Re-raise the well-formatted exceptions from the generic loader.
            log.error(e)
            raise e

    def execute(
            self,
            target_name: str,
            final_step_name: str = None,
            callbacks: Optional[Dict[str, Callable]] = None,
    ):
        """Plans and executes the build, reporting progress via callbacks."""
        callbacks = callbacks or {}
        on_info = callbacks.get("on_info", lambda msg: None)

        on_info(f"🔵 Executing build for target: '{target_name}'")
        if final_step_name:
            on_info(f"--- Building only up to step: '{final_step_name}' ---")

        state_manager = BuildStateManager(self.state_file)
        planner = BuildPlanner(self.config, state_manager.load_state())
        plan = planner.plan_build(target_name, final_step_name)

        executor = BuildExecutor(state_manager, self.config)
        success = executor.execute_plan(plan, callbacks)

        if success:
            on_info(f"\n✅ Build finished successfully for target: '{target_name}'.")
        else:
            on_info(f"\n❌ Build failed for target: '{target_name}'.")

    def describe(self, target_name: str) -> str:
        """Generates a Markdown description of the workflow for a given target."""
        reporter = BuildReporter(self.config)
        return reporter.describe_workflow(target_name)


class BuildPlanner:
    """
    Analyzes the workflow and build state to create an incremental build plan.
    """

    def __init__(self, config: Dict, build_state: Dict):
        self.config = config
        self.build_state = build_state

    def _is_step_outdated(self, command: Dict) -> Tuple[UpdateCode, str]:
        """Checks a single step to see if it needs to be rebuilt, with detailed debug logging."""
        output_path = command['output']

        if not os.path.exists(output_path):
            log.debug(f"-> REBUILD: Output does not yet exist.")
            return UpdateCode.MISSING_OUTPUT, os.path.basename(output_path)

        stored_state = self.build_state.get(output_path)
        if not stored_state:
            log.debug(f"-> REBUILD: Output path '{output_path}' is not tracked in the build state file.")
            return UpdateCode.NOT_TRACKED, os.path.basename(output_path)

        # --- Hash comparisons ---
        stored_hashes = stored_state.get("hashes", {})
        current_hashes = command["hashes"]

        if stored_hashes.get("command") != current_hashes.get("command"):
            log.debug("-> REBUILD: Command has changed.")
            log.debug(f"  Stored: {stored_hashes.get('command')}")
            log.debug(f"  Actual: {current_hashes.get('command')}")
            return UpdateCode.COMMAND_CHANGED, ""

        if stored_hashes.get("inputs") != current_hashes.get("inputs"):
            log.debug("-> REBUILD: Command Inputs changed.")
            log.debug(f"  Stored: {stored_hashes.get('inputs')}")
            log.debug(f"  Actual: {current_hashes.get('inputs')}")
            return UpdateCode.INPUTS_CHANGED, ""

        if stored_hashes.get("params") != current_hashes.get("params"):
            log.debug("-> REBUILD: Command parameters have changed.")
            log.debug(f"  Stored: {stored_hashes.get('params')}")
            log.debug(f"  Actual: {current_hashes.get('params')}")
            return UpdateCode.PARAMS_CHANGED, ""

        # --- Mtime comparison ---
        try:
            last_build_mtime = stored_state.get('mtime', 0)
            log.debug(f"  Last build time recorded: {last_build_mtime}")
            for input_file in command['input_files']:
                if not os.path.exists(input_file):
                    log.debug(f"-> REBUILD: Required input file '{input_file}' is missing.")
                    raise FileNotFoundError(input_file)

                input_mtime = os.path.getmtime(input_file)
                log.debug(
                    f"  Checking input '{os.path.basename(input_file)}' (mtime: {input_mtime})"
                    )
                if input_mtime > last_build_mtime:
                    log.debug(
                        f"-> REBUILD: Input file '{os.path.basename(input_file)}' is newer than "
                        f"the last build."
                        )
                    return UpdateCode.NEWER_INPUT, os.path.basename(input_file)
        except FileNotFoundError as e:
            return UpdateCode.MISSING_INPUT, os.path.basename(str(e))

        log.debug("-> UP-TO-DATE.")
        return UpdateCode.UP_TO_DATE, ""

    def plan_build(self, target_name: str, final_step_name: str = None) -> BuildPlan:
        """Creates the full build plan, including steps to run and skip."""
        command_map, execution_graph = self._generate_command_map_and_graph(
            target_name, final_step_name
        )

        initially_outdated = {}
        build_order = list(nx.topological_sort(execution_graph))
        for node_name in build_order:
            command = command_map[node_name]
            update_code, context = self._is_step_outdated(command)
            if update_code != UpdateCode.UP_TO_DATE:
                initially_outdated[node_name] = (update_code, context)

        all_nodes_to_run = set(initially_outdated.keys())
        for node_name in initially_outdated:
            all_nodes_to_run.update(nx.descendants(execution_graph, node_name))

        steps_to_run, steps_to_skip = [], []
        for node_name in build_order:
            step_command = command_map[node_name]
            if node_name in all_nodes_to_run:
                update_code, context = initially_outdated.get(
                    node_name, (UpdateCode.DEPENDENCY_CHANGED, "")
                )
                steps_to_run.append(BuildStep(node_name, step_command, update_code, context))
            else:
                steps_to_skip.append(BuildStep(node_name, step_command, UpdateCode.UP_TO_DATE, ""))
        return BuildPlan(steps_to_run, steps_to_skip, command_map, execution_graph)

    def _generate_command_map_and_graph(self, target_name: str, final_step_name: str = None) -> \
            Tuple[Dict, nx.DiGraph]:
        """Generates all commands and the dependency graph for a given target."""
        all_targets = self.config.get("TARGETS", {})
        target_config = {}
        if target_name:
            if target_name in all_targets:
                target_config = all_targets[target_name]
            else:
                available = "\n - ".join(all_targets.keys())
                raise ValueError(
                    f"Target '{target_name}' not found. Available targets are:\n - {available}"
                )
        else:
            log.info("Parameterized build.")

        graph_manager = DependencyGraph(self.config.get("WORKFLOW", {}))
        execution_graph = graph_manager.get_execution_subgraph(final_step_name)
        general_config = self.config.get("GENERAL", {})
        command_gen = CommandGenerator(general_config, target_config)
        context = {"target_name": target_name, **general_config, **target_config}

        command_map, resolved_outputs = {}, {}
        for node_name in nx.topological_sort(execution_graph):
            node_data = execution_graph.nodes[node_name]
            command_map[node_name] = command_gen.generate_for_node(
                node_name, node_data, context, resolved_outputs
            )
        return command_map, execution_graph


class BuildExecutor:
    """Executes a build plan, running commands in parallel where possible."""

    def __init__(self, state_manager, config): # Pass in the whole config
        self.state_manager = state_manager
        self.build_state = state_manager.load_state()
        self.config = config # Store config to get GENERAL params
        self.update_codes = {
            UpdateCode.UP_TO_DATE: "Up-to-date.",
            UpdateCode.MISSING_OUTPUT: "Output file '{context}' does not yet exist.",
            UpdateCode.NOT_TRACKED: "File '{context}' (first build).",
            UpdateCode.COMMAND_CHANGED: "Command changed.",
            UpdateCode.INPUTS_CHANGED: "Input file list  changed.",
            UpdateCode.PARAMS_CHANGED: "Parameters changed.",
            UpdateCode.NEWER_INPUT: "Input file '{context}' is newer.",
            UpdateCode.MISSING_INPUT: "Input file '{context}' missing.",
            UpdateCode.DEPENDENCY_CHANGED: "A dependency has changed."
        }

    def execute_plan(self, plan: BuildPlan, callbacks: Dict[str, Callable]) -> bool:
        """Executes the build plan, managing parallel execution and state."""
        on_info = callbacks.get("on_info", lambda msg: None)
        on_step_output = callbacks.get("on_step_output", lambda step, msg: None)

        total_to_run = len(plan.steps_to_run)
        finished_count = 0

        for step in plan.steps_to_skip:
            on_info(f"Skipping '{step.node_name}' (up-to-date)")

        if not plan.steps_to_run:
            return True

        tasks_to_run_map = {s.node_name: s for s in plan.steps_to_run}
        for generation in nx.topological_generations(plan.execution_graph):
            tasks_this_generation = []
            for node_name in generation:
                if node_name in tasks_to_run_map:
                    step = tasks_to_run_map[node_name]
                    update_text = self.update_codes.get(step.update_code).format(
                        context=step.context
                    )
                    # Pass the reason to the worker
                    tasks_this_generation.append((step.node_name, step.command, update_text))

            if not tasks_this_generation:
                continue

            max_workers = self.config.get("GENERAL", {}).get("MAX_WORKERS") # Get from config
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                results = list(executor.map(self._run_single_command, tasks_this_generation))

            halt_build = False
            for status, output_lines, result_data in results:
                step_name = result_data.get('step_name', 'N/A')
                # Use the on_step_output callback for all command output ---
                for line in output_lines:
                    on_step_output(step_name, line)

                if status == 'EXECUTED':
                    finished_count += 1
                    on_info(f"✅ Finished step '{step_name}' [{finished_count}/{total_to_run}]")
                    self.build_state[result_data['output_path']] = {
                        "hashes": result_data['hashes'], "mtime": result_data['mtime']
                    }
                elif status == 'FAILED':
                    halt_build = True
                    on_info(f"❌    Build failed on step '{step_name}'")
            if halt_build:
                self.state_manager.save_state(self.build_state)
                return False
        self.state_manager.save_state(self.build_state)
        return True

    @staticmethod
    def _run_single_command(task: Tuple[str, Dict, str]) -> Tuple[str, List[str], Dict]:
        """
        Runs a command and captures all output.
        Returns: (status, list_of_output_lines, result_data)
        """
        step_name, command, update_text = task
        output_path = command['output']

        # --- MODIFIED: Capture all output to a list ---
        output_lines = []
        output_lines.append(f"\n▶️  Running step '{step_name}': {update_text}")
        output_lines.append(f"  [{step_name}]       {command['cmd_string']}")

        try:
            process = subprocess.Popen(
                command['cmd_string'], shell=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace'
            )
            for line in process.stdout:
                output_lines.append(f"  [{step_name}]       {line.strip()}")
            return_code = process.wait()
            if return_code != 0:
                raise subprocess.CalledProcessError(return_code, command['cmd_string'])

            new_mtime = os.path.getmtime(output_path)
            result_data = {
                'step_name': step_name, 'output_path': output_path,
                'hashes': command['hashes'], 'mtime': new_mtime
            }
            return 'EXECUTED', output_lines, result_data
        except Exception as e:
            #tb = traceback.format_exc()
            output_lines.append(f"\n❌       Step '{step_name}' failed with an exception.")
            #output_lines.append(tb)
            result_data = {'step_name': step_name}
            return 'FAILED', output_lines, result_data


class BuildReporter:
    """Generates human-readable descriptions and diagrams of the workflow."""

    def __init__(self, config: Dict):
        self.config = config

    def generate_mermaid_diagram(self) -> str:
        """Creates a Mermaid graph syntax string for the workflow."""
        graph_manager = DependencyGraph(self.config.get("WORKFLOW", {}))
        graph = graph_manager.get_execution_subgraph()
        if not graph:
            return "graph TD;\n    Empty_Workflow[Workflow is empty];"
        lines = ["graph TD;"]
        for u, v in graph.edges():
            lines.append(f"    {u} --> {v};")
        sources = [n for n, d in graph.in_degree() if d == 0]
        sinks = [n for n, d in graph.out_degree() if d == 0]
        for node in sources:
            lines.append(f"    style {node} fill:#d4edda,stroke:#155724")
        for node in sinks:
            lines.append(f"    style {node} fill:#f8d7da,stroke:#721c24")
        return "\n".join(lines)

    def describe_workflow(self, target_name: str) -> str:
        """Generates a full Markdown report for the workflow."""
        lines = [f"# Workflow Description for Target: {target_name}\n"]
        lines.append(f"```mermaid\n{self.generate_mermaid_diagram()}\n```\n")
        planner = BuildPlanner(self.config, {})
        plan = planner.plan_build(target_name)
        lines.append("## Workflow Steps\n")
        if not plan.command_map:
            lines.append("No steps defined.")
            return "\n".join(lines)
        build_order = list(nx.topological_sort(plan.execution_graph))
        for node_name in build_order:
            command = plan.command_map[node_name]
            node_data = plan.execution_graph.nodes[node_name]
            lines.append(f"### {node_name}\n*   **Output:** `{command['output']}`")
            if command['input_files']:
                lines.append("*   **Inputs:**")
                lines.extend([f"    *   `{f}`" for f in command['input_files']])
            if node_data.get("REQUIRES"):
                lines.append("*   **Requires:**")
                lines.extend([f"    *   {dep}" for dep in node_data["REQUIRES"]])
            lines.append(f"```sh\n{command['cmd_string']}\n```\n")
        lines.append("## Full Command List\n")
        lines.append("A complete list of commands to be run for a full, clean build, in order.\n")
        lines.append("```sh")
        for i, node_name in enumerate(build_order):
            command_details = plan.command_map[node_name]
            lines.append(f"#  {node_name}")
            lines.append(command_details['cmd_string'])
            lines.append("")
        lines.append("```\n")
        return "\n".join(lines)


class BuildStateManager:
    """Manages loading and saving the .build_state.json file."""

    def __init__(self, state_file_path: str):
        self.state_file_path = state_file_path

    def load_state(self) -> Dict:
        """Loads the build state from the JSON file."""
        if not os.path.exists(self.state_file_path):
            return {}
        try:
            with open(self.state_file_path, 'r') as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError):
            log.warning("Could not read or parse state file. Forcing a full rebuild.")
            return {}

    def save_state(self, state: Dict):
        """Saves the build state to the JSON file."""
        try:
            with open(self.state_file_path, 'w') as f:
                json.dump(state, f, indent=2)
        except IOError as e:
            log.error(f"Could not write to state file '{self.state_file_path}': {e}")
