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
let _lastSyncKey = "";
let _lastSyncAt = 0;

export function syncEpisodeToCurator(
  episodeId: number,
  languageInstruction: string = "",
): void {
  if (typeof window === "undefined") return;
  const key = `${episodeId}\0${languageInstruction}`;
  const now = Date.now();
  if (key === _lastSyncKey && now - _lastSyncAt < 500) {
    vizDebug("syncEpisodeToCurator → skip duplicate within 500ms", episodeId);
    return;
  }
  _lastSyncKey = key;
  _lastSyncAt = now;
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

/** Poll bridge: curator Save → advance; curator "Sync from visualizer" → resync; "Go to last saved" → navigateTo. */
export async function pollCuratorBridge(): Promise<{
  advance: boolean;
  resync: boolean;
  navigateTo: number | null;
}> {
  const base = getCuratorBridgeBase();
  try {
    const r = await fetch(`${base}/poll`, { cache: "no-store", mode: "cors" });
    if (!r.ok) return { advance: false, resync: false, navigateTo: null };
    const text = await r.text();
    if (!text.trim()) return { advance: false, resync: false, navigateTo: null };
    let j: { advance?: boolean; resync?: boolean; navigateTo?: number | null };
    try {
      j = JSON.parse(text) as { advance?: boolean; resync?: boolean; navigateTo?: number | null };
    } catch {
      return { advance: false, resync: false, navigateTo: null };
    }
    const advance = Boolean(j.advance);
    const resync = Boolean(j.resync);
    const navigateTo = typeof j.navigateTo === "number" ? j.navigateTo : null;
    if (advance) vizDebug("poll → advance=true");
    if (resync) vizDebug("poll → resync=true");
    if (navigateTo !== null) vizDebug("poll → navigateTo=", navigateTo);
    return { advance, resync, navigateTo };
  } catch {
    return { advance: false, resync: false, navigateTo: null };
  }
}

/** Returns true once after the user saves a row in the curator (advances visualizer). */
export async function pollCuratorAdvance(): Promise<boolean> {
  return (await pollCuratorBridge()).advance;
}
