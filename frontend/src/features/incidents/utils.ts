export function formatElapsedSince(
  start: string | Date,
  end?: string | Date | null,
): string {
  const startMs = new Date(start).getTime();
  const endMs = end ? new Date(end).getTime() : Date.now();
  if (Number.isNaN(startMs) || Number.isNaN(endMs)) return "--:--";

  const elapsedMin = Math.max(0, Math.floor((endMs - startMs) / 60000));
  const hh = Math.floor(elapsedMin / 60);
  const mm = elapsedMin % 60;

  return `${String(hh).padStart(2, "0")}h${String(mm).padStart(2, "0")}mn`;
}
