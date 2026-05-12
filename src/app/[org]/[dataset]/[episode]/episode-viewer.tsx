"use client";

import { useState, useEffect, useRef, useCallback } from "react";
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
  pollCuratorAdvance,
  syncEpisodeToCurator,
} from "@/utils/curatorBridge";
import { getProxiedVideoUrl } from "@/utils/videoProxy";

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
    <TimeProvider duration={data.duration}>
      <EpisodeViewerInner data={data} org={org} dataset={dataset} />
    </TimeProvider>
  );
}

function indexInEpisodeList(episodes: unknown[], episodeId: unknown): number {
  const cur = Number(episodeId);
  return episodes.findIndex((e) => Number(e) === cur);
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

  const episodesRef = useRef(episodes);
  const episodeIdRef = useRef(episodeId);
  episodesRef.current = episodes;
  episodeIdRef.current = episodeId;

  const [videosReady, setVideosReady] = useState(!videosInfo.length);
  const [chartsReady, setChartsReady] = useState(false);
  const [preloadVideos, setPreloadVideos] = useState<string[]>([]);
  const isLoading = !videosReady || !chartsReady;

  // Keep callbacks stable so children don't re-render / re-init on every time tick.
  const handleVideosReady = useCallback(() => {
    setVideosReady(true);
  }, [setVideosReady]);

  const handleChartsReady = useCallback(() => {
    setChartsReady(true);
  }, [setChartsReady]);

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
  const totalPages = Math.ceil(episodes.length / pageSize);
  const paginatedEpisodes = episodes.slice(
    (currentPage - 1) * pageSize,
    currentPage * pageSize,
  );

  // Preload next episodes' videos so high-speed playback works immediately after navigation
  useEffect(() => {
    if (!org || !dataset) return;
    setPreloadVideos([]);

    const preloadAdjacent = async () => {
      try {
        const adjacent = await getAdjacentEpisodesVideoInfo(org, dataset, episodeId, 2);
        const currentIdx = indexInEpisodeList(episodes, episodeId);
        const urls = adjacent
          .filter(({ episodeId: id }) => indexInEpisodeList(episodes, id) > currentIdx)
          .flatMap(({ videosInfo: vInfo }) =>
            vInfo.map((v: any) => getProxiedVideoUrl(v.url))
          );
        setPreloadVideos(urls);
      } catch {
        // Skip preloading on error
      }
    };

    preloadAdjacent();
  }, [org, dataset, episodeId]);

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

  // After curator Save, jump to next episode in dataset order. Serial polls (no overlapping
  // setInterval + async) so one GET /poll consumes advance=true reliably; URL wins for "current".
  useEffect(() => {
    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout>;

    const schedule = () => {
      timeoutId = window.setTimeout(run, 350);
    };

    async function run() {
      if (cancelled) return;
      try {
        const go = await pollCuratorAdvance();
        if (!go) return;

        const eps = episodesRef.current;
        const fromUrl = pathname.match(/\/episode_(\d+)/);
        const cur = fromUrl
          ? Number(fromUrl[1])
          : Number(episodeIdRef.current);
        const idx = indexInEpisodeList(eps, cur);

        if (idx >= 0 && idx < eps.length - 1) {
          const nextId = eps[idx + 1];
          const path =
            org && dataset
              ? `/${org}/${dataset}/episode_${nextId}`
              : `./episode_${nextId}`;
          curatorBridgeLog("advance → router.push", { cur, nextId, idx, path });
          router.push(path);
        } else {
          console.warn(
            "[CuratorBridge:viz] advance=true but cannot go to next episode",
            { cur, idx, episodesLen: eps?.length, pathname },
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
  }, [router, org, dataset, pathname]);

  // Initialize based on URL time parameter
  useEffect(() => {
    // Initialize page based on current episode
    const episodeIndex = indexInEpisodeList(episodes, episodeId);
    if (episodeIndex !== -1) {
      setCurrentPage(Math.floor(episodeIndex / pageSize) + 1);
    }

    // Add keyboard event listener
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [episodes, episodeId, pageSize, searchParams, org, dataset, router]);

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
      const idx = indexInEpisodeList(episodes, episodeId);
      if (idx === -1) return;
      const nextIdx = key === "ArrowDown" ? idx + 1 : idx - 1;
      if (nextIdx >= 0 && nextIdx < episodes.length) {
        const nextId = episodes[nextIdx];
        const path =
          org && dataset
            ? `/${org}/${dataset}/episode_${nextId}`
            : `./episode_${nextId}`;
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
            preload="auto"
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
