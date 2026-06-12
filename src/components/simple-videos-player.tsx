"use client";

import React, { useEffect, useRef, useCallback } from "react";
import { usePlayback, useTime } from "../context/time-context";
import { FaExpand, FaCompress, FaTimes, FaEye } from "react-icons/fa";
import { getProxiedVideoUrl } from "@/utils/videoProxy";

/** Wall-clock interval between global time updates while playing (~20 Hz). */
const CLOCK_EMIT_MS = 50;
/** When speeding up, wait until this many seconds are buffered ahead (scaled by rate). */
const SPEED_UP_BUFFER_AHEAD_SEC = 2.5;
const SPEED_UP_WAIT_MS = 4500;

type VideoInfo = {
  filename: string;
  url: string;
  isSegmented?: boolean;
  segmentStart?: number;
  segmentEnd?: number;
  segmentDuration?: number;
};

type VideoPlayerProps = {
  videosInfo: VideoInfo[];
  onVideosReady?: () => void;
};

function bufferedAheadSeconds(video: HTMLVideoElement): number {
  if (!video.buffered.length) return 0;
  const t = video.currentTime;
  for (let i = 0; i < video.buffered.length; i++) {
    const start = video.buffered.start(i);
    const end = video.buffered.end(i);
    if (t >= start && t <= end) {
      return Math.max(0, end - t);
    }
    if (t < start) return 0;
  }
  return Math.max(0, video.buffered.end(video.buffered.length - 1) - t);
}

function globalTimeFromVideo(
  video: HTMLVideoElement,
  info: VideoInfo | undefined,
): number {
  if (!info) return video.currentTime;
  if (info.isSegmented) {
    return video.currentTime - (info.segmentStart || 0);
  }
  return video.currentTime;
}

