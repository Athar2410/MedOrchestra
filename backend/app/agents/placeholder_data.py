"""PHASE 1 PLACEHOLDER DATA — not clinical knowledge.

A tiny hand-written differential table so the Diagnostician runs end to end
before PubMed retrieval-augmented diagnosis exists (Phase 3).
"""

# (any of these keywords in the complaint, candidate conditions with base confidence)
KEYWORD_DIFFERENTIALS: list[tuple[tuple[str, ...], list[tuple[str, float]]]] = [
    (
        ("chest pain", "chest tightness", "chest pressure"),
        [("Acute coronary syndrome", 0.72), ("Pulmonary embolism", 0.4), ("GERD", 0.25)],
    ),
    (
        ("fever", "cough"),
        [("Community-acquired pneumonia", 0.7), ("Acute bronchitis", 0.4), ("Influenza", 0.35)],
    ),
    (
        ("headache", "neck stiffness"),
        [("Bacterial meningitis", 0.65), ("Subarachnoid haemorrhage", 0.45), ("Migraine", 0.2)],
    ),
    (
        ("abdominal pain", "right lower"),
        [("Acute appendicitis", 0.7), ("Mesenteric adenitis", 0.3), ("Ovarian torsion", 0.25)],
    ),
    (
        ("shortness of breath", "wheeze"),
        [("Acute asthma exacerbation", 0.68), ("COPD exacerbation", 0.45), ("Heart failure", 0.3)],
    ),
]
