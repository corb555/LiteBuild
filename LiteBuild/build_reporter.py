from datetime import datetime
from pathlib import Path
from typing import Dict

import networkx as nx

from LiteBuild.build_planner import BuildPlanner
from LiteBuild.provenance import analyze_provenance, group_sources_by_entry
from LiteBuild.schema import YAMLSection


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
                lines.append(f"    {node}[{node}]:::root;")
            else:
                lines.append(f"    {node}[{node}]:::process;")

        # Edges
        for u, v in graph.edges():
            lines.append(f"    {u} --> {v};")

        # Styling Definitions
        lines.append("    classDef root fill:#d4edda,stroke:#155724,color:#155724;")
        lines.append("    classDef process fill:#e2e3e5,stroke:#383d41,color:#383d41;")

        return "\n".join(lines)

    @staticmethod
    def _format_file_size(size_bytes: int) -> str:
        """Return a compact human-readable file size."""
        units = ("B", "KB", "MB", "GB", "TB")
        size = float(size_bytes)

        for unit in units:
            if size < 1024.0 or unit == units[-1]:
                if unit == "B":
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024.0

        return f"{size_bytes} B"

    @classmethod
    def _append_paths(cls, lines: list[str], paths: list[str]) -> None:
        """Append sorted source paths with observed file metadata."""
        previous_dir = None

        for path_string in sorted(paths):
            path = Path(path_string)
            current_dir = str(path.parent)

            if previous_dir is not None and current_dir != previous_dir:
                lines.append("")

            if path.is_file():
                stat = path.stat()
                modified = datetime.fromtimestamp(stat.st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                size = cls._format_file_size(stat.st_size)
                lines.append(
                    f"* `{path_string}` — {size}; modified {modified}"
                )
            else:
                lines.append(f"* `{path_string}`")

            previous_dir = current_dir

    @classmethod
    def _append_provenance_group(
        cls,
        lines: list[str],
        entry,
        paths: list[str],
    ) -> None:
        """Append one resolved provenance entry and its source files."""
        lines.append(f"#### {entry.name}")
        lines.append("")
        lines.append(entry.description)
        lines.append("")

        if entry.attribution:
            lines.append(f"**Attribution:** {entry.attribution}")
            lines.append("")

        if entry.citation:
            lines.append(f"**Citation:** {entry.citation}")
            lines.append("")

        lines.append("**Files:**")
        cls._append_paths(lines, paths)
        lines.append("")

    def _append_provenance(
        self,
        lines: list[str],
        command_map: Dict,
        context: Dict,
    ) -> None:
        """Append provenance derived from the resolved workflow."""
        provenance_config = self.config.get(YAMLSection.PROVENANCE, {})
        analysis = analyze_provenance(
            command_map,
            provenance_config,
            context,
        )

        if analysis.ambiguous:
            details = []
            for ambiguity in analysis.ambiguous:
                matches = ", ".join(
                    entry.name for entry in ambiguity.matches
                )
                details.append(f"  {ambiguity.path}: {matches}")

            raise ValueError(
                "Ambiguous provenance declarations:\n"
                + "\n".join(details)
            )

        lines.append("## Provenance")
        lines.append("")

        data_groups = group_sources_by_entry(analysis.data_sources)
        config_groups = group_sources_by_entry(analysis.config_sources)

        if data_groups:
            lines.append("### Data Sources")
            lines.append("")

            for entry, paths in data_groups:
                self._append_provenance_group(lines, entry, paths)

        if config_groups:
            lines.append("### Config Sources")
            lines.append("")

            for entry, paths in config_groups:
                self._append_provenance_group(lines, entry, paths)

        if analysis.missing:
            lines.append("### Missing Provenance")
            lines.append("")
            self._append_paths(lines, analysis.missing)
            lines.append("")

        if not data_groups and not config_groups and not analysis.missing:
            lines.append("_No provenance sources._")
            lines.append("")

        lines.append("---")
        lines.append("")

    def describe_workflow(self, profile_name: str) -> str:
        """Generates a full Markdown report for the workflow."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        project_cfg = self.config.get(YAMLSection.PROJECT)
        project_name = project_cfg.get("PROJECT_NAME", "LiteBuild Project")

        # Resolve the complete workflow without running freshness checks.
        # Reporting needs the resolved command map and variable context, but it
        # remains available even when provenance coverage is incomplete.
        planner = BuildPlanner(self.config, {})
        command_map, graph, context = (
            planner._generate_command_map_graph_and_context(
                profile_name,
                None,
            )
        )

        if not command_map:
            return f"# {project_name} - {profile_name}\nNo steps defined for this profile."

        build_order = list(nx.topological_sort(graph))
        final_step = build_order[-1]
        final_output = command_map[final_step]['output']

        # --- HEADER ---
        lines = [f"# {project_name} Pipeline Documentation", "", f"**Profile:** `{profile_name}`  ",
            f"**Date:**     {timestamp}  ", f"**Target Output:** `{final_output}`", "", "---", ""]

        # --- OVERVIEW SECTION ---
        # Checks for the  OVERVIEW key in the config

        overview_text = project_cfg.get("OVERVIEW")
        if overview_text:
            lines.append("## Overview")
            lines.append(overview_text.strip())
            lines.append("")
            lines.append("---")
            lines.append("")

        # --- PROVENANCE ---
        self._append_provenance(
            lines,
            command_map,
            context,
        )

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
            cmd_data = command_map[node_name]
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
