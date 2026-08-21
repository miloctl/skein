/** A size in the unit that actually says something about it.
 *
 *  Fixed to MB, every small file reads as "0.0 MB" — which says a file the
 *  reader can plainly see is empty, and makes the row look like a bug rather
 *  than a note they attached. Mixed units in one sentence ("35 KB of 100 MB
 *  used") are the honest version. */
export function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} byte${bytes === 1 ? "" : "s"}`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${Math.round(kb)} KB`;
  const value = kb / 1024;
  return value < 10 ? `${value.toFixed(1)} MB` : `${Math.round(value)} MB`;
}
