/**
 * Parse JSON from a fetch Response without throwing SyntaxError on empty bodies.
 */

export async function parseJsonResponse<T>(res: Response): Promise<T> {
  const text = await res.text();
  const trimmed = text.trim();
  if (!trimmed) {
    throw new Error(
      `Empty JSON body (${res.status} ${res.statusText})`,
    );
  }
  try {
    return JSON.parse(trimmed) as T;
  } catch (e) {
    const hint = trimmed.length > 220 ? `${trimmed.slice(0, 220)}…` : trimmed;
    throw new Error(
      `Invalid JSON (${res.status}): ${e instanceof Error ? e.message : String(e)} — ${hint}`,
    );
  }
}
