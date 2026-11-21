export function formatElapsedSince(
  start: string | Date,
  now: Date = new Date(),
): string {
  const startMs = new Date(start).getTime();
  if (Number.isNaN(startMs)) return "--:--";

  const elapsedMin = Math.max(0, Math.floor((now.getTime() - startMs) / 60000));
  const hh = Math.floor(elapsedMin / 60);
  const mm = elapsedMin % 60;

  return `${String(hh).padStart(2, "0")}h${String(mm).padStart(2, "0")}mn`;
}
