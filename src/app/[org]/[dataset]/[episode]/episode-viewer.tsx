"use client";

import { useState, useEffect, useRef, useCallback, useMemo } from "react";

// Module-level timing — persists across React remounts within the same browser session.
let _navStartMs = 0;
let _navFromEpisode: number | null = null;
function navLog(msg: string, ...args: any[]) {
  console.log(`%c[Nav] ${msg}`, "color:#5b9bd5;font-weight:bold", ...args);
}
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { postParentMessageWithParams } from "@/utils/postParentMessage";
import { SimpleVideosPlayer } from "@/components/simple-videos-player";
import DataRecharts from "@/components/data-recharts";
import PlaybackBar from "@/components/playback-bar";
import { TimeProvider, usePlayback, useTime } from "@/context/time-context";
import Sidebar from "@/components/side-nav";
import Loading from "@/components/loading-component";
import { getAdjacentEpisodesVideoInfo } from "./fetch-data";
import {
  curatorBridgeLog,
  pollCuratorBridge,
  syncEpisodeToCurator,
} from "@/utils/curatorBridge";
import { getProxiedVideoUrl } from "@/utils/videoProxy";
import {
  nextVisibleEpisodeAfter,
  prevVisibleEpisodeBefore,
  sortedVisibleEpisodeIds,
} from "@/utils/episodeFilter";

export default function EpisodeViewer({
  data,
  error,
  org,
  dataset,
}: {
  data?: any;
  error?: string;
  org?: string;
  dataset?: string;
}) {
  if (error) {
    return (
      <div className="flex h-screen items-center justify-center bg-slate-950 text-red-400">
        <div className="max-w-xl p-8 rounded bg-slate-900 border border-red-500 shadow-lg">
          <h2 className="text-2xl font-bold mb-4">Something went wrong</h2>
          <p className="text-lg font-mono whitespace-pre-wrap mb-4">{error}</p>
        </div>
      </div>
    );
  }
  return (
    <TimeProvider
      key={`${org}/${dataset}/episode_${data.episodeId}`}
      duration={data.duration}
    >
      <EpisodeViewerInner data={data} org={org} dataset={dataset} />
    </TimeProvider>
  );
}

