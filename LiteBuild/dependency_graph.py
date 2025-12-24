from typing import List

import networkx as nx


class DependencyGraph:
    """Manages the build's dependency graph (DAG)."""

    def __init__(self, pipeline_config: dict):
        self._graph = self._build(pipeline_config)

    @staticmethod
    def _build(pipeline_config: dict) -> nx.DiGraph:
        """Constructs the dependency graph from the pipeline configuration."""
        gr = nx.DiGraph()
        for node_name, config_data in pipeline_config.items():
            # In the original code, ENABLED defaults to False in the schema.
            if config_data.get("ENABLED", False):
                gr.add_node(node_name, **config_data)

        for node_name, config_data in gr.nodes(data=True):
            for dep_name in config_data.get("REQUIRES", []):
                if not gr.has_node(dep_name):
                    raise ValueError(
                        f"❌ '{node_name}' has an undefined requirement: '{dep_name}'"
                    )
                gr.add_edge(dep_name, node_name)

        if not nx.is_directed_acyclic_graph(gr):
            cycle_info = " -> ".join(map(str, nx.find_cycle(gr)))
            raise nx.NetworkXUnfeasible(f"Circular dependency found in WORKFLOW: {cycle_info}")
        return gr

    def get_execution_subgraph(self, final_step_name: str = None) -> nx.DiGraph:
        """Returns the subgraph required to build up to the final_step_name."""
        if not final_step_name:
            return self._graph  # Full build

        if not self._graph.has_node(final_step_name):
            raise ValueError(f"❌Final step '{final_step_name}' not found in WORKFLOW.")

        ancestors = nx.ancestors(self._graph, final_step_name)
        nodes_to_run = ancestors.union({final_step_name})
        return self._graph.subgraph(nodes_to_run)

    @staticmethod
    def get_build_order(graph: nx.DiGraph) -> List[str]:
        """Returns the topologically sorted list of nodes for execution."""
        return list(nx.topological_sort(graph))
