"""CSV persistence for dataset curation decisions."""

import csv
from pathlib import Path

DEFAULT_CSV_PATH = Path(__file__).resolve().parent / "curation_log.csv"


def append_curation_row(
    episode_index: int,
    action: str,
    detail: str,
    csv_path: Path | None = None,
) -> None:
    """
    Append one row to the curation CSV: episode_index, action, detail.

    Creates the file with a header row if it does not exist.
    """
    path = csv_path or DEFAULT_CSV_PATH
    file_exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["episode_index", "action", "detail"])
        writer.writerow([episode_index, action, detail])
