import { NextRequest, NextResponse } from "next/server";
import fs from "fs";
import os from "os";
import path from "path";
import crypto from "crypto";
import { Readable } from "stream";

function isTransientNetworkError(err: unknown): boolean {
  if (!(err instanceof Error)) return false;
  const code = (err as any).cause?.code ?? (err as any).code ?? "";
  return ["EAI_AGAIN", "ECONNRESET", "ECONNREFUSED", "ETIMEDOUT"].includes(code)
    || err.name === "AbortError";
}

async function fetchWithRetry(
  url: string,
  options: RequestInit,
  maxAttempts: number,
  delayMs: (attempt: number) => number,
): Promise<Response> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await fetch(url, options);
    } catch (err) {
      lastError = err;
      if (isTransientNetworkError(err) && attempt < maxAttempts) {
        const delay = delayMs(attempt);
        console.warn(`[VideoProxy] Transient DNS error (attempt ${attempt}/${maxAttempts}), retrying in ${delay}ms…`);
        await new Promise((r) => setTimeout(r, delay));
        continue;
      }
      throw err;
    }
  }
  throw lastError;
}

// Local disk cache — persists across requests and server restarts.
const VIDEO_CACHE_DIR = path.join(os.homedir(), ".cache", "lerobot-dataset-visualizer", "videos");
const completedCache = new Map<string, string>();      // videoUrl → local file path
const inProgressDownloads = new Set<string>();         // urls currently being downloaded

// 4 cameras per episode — allow all to download concurrently.
const MAX_CONCURRENT_DOWNLOADS = 4;
const MAX_QUEUED_DOWNLOADS = 8;
let _activeDownloads = 0;
const _downloadQueue: Array<() => void> = [];

function acquireDownloadSlot(): Promise<void> | null {
  if (_activeDownloads < MAX_CONCURRENT_DOWNLOADS) {
    _activeDownloads++;
    return Promise.resolve();
  }
  if (_downloadQueue.length >= MAX_QUEUED_DOWNLOADS) {
    return null; // queue full — caller should skip this download
  }
  return new Promise((resolve) => {
    _downloadQueue.push(resolve); // slot transfer: counter stays the same
  });
}

function releaseDownloadSlot(): void {
  const next = _downloadQueue.shift();
  if (next) { next(); } else { _activeDownloads--; }
}

function getCachePath(videoUrl: string): string {
  const hash = crypto.createHash("md5").update(videoUrl).digest("hex");
  return path.join(VIDEO_CACHE_DIR, hash + ".mp4");
}

function getLocalCachedFile(videoUrl: string): string | null {
  if (completedCache.has(videoUrl)) return completedCache.get(videoUrl)!;
  const filePath = getCachePath(videoUrl);
  if (fs.existsSync(filePath)) {
    completedCache.set(videoUrl, filePath);
    return filePath;
  }
  return null;
}

// Downloads the full video file in the background so future range requests
// are served from disk instantly. Does NOT block incoming requests.
function startBackgroundDownload(videoUrl: string, token: string): void {
  if (inProgressDownloads.has(videoUrl) || completedCache.has(videoUrl)) return;
  const filePath = getCachePath(videoUrl);
  if (fs.existsSync(filePath)) {
    completedCache.set(videoUrl, filePath);
    return;
  }

  inProgressDownloads.add(videoUrl);
  const shortName = videoUrl.split("/").pop() ?? "video";
  const tmpPath = filePath + ".tmp";

  (async () => {
    // Brief pause so the initial range request gets a head start before
    // a full-file download competes for the same connection.
    await new Promise((r) => setTimeout(r, 500));

    // Another request may have cached the file during the wait.
    if (getLocalCachedFile(videoUrl)) {
      inProgressDownloads.delete(videoUrl);
      return;
    }

    const slot = acquireDownloadSlot();
    if (slot === null) {
      inProgressDownloads.delete(videoUrl);
      console.log(`[VideoCache] Queue full, skipping background download for ${shortName}`);
      return;
    }
    await slot;
    try {
      fs.mkdirSync(VIDEO_CACHE_DIR, { recursive: true });
      console.log(`[VideoCache] Downloading ${shortName} … (slot ${_activeDownloads}/${MAX_CONCURRENT_DOWNLOADS})`);
      const response = await fetchWithRetry(
        videoUrl,
        { headers: { Authorization: `Bearer ${token}` } },
        4,
        (attempt) => attempt * 1500, // 1.5s, 3s, 4.5s
      );
      if (!response.ok || !response.body) {
        throw new Error(`HuggingFace returned ${response.status}`);
      }

      const fileStream = fs.createWriteStream(tmpPath);
      const reader = response.body.getReader();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        fileStream.write(value);
      }
      await new Promise<void>((resolve, reject) => {
        fileStream.end();
        fileStream.on("finish", resolve);
        fileStream.on("error", reject);
      });

      fs.renameSync(tmpPath, filePath);
      completedCache.set(videoUrl, filePath);
      const sizeMB = (fs.statSync(filePath).size / 1024 / 1024).toFixed(1);
      console.log(`[VideoCache] ✓ ${shortName} cached (${sizeMB} MB) — disk serves from now on`);
    } catch (error) {
      console.error(`[VideoCache] Download failed for ${shortName}:`, error);
      try { fs.unlinkSync(tmpPath); } catch { /* ignore */ }
    } finally {
      releaseDownloadSlot();
      inProgressDownloads.delete(videoUrl);
    }
  })();
}

