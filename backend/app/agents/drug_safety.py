import asyncio
from itertools import combinations

from app.agents.base import AgentContext, agent_node
from app.agents.placeholder_data import INTERACTIONS, KNOWN_DRUGS
from app.config import get_settings
from app.graph.state import ClinicalState
from app.schemas import DrugInteraction


@agent_node("drug_safety")
async def drug_safety_agent(state: ClinicalState, ctx: AgentContext) -> dict:
    # Phase 2 replaces the placeholder table with RxNorm normalization + a DDInter
    # NetworkX graph (with enzyme nodes for depth-2 inferred interactions) and an LLM
    # plain-English explanation per alert.
    meds = sorted({m.strip().lower() for m in state["case"].medications if m.strip()})
    if len(meds) < 2:
        ctx.think("Fewer than two medications — no pairs to check")
        return {"drug_interactions": [], "unrecognized_medications": []}

    ctx.think(f"Checking {len(meds) * (len(meds) - 1) // 2} medication pairs")
    await asyncio.sleep(get_settings().stub_delay_seconds)

    interactions = []
    for a, b in combinations(meds, 2):
        hit = INTERACTIONS.get(frozenset({a, b}))
        if hit:
            severity, mechanism = hit
            interactions.append(
                DrugInteraction(
                    drug_a=a,
                    drug_b=b,
                    severity=severity,
                    mechanism=mechanism,
                    explanation=mechanism,
                    evidence="Placeholder table (Phase 1)",
                )
            )

    order = {"major": 0, "moderate": 1, "minor": 2}
    interactions.sort(key=lambda i: order[i.severity])
    return {
        "drug_interactions": interactions,
        "unrecognized_medications": [m for m in meds if m not in KNOWN_DRUGS],
    }
