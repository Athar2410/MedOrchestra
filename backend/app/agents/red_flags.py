"""Rule-based red-flag phrases for the triage fallback.

Used when the LLM is unavailable, so that a time-critical complaint with normal vital
signs (classic ACS, stroke) is not triaged LOW by NEWS2 alone. Deliberately
conservative: in fallback mode over-triage is the safe failure direction.
"""

import re

# (label, pattern) — patterns run against the lower-cased complaint.
RED_FLAGS: list[tuple[str, str]] = [
    ("chest pain", r"chest (pain|tightness|pressure|heaviness)|crushing"),
    ("pain radiating to arm/jaw", r"radiat\w* (to|into|down) (the )?(left )?(arm|jaw|neck|back)"),
    ("diaphoresis", r"\bsweat(y|ing)\b|diaphore|clammy"),
    (
        "stroke signs",
        r"facial droop|face droop|slurred speech|one[- ]sided weakness|hemipleg|aphasia",
    ),
    ("sudden severe headache", r"thunderclap|worst headache|sudden severe headache"),
    ("neck stiffness", r"neck stiffness|stiff neck"),
    ("difficulty breathing", r"can'?t breathe|unable to breathe|struggling to breathe|gasping"),
    ("airway swelling", r"(swollen|swelling of) (the )?(lips?|tongue|throat)|anaphyla"),
    ("GI bleeding", r"vomit\w* blood|haematemesis|hematemesis|black(,)? tarry|melaena|melena"),
    ("altered consciousness", r"unconscious|unresponsive|collaps\w*|seizure|fitting"),
    ("suicidal ideation", r"suicid|overdose"),
    ("pregnancy with pain or bleeding", r"pregnan\w*.*(bleed|pain)|(bleed|pain).*pregnan"),
]
_COMPILED = [(label, re.compile(pattern)) for label, pattern in RED_FLAGS]


def find_red_flags(complaint: str) -> list[str]:
    text = complaint.lower()
    return [label for label, pattern in _COMPILED if pattern.search(text)]
