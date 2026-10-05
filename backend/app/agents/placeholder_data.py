"""PHASE 1 PLACEHOLDER DATA — not clinical knowledge.

Tiny hand-written tables so the pipeline runs end to end before the real
sources exist. Replaced by the DDInter interaction graph (Phase 2) and PubMed
retrieval-augmented diagnosis (Phase 3).
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

# Unordered drug pairs -> (severity, mechanism)
INTERACTIONS: dict[frozenset[str], tuple[str, str]] = {
    frozenset({"warfarin", "aspirin"}): (
        "major",
        "Additive anticoagulant/antiplatelet bleeding risk",
    ),
    frozenset({"warfarin", "ibuprofen"}): ("major", "NSAID increases bleeding risk with warfarin"),
    frozenset({"simvastatin", "clarithromycin"}): (
        "major",
        "CYP3A4 inhibition raises statin levels",
    ),
    frozenset({"sertraline", "tramadol"}): ("major", "Serotonin syndrome risk"),
    frozenset({"lisinopril", "spironolactone"}): ("moderate", "Additive hyperkalaemia risk"),
    frozenset({"metformin", "furosemide"}): ("minor", "Furosemide may raise metformin levels"),
}

KNOWN_DRUGS: set[str] = {drug for pair in INTERACTIONS for drug in pair} | {
    "paracetamol",
    "acetaminophen",
    "metoprolol",
    "amlodipine",
    "atorvastatin",
    "omeprazole",
}
