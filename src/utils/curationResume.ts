import fs from "fs";
import path from "path";

/**
 * Reads dataset_curator/curation_log.csv and returns the first episode index
 * within the EPISODES_IDS range that has not yet been curated.
 *
 * - If EPISODES_IDS defines a range (e.g. "10,20"), scans that range and
 *   returns the first episode missing from the log.
 * - If EPISODES_IDS is not set, returns max(curated) + 1.
 * - Returns null when the log is absent, empty, or all range episodes are done.
 */
export function getResumeEpisodeIndex(): number | null {
  const logPath = path.join(process.cwd(), "dataset_curator", "curation_log.csv");

  const curatedSet = new Set<number>();
  try {
    const content = fs.readFileSync(logPath, "utf-8");
    for (const line of content.split("\n")) {
      const firstCol = line.trim().split(",")[0];
      const n = parseInt(firstCol, 10);
      if (!isNaN(n)) curatedSet.add(n);
    }
  } catch {
    return null;
  }

  if (curatedSet.size === 0) return null;

  const parts = process.env.EPISODES_IDS?.split(",")
    .map((s) => parseInt(s.trim(), 10))
    .filter((n) => !isNaN(n));

  if (parts && parts.length >= 2) {
    const lo = Math.min(parts[0], parts[1]);
    const hi = Math.max(parts[0], parts[1]);
    for (let i = lo; i <= hi; i++) {
      if (!curatedSet.has(i)) return i;
    }
    return null;
  }

  let maxCurated = -1;
  for (const n of curatedSet) {
    if (n > maxCurated) maxCurated = n;
  }
  return maxCurated >= 0 ? maxCurated + 1 : null;
}
