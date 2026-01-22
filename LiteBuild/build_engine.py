from concurrent.futures import ProcessPoolExecutor
import datetime
from enum import IntEnum
import json
import os
from pathlib import Path
import subprocess
import time
import traceback
from typing import List, Dict, Tuple, NamedTuple, Optional

import networkx as nx
from YMLEditor.yaml_reader import ConfigLoader

from LiteBuild.build_logger import BuildLogger, setup_logger, get_logger
from LiteBuild.command_generator import CommandGenerator
from LiteBuild.dependency_graph import DependencyGraph
from LiteBuild.schema import BUILD_SCHEMA, LiteBuildValidator


class UpdateCode(IntEnum):
    """Enumeration for why a build step is  outdated."""
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
        # Validate Input Directory
        input_dir = config_data.get("GENERAL", {}).get("INPUT_DIRECTORY")
        if input_dir:
            path = Path(input_dir)
            if not path.exists():
                raise FileNotFoundError(
                    f"❌ Configuration Error: INPUT_DIRECTORY does not exist.\n"
                    f"   Path: {path.absolute()}"
                )
            if not path.is_dir():
                raise NotADirectoryError(
                    f"❌ Configuration Error: INPUT_DIRECTORY is not a directory.\n"
                    f"   Path: {path.absolute()}"
                )

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

    def execute(self, final_step_name: str, profile_name: str = "", logger: BuildLogger = None, status_callback=None):
        """
        Plans and executes the build for a specific workflow entry step.
        """
        if logger is None:
            logger = get_logger()
        setup_logger(logger)

        # --- Extract Context info ---
        general_cfg = self.config.get("GENERAL", {})
        profile_cfg = self.config.get("PROFILES", {}).get(profile_name, {})

        # Get (optional) segment/category from General  or Profile
        segment = general_cfg.get("SEGMENT") or profile_cfg.get("SEGMENT") or ""
        category = general_cfg.get("CATEGORY") or profile_cfg.get("CATEGORY") or ""

        context_str = f"Segment: {segment}    Category: {category}  "
        if profile_name:
            context_str += f" Profile: {profile_name}"

        # Get the current date and time
        #now = datetime.now()

        # Format the time as a string (HH:MM:SS) using strftime()
        current_time = "" #now.strftime("%H:%M:%S")

        logger.log(f"🔵 Executing build for step {final_step_name} - {current_time}")
        logger.log(f"ℹ️   {context_str}\n")

        try:
            state_manager = BuildStateManager(self.state_file)
            planner = BuildPlanner(self.config, state_manager.load_state())
            plan = planner.plan_build(profile_name, final_step_name)

            executor = BuildExecutor(state_manager, self.config)
            success = executor.execute_plan(plan, logger, status_callback=status_callback)
        except Exception as e:
            success = False
            # ---  ERROR REPORTING ---
            logger.log(f"\n❌ CRITICAL ERROR")
            logger.log(f"{e}")
            #logger.log("\n--- Traceback ---")
            #logger.log(traceback.format_exc())

            # Update status callback if present
            if status_callback:
                status_callback("step", 0, 0, "error")
        # Get the current date and time
        #now = datetime.now()

        # Format the time as a string (HH:MM:SS) using strftime()
        current_time = "" #now.strftime("%H:%M:%S")

        if success:
            logger.log(f"\n✅ Build finished successfully. {current_time}")
        else:
            logger.log(f"🔴Build failed for {final_step_name}.  {current_time}")

    def has_profile(self, profile_name: str) -> bool:
        """Checks if a specific profile key exists in the YAML config."""
        return profile_name in self.config.get('PROFILES', {})

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
        self.logger = get_logger()

    def _is_step_outdated(self, command: Dict) -> Tuple[UpdateCode, str]:
        """Checks a single step to see if it needs to be rebuilt, with detailed debug logging."""
        output_path = command['output']
        node_name = command.get('node_name', 'UnknownStep')

        self.logger.debug(f"\n--- Checking status of step '{node_name}' ---")
        self.logger.debug(f"  - Output file: '{output_path}'")

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
        command_map, execution_graph = self._generate_command_map_and_graph(
            profile_name, final_step_name
        )

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
                    f"\nProfile '{profile_name}' not found. \n\nAvailable profiles:\n - {available}\n"
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
            full_paths = [os.path.join(input_dir, f) for f in input_basenames]
            context['INPUT_FILES'] = full_paths

        command_map, resolved_outputs = {}, {}
        for node_name in nx.topological_sort(execution_graph):
            node_data = execution_graph.nodes[node_name]

            # --- IMPROVED ERROR HANDLING ---
            try:
                command_map[node_name] = command_gen.generate_for_node(
                    node_name, node_data, context, resolved_outputs
                )
            except Exception as e:
                # This catches errors in CommandGenerator (like invalid format strings)
                # and adds the context of WHICH rule failed.
                raise RuntimeError(f"Error generating COMMAND for rule '{node_name}': {e}") from e

        return command_map, execution_graph

