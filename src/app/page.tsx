import { redirect } from "next/navigation";
import HomeClient from "./home-client";
import { getDatasetHostingConfig } from "@/utils/datasetEnv";
import { initialEpisodeIndexFromEnv } from "@/utils/episodeFilter";
import { getResumeEpisodeIndex } from "@/utils/curationResume";

type HomeSearchParams = {
  path?: string | string[];
  dataset?: string | string[];
  episode?: string | string[];
  t?: string | string[];
};

function firstString(v: string | string[] | undefined): string | undefined {
  if (v === undefined) return undefined;
  return typeof v === "string" ? v : v[0];
}

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<HomeSearchParams>;
}) {
  const sp = await searchParams;

  const path = firstString(sp.path);
  if (path) {
    const p = path.startsWith("/") ? path : `/${path}`;
    redirect(p);
  }

  const dataset = firstString(sp.dataset);
  const episode = firstString(sp.episode);
  const t = firstString(sp.t);

  if (dataset && episode) {
    let url = `/${dataset}/episode_${episode}`;
    if (t) url += `?t=${encodeURIComponent(t)}`;
    redirect(url);
  }
  if (dataset) {
    redirect(`/${dataset}`);
  }

  const repo =
    process.env.REPO_ID?.trim() ||
    process.env.NEXT_PUBLIC_REPO_ID?.trim() ||
    getDatasetHostingConfig().defaultRepoId;

  if (repo) {
    const episodeN = getResumeEpisodeIndex() ?? initialEpisodeIndexFromEnv();
    redirect(`/${repo.replace(/^\//, "")}/episode_${episodeN}`);
  }

  return <HomeClient />;
}
