/**
 * Hosting base URL + optional default repo when DATASET_URL is a full HF dataset URL.
 */

export type DatasetHostingConfig = {
  baseUrl: string;
  defaultRepoId: string | null;
};

let cached: DatasetHostingConfig | null = null;

function computeConfig(): DatasetHostingConfig {
  const fallbackBase = "https://huggingface.co/datasets";
  const raw = process.env.DATASET_URL?.trim();
  if (!raw) {
    return { baseUrl: fallbackBase, defaultRepoId: null };
  }

  const hfPage =
    /^https?:\/\/(?:www\.)?huggingface\.co\/datasets\/([^/]+)\/([^/?#]+)\/?(?:[#?].*)?$/i.exec(
      raw,
    );
  if (hfPage) {
    return {
      baseUrl: fallbackBase,
      defaultRepoId: `${hfPage[1]}/${hfPage[2]}`,
    };
  }

  return {
    baseUrl: raw.replace(/\/$/, ""),
    defaultRepoId: null,
  };
}

export function getDatasetHostingConfig(): DatasetHostingConfig {
  if (!cached) {
    cached = computeConfig();
  }
  return cached;
}
