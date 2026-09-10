export function providerPageUrl(
  provider: string | null | undefined,
  externalId: string | number | null | undefined,
  kind: "series" | "movie",
): string | null {
  if (
    !provider ||
    externalId === null ||
    externalId === undefined ||
    externalId === ""
  ) {
    return null;
  }
  const id = encodeURIComponent(String(externalId));
  const p = provider.toLowerCase();
  if (p === "tvdb") {
    return kind === "movie"
      ? `https://www.thetvdb.com/?tab=movie&id=${id}`
      : `https://www.thetvdb.com/?tab=series&id=${id}`;
  }
  if (p === "tmdb") {
    return kind === "movie"
      ? `https://www.themoviedb.org/movie/${id}`
      : `https://www.themoviedb.org/tv/${id}`;
  }
  return null;
}
