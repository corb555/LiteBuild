# build_engine.py
from concurrent.futures import ProcessPoolExecutor
from enum import IntEnum
import json
import os
from pathlib import Path
import subprocess
import time
from typing import List, Dict, Tuple, NamedTuple, Optional

import networkx as nx
from YMLEditor.yaml_reader import ConfigLoader

from build_logger import BuildLogger, setup_logger, get_logger
from command_generator import CommandGenerator
from dependency_graph import DependencyGraph
from schema import BUILD_SCHEMA, LiteBuildValidator


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
    STALE_TARGET = 8


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
        """Creates a BuildEngine instance from a configuration file."""
        try:
            loader = ConfigLoader(BUILD_SCHEMA, validator_class=LiteBuildValidator)
            config_data = loader.read(
                config_file=Path(config_filepath), normalize=True
            )
            return cls(config_data, cli_vars=cli_vars, state_file=state_file)
        except (FileNotFoundError, ValueError) as e:
            # Re-raise to be handled by the calling script (CLI or GUI)
            raise e

    def execute(self, final_step_name: str, profile_name: str = "", logger: BuildLogger = None):
        """
        Plans and executes the build for a specific workflow entry step.

        Args:
            final_step_name: The required  step of the workflow DAG to execute.
            profile_name: An optional parameter context .
            logger: The logger instance to use for output.
        """
        if logger is None:
            logger = get_logger()
        setup_logger(logger)

        logger.log(f"🔵 Executing build for step {final_step_name} using: '{profile_name}'")

        try:
            state_manager = BuildStateManager(self.state_file)
            planner = BuildPlanner(self.config, state_manager.load_state())
            plan = planner.plan_build(profile_name, final_step_name)

            executor = BuildExecutor(state_manager, self.config)
            success = executor.execute_plan(plan, logger)
        except Exception as e:
            success = False
            logger.log(f"{e}")

        if success:
            logger.log(f"\n✅ Build finished successfully.")
        else:
            logger.log(f"❌ Build failed for {final_step_name} using: '{profile_name}'")

    def describe(self, profile_name: str) -> str:
        """Generates a Markdown description of the workflow for a given profile."""
        reporter = BuildReporter(self.config)
        return reporter.describe_workflow(profile_name)


