/**
 * Utility functions for checking dataset version compatibility
 */

import { getDatasetHostingConfig } from "@/utils/datasetEnv";
import { parseJsonResponse } from "@/utils/jsonResponse";

function datasetBaseUrl(): string {
  return getDatasetHostingConfig().baseUrl;
}

/**
 * Get HuggingFace token from environment or cache
 */
function getHFToken(): string | undefined {
  // First try environment variable
  if (process.env.HF_TOKEN) {
    return process.env.HF_TOKEN;
  }
  
  // Fallback to reading from HuggingFace cache (server-side only)
  // Check if we're in a Node.js environment
  if (typeof window === 'undefined') {
    try {
      // Dynamic require to avoid webpack bundling issues
      const fs = require('fs');
      const os = require('os');
      const path = require('path');
      
      const tokenPath = path.join(os.homedir(), '.cache', 'huggingface', 'token');
      const token = fs.readFileSync(tokenPath, 'utf-8').trim();
      return token;
    } catch {
      // Token file doesn't exist or can't be read
      return undefined;
    }
  }
  
  return undefined;
}

/**
 * Get authorization headers for HuggingFace API calls
 */
export function getAuthHeaders(): HeadersInit {
  const headers: HeadersInit = {};
  const token = getHFToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

/**
 * Dataset information structure from info.json
 */
interface DatasetInfo {
  codebase_version: string;
  robot_type: string | null;
  total_episodes: number;
  total_frames: number;
  total_tasks: number;
  chunks_size: number;
  data_files_size_in_mb: number;
  video_files_size_in_mb: number;
  fps: number;
  splits: Record<string, string>;
  data_path: string;
  video_path: string;
  features: Record<string, any>;
}

function isTransientNetworkError(err: unknown): boolean {
  if (!(err instanceof Error)) return false;
  const code = (err as any).cause?.code ?? (err as any).code ?? "";
  // EAI_AGAIN = DNS temporary failure, ECONNRESET / ECONNREFUSED = TCP issues
  return ["EAI_AGAIN", "ECONNRESET", "ECONNREFUSED", "ETIMEDOUT"].includes(code)
    || err.name === "AbortError";
}

/**
 * Fetches dataset information from the main revision.
 * Retries up to 3 times on transient network errors (DNS, TCP, timeout).
 */
export async function getDatasetInfo(repoId: string): Promise<DatasetInfo> {
  const testUrl = `${datasetBaseUrl()}/${repoId}/resolve/main/meta/info.json`;
  const MAX_ATTEMPTS = 3;

  let lastError: unknown;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 12000);

      const response = await fetch(testUrl, {
        method: "GET",
        cache: "no-store",
        signal: controller.signal,
        headers: getAuthHeaders(),
      });

      clearTimeout(timeoutId);

      if (!response.ok) {
        throw new Error(`Failed to fetch dataset info: ${response.status}`);
      }

      const data = await parseJsonResponse<DatasetInfo>(response);

      if (!data.features) {
        throw new Error("Dataset info.json does not have the expected features structure");
      }

      return data as DatasetInfo;
    } catch (error) {
      lastError = error;
      if (isTransientNetworkError(error) && attempt < MAX_ATTEMPTS) {
        const delay = attempt * 1500; // 1.5s, 3s
        console.warn(`[versionUtils] Transient network error (attempt ${attempt}/${MAX_ATTEMPTS}), retrying in ${delay}ms…`, (error as Error).message);
        await new Promise((r) => setTimeout(r, delay));
        continue;
      }
      break;
    }
  }

  if (lastError instanceof Error) throw lastError;
  throw new Error(
    `Dataset ${repoId} is not compatible with this visualizer. ` +
    "Failed to read dataset information from the main revision."
  );
}


/**
 * Gets the dataset version by reading the codebase_version from the main revision's info.json
 */
export async function getDatasetVersion(repoId: string): Promise<string> {
  try {
    const datasetInfo = await getDatasetInfo(repoId);
    
    // Extract codebase_version
    const codebaseVersion = datasetInfo.codebase_version;
    if (!codebaseVersion) {
      throw new Error("Dataset info.json does not contain codebase_version");
    }
    
    // Validate that it's a supported version
    const supportedVersions = ["v3.0", "v2.1", "v2.0"];
    if (!supportedVersions.includes(codebaseVersion)) {
      throw new Error(
        `Dataset ${repoId} has codebase version ${codebaseVersion}, which is not supported. ` +
        "This tool only works with dataset versions 3.0, 2.1, or 2.0. " +
        "Please use a compatible dataset version."
      );
    }
    
    return codebaseVersion;
  } catch (error) {
    if (error instanceof Error) {
      throw error;
    }
    throw new Error(
      `Dataset ${repoId} is not compatible with this visualizer. ` +
      "Failed to read dataset information from the main revision."
    );
  }
}

export function buildVersionedUrl(repoId: string, version: string, path: string): string {
  return `${datasetBaseUrl()}/${repoId}/resolve/main/${path}`;
}

