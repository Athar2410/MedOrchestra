"""DDInter 2.0 drug-drug interaction graph.

Undirected NetworkX graph: nodes are lower-cased DDInter drug names, edges carry
the interaction severity. DDInter splits its data into one CSV per ATC class, so
a cross-class pair can appear in several files: duplicates are merged, keeping
the most severe graded level (a graded level always beats "unknown").
"""

import csv
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import networkx as nx

from app.config import get_settings
from app.schemas import Severity

logger = logging.getLogger(__name__)

SEVERITY_RANK: dict[Severity, int] = {"unknown": 0, "minor": 1, "moderate": 2, "major": 3}


@dataclass(frozen=True)
class InteractionEdge:
    drug_a: str
    drug_b: str
    severity: Severity
    ddinter_ids: tuple[str, str]


class DrugGraph:
    def __init__(self, graph: nx.Graph) -> None:
        self.graph = graph

    @classmethod
    def from_csvs(cls, paths: Iterable[Path]) -> "DrugGraph":
        graph = nx.Graph()
        for path in paths:
            with path.open(newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    a, b = row["Drug_A"].strip().lower(), row["Drug_B"].strip().lower()
                    if not a or not b or a == b:
                        continue
                    graph.add_node(a, ddinter_id=row["DDInterID_A"].strip())
                    graph.add_node(b, ddinter_id=row["DDInterID_B"].strip())
                    level = row["Level"].strip().lower()
                    severity: Severity = level if level in SEVERITY_RANK else "unknown"  # type: ignore[assignment]
                    current = graph.get_edge_data(a, b)
                    if (
                        current is None
                        or SEVERITY_RANK[severity] > SEVERITY_RANK[current["severity"]]
                    ):
                        graph.add_edge(a, b, severity=severity)
        return cls(graph)

    def __contains__(self, drug: str) -> bool:
        return drug in self.graph

    @property
    def drug_names(self) -> list[str]:
        return list(self.graph.nodes)

    def interactions_among(self, drugs: Iterable[str]) -> list[InteractionEdge]:
        """Every interacting pair within `drugs` (graph neighbourhood intersection),
        most severe first."""
        selected = sorted({d for d in drugs if d in self.graph})
        selected_set = set(selected)
        edges = []
        for a in selected:
            for b in selected_set.intersection(self.graph.neighbors(a)):
                if b > a:
                    edges.append(
                        InteractionEdge(
                            drug_a=a,
                            drug_b=b,
                            severity=self.graph.edges[a, b]["severity"],
                            ddinter_ids=(
                                self.graph.nodes[a]["ddinter_id"],
                                self.graph.nodes[b]["ddinter_id"],
                            ),
                        )
                    )
        edges.sort(key=lambda e: (-SEVERITY_RANK[e.severity], e.drug_a, e.drug_b))
        return edges


@lru_cache
def get_drug_graph() -> DrugGraph | None:
    """Load once per process; None if the DDInter CSVs have not been downloaded."""
    paths = sorted(get_settings().ddinter_dir.glob("*.csv"))
    if not paths:
        logger.warning("No DDInter CSVs in %s", get_settings().ddinter_dir)
        return None
    graph = DrugGraph.from_csvs(paths)
    logger.info(
        "Loaded DDInter graph: %d drugs, %d interactions",
        graph.graph.number_of_nodes(),
        graph.graph.number_of_edges(),
    )
    return graph
