/**
 * Local HTTP bridge to the PyQt dataset curator (dataset_curator/main.py).
 * Default port 18765; override with NEXT_PUBLIC_CURATOR_BRIDGE_URL.
 */

const LOG_PREFIX = "[CuratorBridge:viz]";

function vizDebug(...args: unknown[]) {
  if (process.env.NEXT_PUBLIC_CURATOR_BRIDGE_DEBUG === "0") return;
  console.log(LOG_PREFIX, ...args);
}

/** For client components (e.g. episode viewer) when NEXT_PUBLIC_CURATOR_BRIDGE_DEBUG is set. */
export function curatorBridgeLog(...args: unknown[]) {
  vizDebug(...args);
}

const DEFAULT_BASE = "http://127.0.0.1:18765";

export function getCuratorBridgeBase(): string {
  const raw = process.env.NEXT_PUBLIC_CURATOR_BRIDGE_URL ?? DEFAULT_BASE;
  return raw.replace(/\/$/, "");
}

/** Push episode id and language instruction (task) for the curator UI. */
export function syncEpisodeToCurator(
  episodeId: number,
  languageInstruction: string = "",
): void {
  if (typeof window === "undefined") return;
  const base = getCuratorBridgeBase();
  const url = `${base}/sync`;
  const body = JSON.stringify({ episodeId, languageInstruction });
  vizDebug("syncEpisodeToCurator → POST", url, {
    episodeId,
    instructionLen: languageInstruction.length,
    origin: window.location.origin,
  });
  void fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
    mode: "cors",
    cache: "no-store",
    keepalive: true,
  })
    .then((r) => {
      vizDebug("sync POST response", r.status, r.ok ? "ok" : "failed");
      if (!r.ok) {
        console.warn(LOG_PREFIX, "sync POST not ok", r.status, url);
      }
    })
    .catch((err) => {
      console.warn(LOG_PREFIX, "sync POST fetch error (curator down / blocked?)", err, url);
    });
}

/** Returns true once after the user saves a row in the curator (advances visualizer). */
export async function pollCuratorAdvance(): Promise<boolean> {
  const base = getCuratorBridgeBase();
  try {
    const r = await fetch(`${base}/poll`, { cache: "no-store", mode: "cors" });
    if (!r.ok) return false;
    const text = await r.text();
    if (!text.trim()) return false;
    let j: { advance?: boolean };
    try {
      j = JSON.parse(text) as { advance?: boolean };
    } catch {
      return false;
    }
    const advance = Boolean(j.advance);
    if (advance) {
      vizDebug("poll → advance=true");
    }
    return advance;
  } catch {
    return false;
  }
}