function serveLocalFile(filePath: string, rangeHeader: string | null): NextResponse {
  const fileSize = fs.statSync(filePath).size;

  if (rangeHeader) {
    const match = rangeHeader.match(/bytes=(\d+)-(\d*)/);
    if (match) {
      const start = parseInt(match[1], 10);
      const end = match[2] ? parseInt(match[2], 10) : fileSize - 1;
      const chunkSize = end - start + 1;
      const nodeStream = fs.createReadStream(filePath, { start, end });
      return new NextResponse(Readable.toWeb(nodeStream) as ReadableStream, {
        status: 206,
        headers: {
          "Content-Type": "video/mp4",
          "Content-Range": `bytes ${start}-${end}/${fileSize}`,
          "Content-Length": String(chunkSize),
          "Accept-Ranges": "bytes",
          "Cache-Control": "public, max-age=3600",
          "Access-Control-Allow-Origin": "*",
        },
      });
    }
  }

  const nodeStream = fs.createReadStream(filePath);
  return new NextResponse(Readable.toWeb(nodeStream) as ReadableStream, {
    status: 200,
    headers: {
      "Content-Type": "video/mp4",
      "Content-Length": String(fileSize),
      "Accept-Ranges": "bytes",
      "Cache-Control": "public, max-age=3600",
      "Access-Control-Allow-Origin": "*",
    },
  });
}

function getHFToken(): string | undefined {
  if (process.env.HF_TOKEN) return process.env.HF_TOKEN;
  try {
    const tokenPath = path.join(os.homedir(), ".cache", "huggingface", "token");
    return fs.readFileSync(tokenPath, "utf-8").trim();
  } catch {
    return undefined;
  }
}

export async function GET(request: NextRequest) {
  const videoUrl = request.nextUrl.searchParams.get("url");

  if (!videoUrl) {
    return NextResponse.json({ error: "Missing url parameter" }, { status: 400 });
  }
  if (!videoUrl.startsWith("https://huggingface.co/")) {
    return NextResponse.json({ error: "Only HuggingFace URLs are allowed" }, { status: 403 });
  }

  const token = getHFToken();
  if (!token) {
    return NextResponse.json({ error: "No HuggingFace token available" }, { status: 401 });
  }

  const rangeHeader = request.headers.get("range");
  const fileName = videoUrl.split("/").slice(-3).join("/"); // camera/chunk-000/file-003.mp4
  const rangeStr = rangeHeader ?? "full";

  // Fast path: already on disk
  const localFile = getLocalCachedFile(videoUrl);
  if (localFile) {
    console.log(`[VideoProxy] DISK  ${fileName}  ${rangeStr}`);
    return serveLocalFile(localFile, rangeHeader);
  }

  // Kick off background full-file download (non-blocking — does not delay this response)
  startBackgroundDownload(videoUrl, token);
  const bgStatus = inProgressDownloads.has(videoUrl) ? "bg:downloading" : "bg:queued/dropped";
  console.log(`[VideoProxy] LIVE  ${fileName}  ${rangeStr}  (${bgStatus})`);


  // Proxy this specific range request to HuggingFace immediately
  try {
    const upstreamHeaders: HeadersInit = { Authorization: `Bearer ${token}` };
    if (rangeHeader) upstreamHeaders["Range"] = rangeHeader;

    const response = await fetchWithRetry(
      videoUrl,
      { headers: upstreamHeaders },
      5,
      (attempt) => attempt * 800, // 800ms, 1.6s, 2.4s, 3.2s
    );

    if (!response.ok) {
      return NextResponse.json(
        { error: `Upstream error: ${response.status} ${response.statusText}` },
        { status: response.status }
      );
    }

    const body = response.body;
    if (!body) {
      return NextResponse.json({ error: "No response body from upstream" }, { status: 502 });
    }

    const responseHeaders = new Headers();
    for (const h of ["content-type", "content-length", "content-range", "accept-ranges"]) {
      const v = response.headers.get(h);
      if (v) responseHeaders.set(h, v);
    }
    responseHeaders.set("Access-Control-Allow-Origin", "*");
    responseHeaders.set("Cache-Control", "public, max-age=3600");

    return new NextResponse(body, { status: response.status, headers: responseHeaders });
  } catch (error) {
    console.error("Video proxy error:", error);
    return NextResponse.json({ error: "Failed to fetch video" }, { status: 500 });
  }
}

export async function DELETE(request: NextRequest) {
  const videoUrl = request.nextUrl.searchParams.get("url");
  if (!videoUrl) {
    return NextResponse.json({ error: "Missing url parameter" }, { status: 400 });
  }
  if (!videoUrl.startsWith("https://huggingface.co/")) {
    return NextResponse.json({ error: "Only HuggingFace URLs are allowed" }, { status: 403 });
  }

  const filePath = getCachePath(videoUrl);
  completedCache.delete(videoUrl);
  try {
    if (fs.existsSync(filePath)) {
      fs.unlinkSync(filePath);
      console.log(`[VideoCache] Deleted ${videoUrl.split("/").pop()}`);
    }
  } catch (e) {
    console.warn(`[VideoCache] Could not delete cached file:`, e);
  }
  return new NextResponse(null, { status: 204 });
}

export async function OPTIONS() {
  return new NextResponse(null, {
    status: 200,
    headers: {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, DELETE, OPTIONS",
      "Access-Control-Allow-Headers": "Range",
    },
  });
}