function EpisodeViewerInner({
  data,
  org,
  dataset,
}: {
  data: any;
  org?: string;
  dataset?: string;
}) {
  const {
    datasetInfo,
    episodeId,
    videosInfo,
    chartDataGroups,
    episodes,
    task,
  } = data;

  const sortedEpisodes = useMemo(
    () => sortedVisibleEpisodeIds(episodes),
    [episodes],
  );

  const episodesRef = useRef(sortedEpisodes);
  const episodeIdRef = useRef(episodeId);
  const taskRef = useRef("");
  episodesRef.current = sortedEpisodes;
  episodeIdRef.current = episodeId;
  taskRef.current = typeof task === "string" ? task : "";

  const [videosReady, setVideosReady] = useState(!videosInfo.length);
  const [chartsReady, setChartsReady] = useState(false);
  const [preloadVideos, setPreloadVideos] = useState<string[]>([]);
  const isLoading = !videosReady || !chartsReady;

  // Tracks the HuggingFace URLs of the previous episode's videos for sliding-window cleanup.
  const prevVideoHFUrlsRef = useRef<string[]>([]);

  // Keep callbacks stable so children don't re-render / re-init on every time tick.
  const handleVideosReady = useCallback(() => {
    setVideosReady(true);
  }, [setVideosReady]);

  const handleChartsReady = useCallback(() => {
    setChartsReady(true);
  }, [setChartsReady]);

  // Log mount timing (server render + hydration elapsed) and which video files are used.
  useEffect(() => {
    const elapsed = _navStartMs > 0 ? `${(performance.now() - _navStartMs).toFixed(0)}ms` : "—";
    const files = videosInfo
      .map((v: any) => (v.url as string).split("/").slice(-3).join("/"))
      .join(", ");
    navLog(`← episode_${episodeId}  server+hydrate: ${elapsed}  videos: [${files}]`);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Log when the first video frame is ready (overlay disappears).
  useEffect(() => {
    if (!videosReady) return;
    const elapsed = _navStartMs > 0 ? `${(performance.now() - _navStartMs).toFixed(0)}ms` : "—";
    navLog(`✓ episode_${episodeId}  videos ready — total from nav: ${elapsed}`);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [videosReady]);

  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // State
  // Use context for time sync
  const { currentTime, setCurrentTime } = useTime();
  const { setIsPlaying, isPlaying } = usePlayback();

  // Pagination state
  const pageSize = 100;
  const [currentPage, setCurrentPage] = useState(1);
  const totalPages = Math.ceil(sortedEpisodes.length / pageSize);
  const paginatedEpisodes = sortedEpisodes.slice(
    (currentPage - 1) * pageSize,
    currentPage * pageSize,
  );

  // Preload next episodes' videos + sliding-window cleanup of old chunk files.
  useEffect(() => {
    if (!org || !dataset) return;
    setPreloadVideos([]);

    const currentHFUrls = videosInfo.map((v: any) => v.url as string);

    const preloadAdjacent = async () => {
      try {
        // Radius 1: prefetch ONLY the immediate next episode. With one slow HF pipe and
        // low download concurrency, prefetching further ahead would steal bandwidth from
        // the file you're currently waiting on. The next episode's files download from
        // leftover capacity while you curate; deeper look-ahead isn't worth the contention.
        const adjacent = await getAdjacentEpisodesVideoInfo(org, dataset, episodeId, 1);
        const currentIdx = sortedEpisodes.indexOf(Number(episodeId));
        const nextEpisodes = adjacent.filter(
          ({ episodeId: id }) => sortedEpisodes.indexOf(Number(id)) > currentIdx,
        );

        const preloadHFUrls = nextEpisodes.flatMap(({ videosInfo: vInfo }) =>
          vInfo.map((v: any) => v.url as string),
        );
        const preloadProxiedUrls = nextEpisodes.flatMap(({ videosInfo: vInfo }) =>
          vInfo.map((v: any) => getProxiedVideoUrl(v.url)),
        );

        setPreloadVideos(preloadProxiedUrls);

        // Delete cached files that are no longer in the current or next chunk.
        const keepUrls = new Set([...currentHFUrls, ...preloadHFUrls]);
        for (const url of prevVideoHFUrlsRef.current) {
          if (!keepUrls.has(url) && url.startsWith("https://huggingface.co/")) {
            fetch(`/api/video-proxy?url=${encodeURIComponent(url)}`, { method: "DELETE" }).catch(() => {});
          }
        }
      } catch {
        // Skip preloading on error
      } finally {
        prevVideoHFUrlsRef.current = currentHFUrls;
      }
    };

    preloadAdjacent();
  }, [org, dataset, episodeId, sortedEpisodes]);

  // Initialize based on URL time parameter
  useEffect(() => {
    const timeParam = searchParams.get("t");
    if (timeParam) {
      const timeValue = parseFloat(timeParam);
      if (!isNaN(timeValue)) {
        setCurrentTime(timeValue);
      }
    }
  }, []);

  // sync with parent window hf.co/spaces
  useEffect(() => {
    postParentMessageWithParams((params: URLSearchParams) => {
      params.set("path", window.location.pathname + window.location.search);
    });
  }, []);

  // PyQt curator: episode index + language instruction (URL wins so navigation updates immediately)
  useEffect(() => {
    const fromPath = pathname.match(/\/episode_(\d+)/);
    const id = fromPath
      ? Number(fromPath[1])
      : Number(episodeId);
    if (!Number.isFinite(id)) return;
    syncEpisodeToCurator(id, typeof task === "string" ? task : "");
  }, [pathname, episodeId, task]);

  // After curator Save, jump to next episode. Snapshot episode + episode list BEFORE await
  // poll (refs can change during await). Next index from sorted visible list only — do not
  // use server nextEpisodeId here (it can desync from RSC/ref timing and skip episodes).
  useEffect(() => {
    let cancelled = false;
    let timeoutId: number | undefined;

    const schedule = () => {
      // Poll the curator bridge often so a Save is detected (and navigation kicks off)
      // within ~120ms instead of up to 350ms. The poll hits the Python bridge directly,
      // so it's cheap and never queues behind the Next server's video I/O.
      timeoutId = window.setTimeout(run, 120);
    };

    function currentEpisodeFromBrowserPath(): number | null {
      const m = window.location.pathname.match(/\/episode_(\d+)/);
      if (!m) return null;
      const n = Number(m[1]);
      return Number.isFinite(n) ? n : null;
    }

    async function run() {
      if (cancelled) return;
      try {
        const epSnapBefore = Number(episodeIdRef.current);
        const episodesSnap = episodesRef.current.slice();

        const { advance, resync, navigateTo } = await pollCuratorBridge();
        if (cancelled) return;

        if (navigateTo !== null && Number.isFinite(navigateTo)) {
          const path =
            org && dataset
              ? `/${org}/${dataset}/episode_${navigateTo}`
              : `./episode_${navigateTo}`;
          curatorBridgeLog("navigateTo → router.push", { navigateTo, path });
          _navStartMs = performance.now();
          navLog(`→ episode_${navigateTo}  (curator navigateTo)`);
          router.push(path);
          return;
        }

        if (resync) {
          const pathEp = currentEpisodeFromBrowserPath();
          const id =
            pathEp != null && Number.isFinite(pathEp)
              ? pathEp
              : Number(episodeIdRef.current);
          if (Number.isFinite(id)) {
            syncEpisodeToCurator(id, taskRef.current);
          }
        }

        if (cancelled) return;

        if (!advance) return;

        if (!Number.isFinite(epSnapBefore)) return;

        const curNow = Number(episodeIdRef.current);
        if (curNow !== epSnapBefore) return;

        const nextId = nextVisibleEpisodeAfter(epSnapBefore, episodesSnap);

        if (nextId !== null) {
          const pathEp = currentEpisodeFromBrowserPath();
          if (pathEp != null && pathEp === nextId) {
            curatorBridgeLog("advance → skip push, URL already at next episode", {
              nextId,
            });
            return;
          }
          const path =
            org && dataset
              ? `/${org}/${dataset}/episode_${nextId}`
              : `./episode_${nextId}`;
          curatorBridgeLog("advance → router.push", {
            cur: epSnapBefore,
            nextId,
            path,
            pathAtPoll: window.location.pathname,
          });
          _navStartMs = performance.now();
          _navFromEpisode = epSnapBefore;
          navLog(`→ episode_${nextId}  from: ${_navFromEpisode}  (curator advance)`);
          router.push(path);
        } else {
          console.warn(
            "[CuratorBridge:viz] advance=true but cannot go to next episode",
            {
              cur: epSnapBefore,
              episodesLen: episodesSnap?.length,
              pathname: window.location.pathname,
            },
          );
        }
      } finally {
        if (!cancelled) schedule();
      }
    }

    schedule();
    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [router, org, dataset]);

  // Initialize based on URL time parameter
  useEffect(() => {
    // Initialize page based on current episode
    const episodeIndex = sortedEpisodes.indexOf(Number(episodeId));
    if (episodeIndex !== -1) {
      setCurrentPage(Math.floor(episodeIndex / pageSize) + 1);
    }

    // Add keyboard event listener
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [sortedEpisodes, episodeId, pageSize, searchParams, org, dataset, router]);

  // Only update URL ?t= param when the integer second changes
  const lastUrlSecondRef = useRef<number>(-1);
  useEffect(() => {
    if (isPlaying) return;
    const currentSec = Math.floor(currentTime);
    if (currentTime > 0 && lastUrlSecondRef.current !== currentSec) {
      lastUrlSecondRef.current = currentSec;
      const newParams = new URLSearchParams(searchParams.toString());
      newParams.set("t", currentSec.toString());
      // Replace state instead of pushing to avoid navigation stack bloat
      window.history.replaceState(
        {},
        "",
        `${window.location.pathname}?${newParams.toString()}`,
      );
      postParentMessageWithParams((params: URLSearchParams) => {
        params.set("path", window.location.pathname + window.location.search);
      });
    }
  }, [isPlaying, currentTime, searchParams]);

  // Handle keyboard shortcuts
  const handleKeyDown = (e: KeyboardEvent) => {
    const { key } = e;

    if (key === " ") {
      e.preventDefault();
      setIsPlaying((prev: boolean) => !prev);
    } else if (key === "ArrowDown" || key === "ArrowUp") {
      e.preventDefault();
      const cur = Number(episodeId);
      const nextId =
        key === "ArrowDown"
          ? nextVisibleEpisodeAfter(cur, sortedEpisodes)
          : prevVisibleEpisodeBefore(cur, sortedEpisodes);
      if (nextId !== null) {
        const path =
          org && dataset
            ? `/${org}/${dataset}/episode_${nextId}`
            : `./episode_${nextId}`;
        _navStartMs = performance.now();
        _navFromEpisode = Number(episodeId);
        navLog(`→ episode_${nextId}  from: ${_navFromEpisode}  (keyboard)`);
        router.push(path);
      }
    }
  };

  // Pagination functions
  const nextPage = () => {
    if (currentPage < totalPages) {
      setCurrentPage((prev) => prev + 1);
    }
  };

  const prevPage = () => {
    if (currentPage > 1) {
      setCurrentPage((prev) => prev - 1);
    }
  };

  return (
    <div className="flex h-screen max-h-screen bg-slate-950 text-gray-200">
      {/* Live "what is the switch waiting for" indicator */}
      <VideoCacheStatus
        videosInfo={videosInfo}
        videosReady={videosReady}
        chartsReady={chartsReady}
      />

      {/* Sidebar */}
      <Sidebar
        datasetInfo={datasetInfo}
        paginatedEpisodes={paginatedEpisodes}
        episodeId={episodeId}
        totalPages={totalPages}
        currentPage={currentPage}
        prevPage={prevPage}
        nextPage={nextPage}
      />

      {/* Content */}
      <div
        className={`flex max-h-screen flex-col gap-4 p-4 md:flex-1 relative ${isLoading ? "overflow-hidden" : "overflow-y-auto"}`}
      >
        {isLoading && <Loading />}

        <div className="flex items-center justify-start my-4">
          <a
            href="https://github.com/huggingface/lerobot"
            target="_blank"
            className="block"
          >
            <img
              src="https://github.com/huggingface/lerobot/raw/main/media/lerobot-logo-thumbnail.png"
              alt="LeRobot Logo"
              className="w-32"
            />
          </a>

          <div>
            <a
              href={`https://huggingface.co/datasets/${datasetInfo.repoId}`}
              target="_blank"
            >
              <p className="text-lg font-semibold">{datasetInfo.repoId}</p>
            </a>

            <p className="font-mono text-lg font-semibold">
              episode {episodeId}
            </p>
          </div>
        </div>

        {/* Videos */}
        {videosInfo.length && (
          <SimpleVideosPlayer
            videosInfo={videosInfo}
            onVideosReady={handleVideosReady}
          />
        )}

        {/* Language Instruction */}
        {task && (
          <div className="mb-6 p-4 bg-slate-800 rounded-lg border border-slate-600">
            <p className="text-slate-300">
              <span className="font-semibold text-slate-100">Language Instruction:</span>
            </p>
            <div className="mt-2 text-slate-300">
              {task.split('\n').map((instruction: string, index: number) => (
                <p key={index} className="mb-1">
                  {instruction}
                </p>
              ))}
            </div>
          </div>
        )}

        {/* Graph */}
        <div className="mb-4">
          <DataRecharts
            data={chartDataGroups}
            onChartsReady={handleChartsReady}
          />

        </div>

        <PlaybackBar />
      </div>

      {/* Hidden video elements to warm the HTTP cache for next episodes */}
      {preloadVideos.map((url) => {
        const label = `[Preload] ${url.split("videos%2F").pop()?.split("%2F").slice(0, 2).join("/") ?? url.slice(-40)}`;
        const t0 = performance.now();
        const getBuffered = (v: HTMLVideoElement) =>
          v.buffered.length ? (v.buffered.end(v.buffered.length - 1) - v.buffered.start(0)).toFixed(2) : "0.00";
        return (
          <video
            key={url}
            src={url}
            // metadata only: warms the proxy disk cache with one request instead of
            // buffer-ahead flooding dozens of range requests for the NEXT episode, which
            // starved the CURRENT episode's cameras of the browser's ~6 connection slots.
            preload="metadata"
            muted
            playsInline
            style={{ position: "absolute", width: 0, height: 0, opacity: 0, pointerEvents: "none" }}
            onLoadStart={(e) => console.log(`${label} | loadstart`)}
            onProgress={(e) => {
              const v = e.currentTarget;
              console.log(`${label} | progress @ ${(performance.now() - t0).toFixed(0)}ms | buffered=${getBuffered(v)}s | networkState=${v.networkState}`);
            }}
            onCanPlayThrough={(e) => {
              const v = e.currentTarget;
              console.log(`${label} | canplaythrough @ ${(performance.now() - t0).toFixed(0)}ms | buffered=${getBuffered(v)}s`);
            }}
          />
        );
      })}
    </div>
  );
}

type CamStatus = {
  state: "cached" | "downloading" | "starting" | "cold" | "?";
  received?: number;
  total?: number;
  pct?: number;
};

// Live indicator: polls the proxy for each camera's cache state so the user can SEE
// exactly what a slow episode switch is waiting for (which 500MB file is still
// downloading from HuggingFace, and how far along). Auto-hides once everything is
// cached and the page is fully ready.
function VideoCacheStatus({
  videosInfo,
  videosReady,
  chartsReady,
}: {
  videosInfo: any[];
  videosReady: boolean;
  chartsReady: boolean;
}) {
  const [statuses, setStatuses] = useState<Record<string, CamStatus>>({});

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const entries = await Promise.all(
          videosInfo.map(async (v: any) => {
            try {
              const r = await fetch(
                `/api/video-proxy?status=1&url=${encodeURIComponent(v.url)}`,
                { cache: "no-store" },
              );
              return [v.filename, (await r.json()) as CamStatus] as const;
            } catch {
              return [v.filename, { state: "?" } as CamStatus] as const;
            }
          }),
        );
        if (!cancelled) setStatuses(Object.fromEntries(entries));
      } finally {
        if (!cancelled) timer = window.setTimeout(poll, 700);
      }
    };
    poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [videosInfo]);

  const allCached = videosInfo.every(
    (v: any) => statuses[v.filename]?.state === "cached",
  );
  if (allCached && videosReady && chartsReady) return null;

  const describe = (s?: CamStatus): string => {
    if (!s) return "…";
    switch (s.state) {
      case "cached":
        return "✓ cached";
      case "downloading":
        return `⬇ ${s.pct ?? 0}%  ${((s.received ?? 0) / 1e6).toFixed(0)}/${((s.total ?? 0) / 1e6).toFixed(0)} MB`;
      case "starting":
        return "⬇ starting…";
      case "cold":
        return "• not downloaded";
      default:
        return "…";
    }
  };
  const colorFor = (s?: CamStatus): string =>
    s?.state === "cached"
      ? "text-green-400"
      : s?.state === "downloading" || s?.state === "starting"
        ? "text-amber-300"
        : "text-slate-400";

  return (
    <div
      style={{ position: "fixed", top: 12, right: 12, zIndex: 9999 }}
      className="bg-black/90 border-2 border-amber-400 rounded-lg p-3 text-sm font-mono text-slate-100 shadow-2xl pointer-events-none min-w-[260px]"
    >
      <div className="font-bold mb-1 text-amber-300">⏳ Waiting on…</div>
      {videosInfo.map((v: any) => {
        const s = statuses[v.filename];
        const cam = String(v.filename).replace("observation.image.", "");
        return (
          <div key={v.filename} className={colorFor(s)}>
            {cam}: {describe(s)}
          </div>
        );
      })}
      <div className={chartsReady ? "text-green-400" : "text-amber-300"}>
        charts: {chartsReady ? "✓ ready" : "… computing"}
      </div>
    </div>
  );
}
