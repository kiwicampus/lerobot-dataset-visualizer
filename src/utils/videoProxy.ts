export function getProxiedVideoUrl(url: string): string {
  if (url.startsWith("https://huggingface.co/")) {
    return `/api/video-proxy?url=${encodeURIComponent(url)}`;
  }
  return url;
}
