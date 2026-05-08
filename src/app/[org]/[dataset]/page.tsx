import { redirect } from "next/navigation";
import { initialEpisodeIndexFromEnv } from "@/utils/episodeFilter";

export default async function DatasetRootPage({
  params,
}: {
  params: Promise<{ org: string; dataset: string }>;
}) {
  const { org, dataset } = await params;
  const episodeN = initialEpisodeIndexFromEnv();

  redirect(`/${org}/${dataset}/episode_${episodeN}`);
}
