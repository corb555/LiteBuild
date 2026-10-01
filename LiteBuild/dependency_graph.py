from typing import List

import networkx as nx


class DependencyGraph:
    """Construct and query LiteBuild's workflow dependency DAG.

    This component performs no direct logging. Invalid workflow structure is
    reported by raising descriptive exceptions for the planner/orchestration
    layer to handle.
    """

    def __init__(self, pipeline_config: dict):
        self._graph = self._build(pipeline_config)

    @staticmethod
    def _build(pipeline_config: dict) -> nx.DiGraph:
        """Construct the dependency graph from workflow configuration."""
        graph = nx.DiGraph()

        for node_name, config_data in pipeline_config.items():
            if config_data.get("ENABLED", False):
                graph.add_node(node_name, **config_data)

        for node_name, config_data in graph.nodes(data=True):
            for dep_name in config_data.get("REQUIRES", []):
                if not graph.has_node(dep_name):
                    raise ValueError(
                        f"WORKFLOW step '{node_name}' requires undefined step "
                        f"'{dep_name}'."
                    )
                graph.add_edge(dep_name, node_name)

        if not nx.is_directed_acyclic_graph(graph):
            cycle_edges = nx.find_cycle(graph)
            cycle_nodes = [cycle_edges[0][0]]
            cycle_nodes.extend(edge[1] for edge in cycle_edges)
            cycle_text = " -> ".join(map(str, cycle_nodes))

            raise ValueError(
                f"WORKFLOW contains a circular dependency: {cycle_text}"
            )

        return graph

    def get_execution_subgraph(
        self,
        final_step_name: str | None = None,
    ) -> nx.DiGraph:
        """Return the DAG required to build through ``final_step_name``."""
        if not final_step_name:
            return self._graph

        if not self._graph.has_node(final_step_name):
            available = sorted(self._graph.nodes)

            if available:
                available_text = "\n  - ".join(available)
                available_message = (
                    f"\nAvailable workflow steps:\n  - {available_text}"
                )
            else:
                available_message = "\nNo enabled workflow steps are configured."

            raise ValueError(
                f"Final step '{final_step_name}' not found in WORKFLOW."
                f"{available_message}"
            )

        ancestors = nx.ancestors(self._graph, final_step_name)
        nodes_to_run = ancestors | {final_step_name}

        return self._graph.subgraph(nodes_to_run)

    @staticmethod
    def get_build_order(graph: nx.DiGraph) -> List[str]:
        """Return nodes in topological execution order."""
        return list(nx.topological_sort(graph))
