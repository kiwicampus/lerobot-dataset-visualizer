/**
 * Build the episode index list shown in the UI.
 *
 * EPISODES_IDS (comma-separated):
 * - Two numbers → inclusive range (e.g. 10,20 → 10,11,…,20).
 * - One number → that episode only.
 * - Three or more → explicit list (order kept, deduped, clipped to dataset).
 *
 * EPISODES (whitespace-separated): explicit indices, same clipping rules.
 * If neither is set, returns [0 .. totalEpisodes - 1].
 */

function normalizeAllowlist(raw: number[], totalEpisodes: number): number[] {
  const seen = new Set<number>();
  const out: number[] = [];
  for (const n of raw) {
    if (Number.isNaN(n) || n < 0 || n >= totalEpisodes || seen.has(n)) continue;
    seen.add(n);
    out.push(n);
  }
  return out;
}

/** First episode index for redirects (home / dataset root). */
export function initialEpisodeIndexFromEnv(): number {
  const fromRange = initialEpisodeFromEpisodesIdsEnv();
  if (fromRange !== null) return fromRange;
  const fromLegacy = process.env.EPISODES?.split(/\s+/)
    .map((s) => parseInt(s.trim(), 10))
    .find((n) => !Number.isNaN(n));
  if (fromLegacy !== undefined) return fromLegacy;
  return 0;
}

/** First episode index when EPISODES_IDS encodes range or single (no total_episodes yet). */
export function initialEpisodeFromEpisodesIdsEnv(): number | null {
  const parts = process.env.EPISODES_IDS?.split(/,/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (!parts?.length) return null;
  const nums = parts
    .map((s) => parseInt(s, 10))
    .filter((n) => !Number.isNaN(n));
  if (nums.length === 0) return null;
  if (nums.length === 1) return nums[0];
  if (nums.length === 2) return Math.min(nums[0], nums[1]);
  return nums[0];
}

export function buildVisibleEpisodesList(totalEpisodes: number): number[] {
  const fromIds = process.env.EPISODES_IDS?.split(/,/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (fromIds?.length) {
    const nums = fromIds
      .map((s) => parseInt(s, 10))
      .filter((n) => !Number.isNaN(n));

    if (nums.length === 0) {
      return [];
    }

    if (nums.length === 1) {
      return normalizeAllowlist(nums, totalEpisodes);
    }

    if (nums.length === 2) {
      let a = nums[0];
      let b = nums[1];
      if (a > b) {
        [a, b] = [b, a];
      }
      const range: number[] = [];
      for (let i = a; i <= b; i++) {
        range.push(i);
      }
      return normalizeAllowlist(range, totalEpisodes);
    }

    return normalizeAllowlist(nums, totalEpisodes);
  }

  const fromLegacy = process.env.EPISODES?.split(/\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (fromLegacy?.length) {
    return normalizeAllowlist(
      fromLegacy.map((s) => parseInt(s, 10)),
      totalEpisodes,
    );
  }

  return Array.from({ length: totalEpisodes }, (_, i) => i);
}

/** Dedupe + numeric sort. Navigation must not rely on raw array order (RSC/JSON can reorder). */
export function sortedVisibleEpisodeIds(
  visibleEpisodes: readonly unknown[],
): number[] {
  return [
    ...new Set(
      visibleEpisodes
        .map((e) => Number(e))
        .filter((n) => Number.isFinite(n)),
    ),
  ].sort((a, b) => a - b);
}

/** Next episode index in visible order (sorted), or null if none / not in list. */
export function nextVisibleEpisodeAfter(
  currentEpisodeId: number,
  visibleEpisodes: readonly unknown[],
): number | null {
  const sorted = sortedVisibleEpisodeIds(visibleEpisodes);
  const i = sorted.indexOf(currentEpisodeId);
  if (i < 0 || i >= sorted.length - 1) return null;
  return sorted[i + 1]!;
}

/** Previous episode index in visible order (sorted), or null. */
export function prevVisibleEpisodeBefore(
  currentEpisodeId: number,
  visibleEpisodes: readonly unknown[],
): number | null {
  const sorted = sortedVisibleEpisodeIds(visibleEpisodes);
  const i = sorted.indexOf(currentEpisodeId);
  if (i <= 0) return null;
  return sorted[i - 1]!;
}