class BuildPlanner:
    """
    Analyzes the workflow and build state to create an incremental build plan.
    """

    def __init__(self, config: Dict, build_state: Dict):
        self.config = config
        self.build_state = build_state
        # --- NEW: Get the logger instance ---
        self.logger = get_logger()

    def _is_step_outdated(self, command: Dict) -> Tuple[UpdateCode, str]:
        """Checks a single step to see if it needs to be rebuilt, with detailed debug logging."""
        output_path = command['output']
        node_name = command.get('node_name', 'UnknownStep') # Assuming node_name is passed in command dict

        self.logger.debug(f"\n--- Checking status of step '{node_name}' ---")
        self.logger.debug(f"  - Output file: '{output_path}'")

        # --- MODIFIED: Added detailed logging for each check ---

        # 1. Check for output file existence
        if not os.path.exists(output_path):
            self.logger.debug(f"  - RESULT: File does not exist. (MISSING_OUTPUT)")
            return UpdateCode.MISSING_OUTPUT, os.path.basename(output_path)
        self.logger.debug(f"  - File exists: True")

        # 2. Check if the output is tracked in the build state
        stored_state = self.build_state.get(output_path)
        if not stored_state:
            self.logger.debug(f"  - RESULT: Output file is not tracked in build state. (NOT_TRACKED)")
            return UpdateCode.NOT_TRACKED, os.path.basename(output_path)
        self.logger.debug(f"  - Tracked in build state: True")

        # 3. --- Hash comparisons ---
        stored_hashes = stored_state.get("hashes", {})
        current_hashes = command["hashes"]

        if stored_hashes.get("command") != current_hashes.get("command"):
            self.logger.debug(f"  - RESULT: Command hash has changed. (COMMAND_CHANGED)")
            return UpdateCode.COMMAND_CHANGED, ""
        self.logger.debug(f"  - Command hash: Match")

        if stored_hashes.get("inputs") != current_hashes.get("inputs"):
            self.logger.debug(f"  - RESULT: Input file list hash has changed. (INPUTS_CHANGED)")
            return UpdateCode.INPUTS_CHANGED, ""
        self.logger.debug(f"  - Input list hash: Match")

        if stored_hashes.get("params") != current_hashes.get("params"):
            self.logger.debug(f"  - RESULT: Parameters hash has changed. (PARAMS_CHANGED)")
            return UpdateCode.PARAMS_CHANGED, ""
        self.logger.debug(f"  - Parameters hash: Match")

        # Slight pause to ensure files from previous step are ready
        time.sleep(0.1)

        # 4. --- Mtime (modification time) comparison ---
        try:
            last_build_mtime = stored_state.get('mtime', 0)
            self.logger.debug(f"  - Last build mtime for output: {last_build_mtime} ({time.ctime(last_build_mtime)})")

            for input_file in command['input_files']:
                self.logger.debug(f"    - Checking input: '{input_file}'")
                if not os.path.exists(input_file):
                    self.logger.debug(f"    - RESULT: Input file does not exist. (MISSING_INPUT)")
                    raise FileNotFoundError(input_file)

                input_mtime = os.path.getmtime(input_file)
                self.logger.debug(f"      - Input mtime: {input_mtime} ({time.ctime(input_mtime)})")

                if input_mtime > last_build_mtime:
                    self.logger.debug(f"      - RESULT: Input is newer than last build. (NEWER_INPUT)")
                    return UpdateCode.NEWER_INPUT, os.path.basename(input_file)

            self.logger.debug(f"    - All inputs are older than the last build.")

        except FileNotFoundError as e:
            return UpdateCode.MISSING_INPUT, os.path.basename(str(e))

        # 5. --- Final Decision ---
        self.logger.debug(f"  - RESULT: Step is up-to-date. (UP_TO_DATE)")
        return UpdateCode.UP_TO_DATE, ""

    def plan_build(self, profile_name: str, final_step_name: str = None) -> BuildPlan:
        # ... (This method is unchanged, but we need to pass node_name to the command dict)
        command_map, execution_graph = self._generate_command_map_and_graph(
            profile_name, final_step_name
        )

        # --- MODIFICATION: Add node_name to command for better logging ---
        for node, cmd in command_map.items():
            cmd['node_name'] = node

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
                    node_name, (UpdateCode.STALE_TARGET, "")
                )
                steps_to_run.append(BuildStep(node_name, step_command, update_code, context))
            else:
                steps_to_skip.append(BuildStep(node_name, step_command, UpdateCode.UP_TO_DATE, ""))
        return BuildPlan(steps_to_run, steps_to_skip, command_map, execution_graph)

    # ... (_generate_command_map_and_graph is unchanged) ...
    def _generate_command_map_and_graph(self, profile_name: str, final_step_name: str = None) -> \
            Tuple[Dict, nx.DiGraph]:
        """Generates all commands and the dependency graph for a given profile."""
        all_profiles = self.config.get("PROFILES", {})
        profile_config = {}
        if profile_name:
            if profile_name in all_profiles:
                profile_config = all_profiles[profile_name]
            else:
                available = "\n - ".join(all_profiles.keys())
                raise ValueError(
                    f"profile '{profile_name}' not found. Available profiles are:\n - {available}"
                )

        graph_manager = DependencyGraph(self.config.get("WORKFLOW", {}))
        execution_graph = graph_manager.get_execution_subgraph(final_step_name)
        general_config = self.config.get("GENERAL", {})
        command_gen = CommandGenerator(general_config, profile_config)
        context = {"profile_name": profile_name, **general_config, **profile_config}

        # Pre-process input files to create full paths automatically.
        input_dir = context.get("INPUT_DIRECTORY")
        input_basenames = context.get("INPUT_FILES")

        if input_dir and input_basenames:
            # Create the list of full paths
            full_paths = [os.path.join(input_dir, f) for f in input_basenames]
            # Overwrite the INPUT_FILES in the context with the full paths.
            # This makes the fully resolved list available to all template substitutions.
            context['INPUT_FILES'] = full_paths

        command_map, resolved_outputs = {}, {}
        for node_name in nx.topological_sort(execution_graph):
            node_data = execution_graph.nodes[node_name]
            command_map[node_name] = command_gen.generate_for_node(
                node_name, node_data, context, resolved_outputs
            )
        return command_map, execution_graph
