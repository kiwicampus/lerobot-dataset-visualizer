"""CSV persistence for dataset curation decisions."""

import csv
import os
from pathlib import Path

DEFAULT_CSV_PATH = Path(__file__).resolve().parent / "curation_log.csv"


def append_curation_row(
    episode_index: int,
    action: str,
    detail: str,
    csv_path: Path | None = None,
) -> None:
    """Append one row to the curation CSV. Creates the file with a header if it does not exist."""
    path = csv_path or DEFAULT_CSV_PATH
    file_exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["episode_index", "action", "detail"])
        writer.writerow([episode_index, action, detail])


def get_curation_row(
    episode_index: int,
    csv_path: Path | None = None,
) -> tuple[str, str] | None:
    """Return (action, detail) for episode_index, or None if not in the log."""
    path = csv_path or DEFAULT_CSV_PATH
    if not path.exists():
        return None
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 3:
                continue
            try:
                if int(row[0]) == episode_index:
                    return row[1], row[2]
            except ValueError:
                continue
    return None


def update_curation_row(
    episode_index: int,
    action: str,
    detail: str,
    csv_path: Path | None = None,
) -> None:
    """Replace the first row matching episode_index in-place. Falls back to append if not found."""
    path = csv_path or DEFAULT_CSV_PATH
    if not path.exists():
        append_curation_row(episode_index, action, detail, csv_path)
        return
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    replaced = False
    for i, row in enumerate(rows):
        if not row:
            continue
        try:
            if int(row[0]) == episode_index:
                rows[i] = [str(episode_index), action, detail]
                replaced = True
                break
        except ValueError:
            continue
    if not replaced:
        rows.append([str(episode_index), action, detail])
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)


def get_resume_episode(csv_path: Path | None = None) -> int | None:
    """Return the first uncurated episode index to resume curation from.

    - With EPISODES_IDS="lo,hi": returns the first episode in [lo, hi] not in the log.
    - Without EPISODES_IDS: returns max(curated) + 1.
    - Returns None when the log is empty or all range episodes are curated.
    """
    path = csv_path or DEFAULT_CSV_PATH
    curated: set[int] = set()
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if not row:
                    continue
                try:
                    curated.add(int(row[0]))
                except ValueError:
                    pass

    if not curated:
        return None

    env = os.environ.get("EPISODES_IDS", "")
    parts = [p.strip() for p in env.split(",") if p.strip()]
    nums: list[int] = []
    for p in parts:
        try:
            nums.append(int(p))
        except ValueError:
            pass

    if len(nums) >= 2:
        lo, hi = min(nums[0], nums[1]), max(nums[0], nums[1])
        for i in range(lo, hi + 1):
            if i not in curated:
                return i
        return None

    return max(curated) + 1