export const SimpleVideosPlayer = ({
  videosInfo,
  onVideosReady,
}: VideoPlayerProps) => {
  const { currentTime, setCurrentTime } = useTime();
  const { isPlaying, setIsPlaying, playbackSpeed } = usePlayback();
  const videoRefs = useRef<(HTMLVideoElement | null)[]>([]);
  const hasSignaledReadyRef = useRef(false);
  const [hiddenVideos, setHiddenVideos] = React.useState<string[]>([]);
  const [enlargedVideo, setEnlargedVideo] = React.useState<string | null>(null);
  const [showHiddenMenu, setShowHiddenMenu] = React.useState(false);
  const [videosReady, setVideosReady] = React.useState(false);
  const [speedBuffering, setSpeedBuffering] = React.useState(false);

  const prevPlaybackSpeedRef = useRef(playbackSpeed);
  const speedUpAbortRef = useRef<AbortController | null>(null);

  const firstVisibleIdx = videosInfo.findIndex(
    (video) => !hiddenVideos.includes(video.filename),
  );

  const flushMasterTimeToContext = useCallback(() => {
    const idx = firstVisibleIdx;
    if (idx < 0) return;
    const video = videoRefs.current[idx];
    const info = videosInfo[idx];
    if (!video) return;
    setCurrentTime(globalTimeFromVideo(video, info));
  }, [firstVisibleIdx, videosInfo, setCurrentTime]);

  // Initialize video refs array
  useEffect(() => {
    videoRefs.current = videoRefs.current.slice(0, videosInfo.length);
  }, [videosInfo.length]);

  const [failedVideos, setFailedVideos] = React.useState<Set<string>>(new Set());

  // Handle videos ready
  useEffect(() => {
    hasSignaledReadyRef.current = false;
    setVideosReady(false);
    let readyCount = 0;
    const videoStatuses = new Map<number, "pending" | "ready" | "failed">();

    const markVideoReady = (index: number, status: "ready" | "failed") => {
      if (videoStatuses.get(index) !== "pending") return;
      videoStatuses.set(index, status);
      readyCount++;

      if (status === "failed") {
        const info = videosInfo[index];
        setFailedVideos((prev) => new Set(prev).add(info.filename));
      }

      const hasSuccessfulVideo = Array.from(videoStatuses.values()).some(
        (s) => s === "ready",
      );
      const allProcessed = readyCount === videosInfo.length;
      const shouldSignalReady =
        !hasSignaledReadyRef.current && (hasSuccessfulVideo || allProcessed);

      if (shouldSignalReady && onVideosReady) {
        hasSignaledReadyRef.current = true;
        setVideosReady(true);
        onVideosReady();
        if (hasSuccessfulVideo) setIsPlaying(true);
      }
    };

    const VIDEO_LOAD_TIMEOUT = 120000;
    const timeoutIds: NodeJS.Timeout[] = [];

    videoRefs.current.forEach((video, index) => {
      if (video) {
        const info = videosInfo[index];
        videoStatuses.set(index, "pending");

        const timeoutId = setTimeout(() => {
          if (videoStatuses.get(index) === "pending") {
            if (video.networkState === 2 || video.readyState > 0) {
              return;
            }
            markVideoReady(index, "failed");
          }
        }, VIDEO_LOAD_TIMEOUT);
        timeoutIds.push(timeoutId);

        const handleError = () => {
          markVideoReady(index, "failed");
        };
        video.addEventListener("error", handleError);

        if (info.isSegmented) {
          const handleTimeUpdate = () => {
            const segmentEnd = info.segmentEnd || video.duration;
            const segmentStart = info.segmentStart || 0;

            if (video.currentTime >= segmentEnd - 0.05) {
              video.currentTime = segmentStart;
              if (index === firstVisibleIdx) {
                setCurrentTime(0);
              }
            }
          };

          const handleLoadedData = () => {
            video.currentTime = info.segmentStart || 0;
            markVideoReady(index, "ready");
          };

          video.addEventListener("timeupdate", handleTimeUpdate);
          video.addEventListener("loadeddata", handleLoadedData);

          if (video.readyState >= 1) {
            video.currentTime = info.segmentStart || 0;
            markVideoReady(index, "ready");
          }

          (video as any)._segmentHandlers = () => {
            video.removeEventListener("timeupdate", handleTimeUpdate);
            video.removeEventListener("loadeddata", handleLoadedData);
            video.removeEventListener("error", handleError);
          };
        } else {
          const handleEnded = () => {
            video.currentTime = 0;
            if (index === firstVisibleIdx) {
              setCurrentTime(0);
            }
          };

          const handleCanPlayThrough = () => {
            markVideoReady(index, "ready");
          };

          video.addEventListener("ended", handleEnded);
          video.addEventListener("canplaythrough", handleCanPlayThrough, {
            once: true,
          });

          if (video.readyState >= 4) {
            markVideoReady(index, "ready");
          }

          (video as any)._segmentHandlers = () => {
            video.removeEventListener("ended", handleEnded);
            video.removeEventListener("error", handleError);
          };
        }
      }
    });

    return () => {
      timeoutIds.forEach((id) => clearTimeout(id));
      videoRefs.current.forEach((video) => {
        if (video && (video as any)._segmentHandlers) {
          (video as any)._segmentHandlers();
        }
      });
    };
  }, [videosInfo, onVideosReady, setIsPlaying, firstVisibleIdx, setCurrentTime]);

  // Throttled clock from master video while playing (avoid setCurrentTime on every timeupdate)
  useEffect(() => {
    if (!videosReady || firstVisibleIdx < 0) return;
    const video = videoRefs.current[firstVisibleIdx];
    const info = videosInfo[firstVisibleIdx];
    if (!video || !info) return;

    if (!isPlaying) {
      flushMasterTimeToContext();
      return;
    }

    let rafId = 0;
    let lastEmit = 0;

    const tick = (now: number) => {
      if (!video.paused) {
        if (now - lastEmit >= CLOCK_EMIT_MS) {
          lastEmit = now;
          setCurrentTime(globalTimeFromVideo(video, info));
        }
      }
      rafId = requestAnimationFrame(tick);
    };

    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, [
    isPlaying,
    videosReady,
    firstVisibleIdx,
    videosInfo,
    setCurrentTime,
    flushMasterTimeToContext,
  ]);

  // Play/pause + playbackRate (overlay effect may pause briefly for speed-up)
  useEffect(() => {
    if (!videosReady || speedBuffering) return;

    videoRefs.current.forEach((video, idx) => {
      if (!video) return;
      if (hiddenVideos.includes(videosInfo[idx].filename)) return;

      video.playbackRate = playbackSpeed;

      if (!isPlaying) {
        // Let any in-flight play() settle before pausing to avoid the
        // "play() interrupted by pause()" DOMException.
        video.play().then(() => video.pause()).catch(() => {});
        return;
      }

      video.play().catch((e) => {
        if (e.name !== "AbortError") {
          console.error("Error playing video");
        }
      });
    });
  }, [
    isPlaying,
    playbackSpeed,
    videosReady,
    hiddenVideos,
    videosInfo,
    speedBuffering,
  ]);

  // Sync non-master videos / apply explicit seeks from context (drift correction)
  useEffect(() => {
    if (!videosReady) return;

    const fastPlaying = isPlaying && playbackSpeed > 1;
    const driftThreshold = fastPlaying ? 1.0 : 0.2;

    videoRefs.current.forEach((video, index) => {
      if (video && !hiddenVideos.includes(videosInfo[index].filename)) {
        if (index === firstVisibleIdx && isPlaying) {
          return;
        }
        const info = videosInfo[index];
        let targetTime = currentTime;

        if (info.isSegmented) {
          targetTime = (info.segmentStart || 0) + currentTime;
        }

        const drift = Math.abs(video.currentTime - targetTime);
        if (drift > driftThreshold) {
          video.currentTime = targetTime;
        }
      }
    });
  }, [
    currentTime,
    videosInfo,
    videosReady,
    hiddenVideos,
    isPlaying,
    playbackSpeed,
    firstVisibleIdx,
  ]);

  // Speed-up: brief buffer gate + overlay when increasing rate above 1× while playing
  useEffect(() => {
    if (!videosReady || firstVisibleIdx < 0) {
      prevPlaybackSpeedRef.current = playbackSpeed;
      return;
    }
    if (!isPlaying) {
      prevPlaybackSpeedRef.current = playbackSpeed;
      return;
    }

    const prev = prevPlaybackSpeedRef.current;
    const increased = playbackSpeed > prev && playbackSpeed > 1;
    prevPlaybackSpeedRef.current = playbackSpeed;
    if (!increased) return;

    const master = videoRefs.current[firstVisibleIdx];
    if (!master) return;

    speedUpAbortRef.current?.abort();
    const ac = new AbortController();
    speedUpAbortRef.current = ac;

    const run = async () => {
      const wasPlaying = isPlaying;
      setSpeedBuffering(true);
      master.playbackRate = playbackSpeed;
      videoRefs.current.forEach((v, idx) => {
        if (!v || hiddenVideos.includes(videosInfo[idx].filename)) return;
        v.playbackRate = playbackSpeed;
        v.play().then(() => v.pause()).catch(() => {});
      });

      const needAhead = SPEED_UP_BUFFER_AHEAD_SEC / Math.max(1, playbackSpeed);
      const deadline = Date.now() + SPEED_UP_WAIT_MS;

      try {
        await new Promise<void>((resolve) => {
          const check = () => {
            if (ac.signal.aborted) {
              resolve();
              return;
            }
            if (
              bufferedAheadSeconds(master) >= needAhead ||
              Date.now() >= deadline
            ) {
              resolve();
              return;
            }
            requestAnimationFrame(check);
          };
          master.addEventListener("progress", check, { signal: ac.signal });
          master.addEventListener("canplay", check, { signal: ac.signal });
          check();
        });
      } finally {
        ac.abort();
      }

      setSpeedBuffering(false);
      if (wasPlaying) {
        videoRefs.current.forEach((v, idx) => {
          if (!v || hiddenVideos.includes(videosInfo[idx].filename)) return;
          v.play().catch(() => {});
        });
      }
    };

    void run();
    return () => {
      ac.abort();
      setSpeedBuffering(false);
    };
  }, [
    playbackSpeed,
    videosReady,
    firstVisibleIdx,
    hiddenVideos,
    videosInfo,
    isPlaying,
  ]);

  const handlePlay = (video: HTMLVideoElement, info: VideoInfo) => {
    if (info.isSegmented) {
      const segmentStart = info.segmentStart || 0;
      const segmentEnd = info.segmentEnd || video.duration;

      if (
        video.currentTime < segmentStart ||
        video.currentTime >= segmentEnd
      ) {
        video.currentTime = segmentStart;
      }
    }
    video.play().catch(() => {});
  };

  return (
    <>
      {speedBuffering && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 pointer-events-auto">
          <div className="rounded-xl border border-slate-500 bg-slate-900 px-8 py-6 text-center shadow-xl">
            <p className="text-slate-100 font-medium">
              Ajustando velocidad ({playbackSpeed}×)…
            </p>
            <p className="mt-2 text-xs text-slate-400">
              Preparando buffer de vídeo
            </p>
          </div>
        </div>
      )}

      {hiddenVideos.length > 0 && (
        <div className="relative mb-4">
          <button
            className="flex items-center gap-2 rounded bg-slate-800 px-3 py-2 text-sm text-slate-100 hover:bg-slate-700 border border-slate-500"
            onClick={() => setShowHiddenMenu(!showHiddenMenu)}
          >
            <FaEye /> Show Hidden Videos ({hiddenVideos.length})
          </button>
          {showHiddenMenu && (
            <div className="absolute left-0 mt-2 w-max rounded border border-slate-500 bg-slate-900 shadow-lg p-2 z-50">
              <div className="mb-2 text-xs text-slate-300">
                Restore hidden videos:
              </div>
              {hiddenVideos.map((filename) => (
                <button
                  key={filename}
                  className="block w-full text-left px-2 py-1 rounded hover:bg-slate-700 text-slate-100"
                  onClick={() =>
                    setHiddenVideos((prev) => prev.filter((v) => v !== filename))
                  }
                >
                  {filename}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="flex flex-wrap gap-x-2 gap-y-6">
        {videosInfo.map((info, idx) => {
          if (hiddenVideos.includes(info.filename)) return null;

          const isEnlarged = enlargedVideo === info.filename;

          return (
            <div
              key={info.filename}
              className={`${
                isEnlarged
                  ? "z-40 fixed inset-0 bg-black bg-opacity-90 flex flex-col items-center justify-center"
                  : "max-w-96"
              }`}
            >
              <p className="truncate w-full rounded-t-xl bg-gray-800 px-2 text-sm text-gray-300 flex items-center justify-between">
                <span>{info.filename}</span>
                <span className="flex gap-1">
                  <button
                    title={isEnlarged ? "Minimize" : "Enlarge"}
                    className="ml-2 p-1 hover:bg-slate-700 rounded"
                    onClick={() =>
                      setEnlargedVideo(isEnlarged ? null : info.filename)
                    }
                  >
                    {isEnlarged ? <FaCompress /> : <FaExpand />}
                  </button>
                  <button
                    title="Hide Video"
                    className="ml-1 p-1 hover:bg-slate-700 rounded"
                    onClick={() =>
                      setHiddenVideos((prev) => [...prev, info.filename])
                    }
                    disabled={
                      videosInfo.filter((v) => !hiddenVideos.includes(v.filename))
                        .length === 1
                    }
                  >
                    <FaTimes />
                  </button>
                </span>
              </p>
              {failedVideos.has(info.filename) ? (
                <div
                  className={`w-full flex flex-col items-center justify-center bg-slate-800 text-slate-300 p-4 ${
                    isEnlarged
                      ? "max-h-[90vh] max-w-[90vw] min-h-[300px]"
                      : "min-h-[200px]"
                  }`}
                >
                  <svg
                    className="w-12 h-12 mb-2 text-slate-500"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                    />
                  </svg>
                  <p className="text-sm font-medium mb-1">Video unavailable</p>
                  <p className="text-xs text-slate-400 text-center">
                    This video requires authentication.
                    <br />
                    Private dataset videos cannot be displayed in the browser.
                  </p>
                </div>
              ) : (
                <video
                  ref={(el) => {
                    videoRefs.current[idx] = el;
                  }}
                  className={`w-full object-contain ${
                    isEnlarged ? "max-h-[90vh] max-w-[90vw]" : ""
                  }`}
                  muted
                  // metadata only: these are 500MB concatenated files. "auto" makes the
                  // browser open speculative buffer-ahead range requests and read them
                  // slowly while paused, holding the ~6 connection slots open for tens of
                  // seconds and starving the other cameras. Metadata is enough to mark the
                  // segment ready (overlay clears); playback buffers on demand.
                  preload="metadata"
                  onPlay={(e) => handlePlay(e.currentTarget, info)}
                >
                  <source src={getProxiedVideoUrl(info.url)} type="video/mp4" />
                  Your browser does not support the video tag.
                </video>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
};

export default SimpleVideosPlayer;
