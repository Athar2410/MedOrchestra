"""DDXPlus (Fansi Tchango et al., 2022; CC BY 4.0) -> MedOrchestra evaluation cases.

Each synthetic patient is a list of coded questionnaire answers plus the true pathology.
Answers are rendered as English "question: answer" lines; history items (antecedents)
are listed separately. One case per pathology (+ extras to reach `n`), fixed seed.

    .\\.venv\\Scripts\\python -m eval.ddxplus --n 50     # writes data/ddxplus/cases.jsonl

Files (download to backend/data/ddxplus/): https://figshare.com/articles/dataset/20043374
"""

import argparse
import ast
import csv
import io
import json
import random
import zipfile
from collections import defaultdict

from app.config import BACKEND_DIR
from app.schemas import CaseInput

DIR = BACKEND_DIR / "data" / "ddxplus"
CASES_FILE = DIR / "cases.jsonl"


def _render(evidences: list[str], catalog: dict) -> tuple[list[str], list[str]]:
    """Evidence codes -> (symptom lines, history lines)."""
    answers: dict[str, list[str]] = defaultdict(list)
    for item in evidences:
        code, _, value = item.partition("_@_")
        meaning = catalog[code].get("value_meaning", {}).get(value, {}).get("en")
        if value:
            answers[code].append(meaning or value)  # numeric scales keep the number
        else:
            answers[code]  # binary: present = yes  # noqa: B018
    symptoms, history = [], []
    for code, values in answers.items():
        q = catalog[code]["question_en"].rstrip("?").strip()
        line = f"{q}: {', '.join(values)}" if values else f"{q}: yes"
        (history if catalog[code].get("is_antecedent") else symptoms).append(line)
    return symptoms, history


def ddxplus_classes() -> list[str]:
    conditions = json.loads((DIR / "release_conditions.json").read_text(encoding="utf-8"))
    return sorted(c["cond-name-eng"] for c in conditions.values())


def build_cases(n: int = 50, seed: int = 7) -> list[dict]:
    catalog = json.loads((DIR / "release_evidences.json").read_text(encoding="utf-8"))
    conditions = json.loads((DIR / "release_conditions.json").read_text(encoding="utf-8"))
    icd10 = {c["cond-name-eng"]: c["icd10-id"] for c in conditions.values()}
    with zipfile.ZipFile(DIR / "release_test_patients.zip") as z:
        rows = list(
            csv.DictReader(io.TextIOWrapper(z.open("release_test_patients"), encoding="utf-8"))
        )

    rng = random.Random(seed)
    by_path: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_path[r["PATHOLOGY"]].append(r)
    picked = [rng.choice(by_path[p]) for p in sorted(by_path)]
    while len(picked) < n:
        picked.append(rng.choice(rows))

    cases = []
    for i, r in enumerate(picked[:n]):
        evidences = ast.literal_eval(r["EVIDENCES"])
        symptoms, history = _render(evidences, catalog)
        first = catalog[r["INITIAL_EVIDENCE"]]["question_en"].rstrip("?")
        complaint = f"Presenting with: {first}.\nSymptoms:\n- " + "\n- ".join(symptoms)
        if history:
            complaint += "\nHistory:\n- " + "\n- ".join(history)
        case = CaseInput(
            chief_complaint=complaint[:4000],
            age=min(int(r["AGE"]), 120),
            sex={"M": "male", "F": "female"}.get(r["SEX"]),
        )
        cases.append(
            {
                "id": f"ddx-{i:03d}",
                "truth": r["PATHOLOGY"],
                "icd10": icd10.get(r["PATHOLOGY"]),
                "case": case.model_dump(mode="json"),
            }
        )
    return cases


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=50)
    args = parser.parse_args()
    cases = build_cases(args.n)
    CASES_FILE.write_text("\n".join(json.dumps(c) for c in cases), encoding="utf-8")
    print(f"wrote {len(cases)} cases covering {len({c['truth'] for c in cases})} pathologies")
    print(cases[0]["truth"], "\n", cases[0]["case"]["chief_complaint"][:600])


if __name__ == "__main__":
    main()