class BuildExecutor:
    """Executes a build plan, running commands in parallel where possible."""

    def __init__(self, state_manager, config: Dict):
        self.state_manager = state_manager
        self.build_state = state_manager.load_state()
        self.config = config
        self.update_codes = {
            UpdateCode.UP_TO_DATE: "(Up-to-date)", UpdateCode.MISSING_OUTPUT: "(Creating Output)",
            UpdateCode.NOT_TRACKED: "(First build)",
            UpdateCode.COMMAND_CHANGED: "(Command has changed)",
            UpdateCode.INPUTS_CHANGED: "(Input file list has changed)",
            UpdateCode.PARAMS_CHANGED: "(Parameters have changed)",
            UpdateCode.NEWER_INPUT: "(Input '{context}' is newer)",
            UpdateCode.MISSING_INPUT: "(Input '{context}' is missing)",
            UpdateCode.STALE_TARGET: "(Target is stale)"
        }

    def execute_plan(self, plan: BuildPlan, logger: BuildLogger) -> bool:
        """Executes the build plan, managing parallel execution and state."""
        total_to_run = len(plan.steps_to_run)
        finished_count = 0

        for step in plan.steps_to_skip:
            logger.log(f"Skipping '{step.node_name}' (up-to-date)")

        if not plan.steps_to_run:
            return True

        # Ask the logger for the information needed to initialize workers.
        # This is polymorphic: a FileLogger provides info, a StreamLogger does not.
        worker_init_info = logger.get_worker_init_info()
        initializer, initargs = (worker_init_info if worker_init_info else (None, ()))

        tasks_to_run_map = {s.node_name: s for s in plan.steps_to_run}
        for generation in nx.topological_generations(plan.execution_graph):
            tasks_this_generation = []
            for node_name in generation:
                if node_name in tasks_to_run_map:
                    step = tasks_to_run_map[node_name]
                    update_text = self.update_codes.get(step.update_code).format(
                        context=step.context
                    )
                    tasks_this_generation.append((step.node_name, step.command, update_text))

            if not tasks_this_generation:
                continue

            max_workers = self.config.get("GENERAL", {}).get("MAX_WORKERS")
            with ProcessPoolExecutor(
                    max_workers=max_workers, initializer=initializer, initargs=initargs
            ) as executor:
                results = list(executor.map(self._run_single_command, tasks_this_generation))

            halt_build = False
            for status, result_data in results:
                step_name = result_data.get('step_name', 'N/A')
                if status == 'EXECUTED':
                    finished_count += 1
                    logger.log(f"✅ Finished step '{step_name}' [{finished_count}/{total_to_run}]")
                    self.build_state[result_data['output_path']] = {
                        "hashes": result_data['hashes'], "mtime": result_data['mtime']
                    }
                elif status == 'FAILED':
                    halt_build = True
                    logger.log(f"❌ Build failed for Step '{step_name}'")
            if halt_build:
                self.state_manager.save_state(self.build_state)
                return False
        self.state_manager.save_state(self.build_state)
        return True

    @staticmethod
    def _run_single_command(task: Tuple[str, Dict, str]) -> Tuple[str, Dict]:
        """Runs a command and streams all output to the configured log ."""
        logger = get_logger()
        step_name, command, update_text = task
        output_path = command['output']

        logger.log(f"\n▶️  Running step '{step_name}': {update_text}")
        logger.log(f"  [{step_name}]       {command['cmd_string']}")

        try:
            process = subprocess.Popen(
                command['cmd_string'], shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace'
            )
            for line in iter(process.stdout.readline, ''):
                logger.log(f"  [{step_name}]       {line.strip()}")
            return_code = process.wait()
            if return_code != 0:
                raise subprocess.CalledProcessError(return_code, "")

            new_mtime = os.path.getmtime(output_path)
            result_data = {
                'step_name': step_name, 'output_path': output_path, 'hashes': command['hashes'],
                'mtime': new_mtime
            }
            return 'EXECUTED', result_data
        except Exception as e:
            logger.log(f"❌ Step '{step_name}' failed: {e}")
            result_data = {'step_name': step_name}
            return 'FAILED', result_data


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

    def describe_workflow(self, final_step_name: str, profile_name: str) -> str:
        """Generates a full Markdown report for the workflow."""
        lines = [f"# Workflow for  {final_step_name}\n"]
        lines.append(f"```mermaid\n{self.generate_mermaid_diagram()}\n```\n")
        planner = BuildPlanner(self.config, {})
        plan = planner.plan_build(profile_name)
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
            return {}

    def save_state(self, state: Dict):
        """Saves the build state to the JSON file."""
        try:
            with open(self.state_file_path, 'w') as f:
                json.dump(state, f, indent=2)
        except IOError as e:
            raise IOError(f"Could not write to state file '{self.state_file_path}': {e}")


# --- Worker initializer  accepts a logger object ---
def setup_worker_logger(logger: BuildLogger):
    """Initializes the logger for a worker process."""
    setup_logger(logger)
