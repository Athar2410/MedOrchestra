"""Download the DDInter 2.0 drug-drug interaction CSVs into backend/data/ddinter/.

DDInter is licensed CC BY-NC-SA 4.0 (https://ddinter2.scbdd.com/terms/) — non-commercial
use with attribution, so the raw files are not committed to git; run this script instead.

    .\\.venv\\Scripts\\python -m pipelines.download_ddinter [--force]
"""

import argparse
import shutil
import sys
import time
import urllib.request
from pathlib import Path

BASE_URL = "https://ddinter2.scbdd.com/static/media/download/ddinter_downloads_code_{}.csv"
# Files are split by ATC top-level class; a cross-class pair may appear in more than one file.
ATC_CODES = ["A", "B", "D", "H", "L", "P", "R", "V"]
DEST = Path(__file__).resolve().parents[1] / "data" / "ddinter"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="re-download existing files")
    args = parser.parse_args()

    DEST.mkdir(parents=True, exist_ok=True)
    for code in ATC_CODES:
        url = BASE_URL.format(code)
        target = DEST / Path(url).name
        if target.exists() and not args.force:
            print(f"skip  {target.name} (exists)")
            continue
        _download(url, target)
        print(f"saved {target.name} ({target.stat().st_size / 1024:.0f} KB)")
    return 0


def _download(url: str, target: Path, attempts: int = 4) -> None:
    # The DDInter server is slow; stream in chunks and retry on timeouts.
    tmp = target.with_suffix(".part")
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp, tmp.open("wb") as out:
                shutil.copyfileobj(resp, out, length=64 * 1024)
            tmp.replace(target)
            return
        except (TimeoutError, OSError) as exc:
            print(f"retry {target.name} (attempt {attempt}/{attempts} failed: {exc})")
            time.sleep(2 * attempt)
    tmp.unlink(missing_ok=True)
    raise RuntimeError(f"Could not download {url}")


if __name__ == "__main__":
    sys.exit(main())
