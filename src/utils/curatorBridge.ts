/**
 * Local HTTP bridge to the PyQt dataset curator (dataset_curator/main.py).
 * Default port 18765; override with NEXT_PUBLIC_CURATOR_BRIDGE_URL.
 */

const DEFAULT_BASE = "http://127.0.0.1:18765";

export function getCuratorBridgeBase(): string {
  return process.env.NEXT_PUBLIC_CURATOR_BRIDGE_URL ?? DEFAULT_BASE;
}

/** Push episode id and language instruction (task) for the curator UI. */
export function syncEpisodeToCurator(
  episodeId: number,
  languageInstruction: string = "",
): void {
  const base = getCuratorBridgeBase();
  void fetch(`${base}/sync`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ episodeId, languageInstruction }),
  }).catch(() => {
    /* Curator not running */
  });
}

/** Returns true once after the user saves a row in the curator (advances visualizer). */
export async function pollCuratorAdvance(): Promise<boolean> {
  const base = getCuratorBridgeBase();
  try {
    const r = await fetch(`${base}/poll`);
    if (!r.ok) return false;
    const j = (await r.json()) as { advance?: boolean };
    return Boolean(j.advance);
  } catch {
    return false;
  }
}