class BuildExecutor:
    """Executes a build plan, running commands in parallel where possible."""

    def __init__(self, state_manager, config: Dict):
        self.state_manager = state_manager
        self.build_state = state_manager.load_state()
        self.config = config
        self.update_codes = {
            UpdateCode.UP_TO_DATE: "", UpdateCode.MISSING_OUTPUT: "(Creating Output)",
            UpdateCode.NOT_TRACKED: "(first build)",
            UpdateCode.COMMAND_CHANGED: "(command has changed)",
            UpdateCode.INPUTS_CHANGED: "(input file list has changed)",
            UpdateCode.PARAMS_CHANGED: "(parameters have changed)",
            UpdateCode.NEWER_INPUT: "(input '{context}' is newer)",
            UpdateCode.MISSING_INPUT: "(input '{context}' is missing)",
            UpdateCode.STALE_TARGET: "(stale target)"
        }

    def execute_plan(self, plan: BuildPlan, logger: BuildLogger, status_callback=None) -> bool:
        """Executes the build plan, managing parallel execution and state."""
        total_to_run = len(plan.steps_to_run)
        finished_count = 0

        # Track timing for reporting
        step_timings = {}
        build_start_time = time.time()

        # --- Initial Status Update ---
        if status_callback:
            status_callback("step", 0, total_to_run, "started")

        for step in plan.steps_to_skip:
            logger.log(f"Skipping '{step.node_name}' (up-to-date)")

        if not plan.steps_to_run:
            if status_callback:
                status_callback("step", 0, 0, "done")
            return True

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

                # Capture timing if available
                if 'elapsed_time' in result_data:
                    step_timings[step_name] = result_data['elapsed_time']

                if status == 'EXECUTED':
                    finished_count += 1
                    logger.log(f"✅ Finished step '{step_name}' [{finished_count}/{total_to_run}]")
                    if status_callback:
                        status_callback("step", finished_count, total_to_run, "done")
                    self.build_state[result_data['output_path']] = {
                        "hashes": result_data['hashes'], "mtime": result_data['mtime']
                    }
                elif status == 'FAILED':
                    halt_build = True
                    logger.log(f"🔺 Build failed for Step '{step_name}'")
                    if status_callback:
                        status_callback("step", finished_count, total_to_run, "error")

            if halt_build:
                self.state_manager.save_state(self.build_state)
                return False

        self.state_manager.save_state(self.build_state)

        # --- TIMING REPORT ---
        total_build_time = time.time() - build_start_time
        self._print_timing_report(logger, step_timings, total_build_time)

        return True

    @staticmethod
    def _run_single_command(task: Tuple[str, Dict, str]) -> Tuple[str, Dict]:
        """Runs a command and streams all output to the configured log."""
        logger = get_logger()
        step_name, command, update_text = task
        output_path = command['output']

        # --- Helper for log truncation ---
        def _truncate(text: str, limit: int = 400) -> str:
            """Keeps the start and end of long strings."""
            if len(text) <= limit:
                return text

            # Keep first 40% and last 40% of the limit
            keep = int(limit * 0.4)
            omitted_count = len(text) - (keep * 2)

            # Formatting: Clear brackets with internal spacing
            return f"{text[:keep]} [ ... {omitted_count} chars truncated ... ] {text[-keep:]}"

        # Log the command (Truncated)
        cmd_display = _truncate(command['cmd_string'])
        logger.log(f"\n▶️  Running step '{step_name}': {update_text}")
        logger.log(f"  [{step_name}]       {cmd_display}")

        start_time = time.perf_counter()

        try:
            process = subprocess.Popen(
                command['cmd_string'], shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace'
            )

            for line in iter(process.stdout.readline, ''):
                clean_line = line.strip()
                if clean_line:
                    # Also truncate massive output lines (rare but possible with WKT echos)
                    logger.log(f"  [{step_name}]       {_truncate(clean_line)}")

            return_code = process.wait()

            end_time = time.perf_counter()
            elapsed = end_time - start_time

            if return_code != 0:
                raise subprocess.CalledProcessError(return_code, "")

            new_mtime = os.path.getmtime(output_path)
            result_data = {
                'step_name': step_name,
                'output_path': output_path,
                'hashes': command['hashes'],
                'mtime': new_mtime,
                'elapsed_time': elapsed
            }
            return 'EXECUTED', result_data
        except Exception as e:
            logger.log(f"🔺 Step '{step_name}' failed: {e}")
            result_data = {'step_name': step_name}
            return 'FAILED', result_data

    def _print_timing_report(self, logger: BuildLogger, step_timings: Dict[str, float], total_time: float):
        """Generates and logs the timing summary table."""
        logger.log("\n🔵 Timing Report:")

        # Sort by duration (Longest first)
        sorted_steps = sorted(step_timings.items(), key=lambda item: item[1], reverse=True)

        # Calculate "CPU Time" (Sum of all work) vs "Wall Time" (Real world time)
        total_cpu_time = sum(step_timings.values())

        for step_name, duration in sorted_steps:
            # Percent of the Wall Clock time this step was active
            percent = (duration / total_time) * 100 if total_time > 0 else 0

            minutes = int(duration // 60)
            seconds = duration % 60
            if minutes > 0:
                time_str = f"{minutes}:{seconds:05.2f}"
            else:
                time_str = f"{seconds:.2f}s"

            logger.log(f"{step_name:<30} {percent:>5.1f}%   {time_str}")

        t_min = int(total_time // 60)
        t_sec = total_time % 60

        # Calculate parallelism factor (e.g., 2.5x speedup)
        speedup = total_cpu_time / total_time if total_time > 0 else 1.0

        logger.log("-" * 50)
        logger.log(f"Wall Time: {t_min}:{t_sec:05.2f}  Parallel Speedup: {speedup:.1f}x")

class BuildReporter:
    """Generates human-readable description of the workflow."""

    def __init__(self, config: Dict):
        self.config = config

    def generate_mermaid_diagram(self, graph: nx.DiGraph) -> str:
        """Creates a Mermaid graph for the workflow."""
        if not graph.nodes:
            return "graph TD;\n    Empty_Workflow[Workflow is empty];"

        lines = ["graph TD;"]

        # Build node styles based on dependency type (Source vs Process)
        for node in graph.nodes():
            # In topological sort, sources have in-degree 0
            if graph.in_degree(node) == 0:
                lines.append(f"    {node}[{node}]:::source;")
            else:
                lines.append(f"    {node}[{node}]:::process;")

        # Edges
        for u, v in graph.edges():
            lines.append(f"    {u} --> {v};")

        # Styling Definitions
        lines.append("    classDef source fill:#d4edda,stroke:#155724,color:#155724;")
        lines.append("    classDef process fill:#e2e3e5,stroke:#383d41,color:#383d41;")

        return "\n".join(lines)

    def describe_workflow(self, profile_name: str) -> str:
        """Generates a full Markdown report for the workflow."""
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        project_name = self.config.get("GENERAL", {}).get("PROJECT_NAME", "LiteBuild Project")

        # 1. Generate the Plan to resolve all variables
        planner = BuildPlanner(self.config, {})
        # Passing None for final_step gets the whole graph
        plan = planner.plan_build(profile_name, None)

        if not plan.command_map:
            return f"# {project_name} - {profile_name}\nNo steps defined for this profile."

        # Get the topologically sorted graph
        graph = plan.execution_graph
        build_order = list(nx.topological_sort(graph))
        final_step = build_order[-1]
        final_output = plan.command_map[final_step]['output']

        # --- HEADER ---
        lines = [
            f"# {project_name} Pipeline Documentation",
            "",
            f"**Profile:** `{profile_name}`  ",
            f"**Date:**     {timestamp}  ",
            f"**Target Output:** `{final_output}`",
            "",
            "---",
            ""
        ]

        # --- OVERVIEW SECTION ---
        # Checks for the global OVERVIEW key in the config
        overview_text = self.config.get("OVERVIEW")
        if overview_text:
            lines.append("## Overview")
            lines.append(overview_text.strip())
            lines.append("")
            lines.append("---")
            lines.append("")

        # --- MERMAID DIAGRAM ---
        lines.append("## Workflow  ")
        lines.append("```mermaid")
        lines.append(self.generate_mermaid_diagram(graph))
        lines.append("```")
        lines.append("")

        # --- STEP DETAIL ---
        lines.append("## Detailed Steps")

        workflow_def = self.config.get("WORKFLOW", {})

        for node_name in build_order:
            cmd_data = plan.command_map[node_name]
            step_def = workflow_def.get(node_name, {})
            rule_name = step_def.get('RULE', {}).get('NAME')

            lines.append(f"### {node_name}")

            # ---  DESCRIPTION LOGIC ---
            if "DESCRIPTION" in step_def:
                # Use blockquote for user-defined descriptions (handles multi-line well)
                lines.append(f"> {step_def['DESCRIPTION']}")
            else:
                # Fallback to technical description
                lines.append(f"_Executes rule: `{rule_name}`_")

            lines.append("")

            # Inputs
            if cmd_data['input_files']:
                lines.append("**Inputs:**")
                for f in cmd_data['input_files']:
                    lines.append(f"* `{f}`")
                lines.append("")

            # Output
            lines.append(f"**Output:** `{cmd_data['output']}`")
            lines.append("")

            # Command
            lines.append("**Command:**")
            lines.append("```bash")
            lines.append(cmd_data['cmd_string'])
            lines.append("```")
            lines.append("---")

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