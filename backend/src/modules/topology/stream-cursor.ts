import type { CollectorEvent, EventBatch } from './collector.types.js';

// Where Nest stands in the collector's event stream. The collector gives
// itself a new `streamId` whenever it resets, and numbers from 1 again, so a
// sequence alone says nothing: it only means something within its stream.
export type StreamCursor = { streamId: string; sequence: number };

export type ResyncReason = 'no_snapshot' | 'stream_changed' | 'sequence_gap';

export type BatchPlan =
  | { action: 'apply'; events: CollectorEvent[] }
  | { action: 'resync'; reason: ResyncReason };

// What to do with a batch of events read after `cursor`:
// - apply: the new events, in order (none when the long poll came back empty);
// - resync: do not apply anything, load a fresh snapshot instead. This is the
//   one path for a stream reset, a gap, and a sequence the collector no
//   longer holds (the collector answers that one with an error).
export function planBatch(
  cursor: StreamCursor | null,
  batch: EventBatch,
): BatchPlan {
  if (!cursor) {
    return { action: 'resync', reason: 'no_snapshot' };
  }

  const sameStream =
    batch.streamId === cursor.streamId &&
    batch.events.every((event) => event.streamId === cursor.streamId);

  if (!sameStream) {
    return { action: 'resync', reason: 'stream_changed' };
  }

  // Already applied: ignored.
  const events = batch.events.filter(
    (event) => event.sequence > cursor.sequence,
  );

  let expected = cursor.sequence + 1;

  for (const event of events) {
    if (event.sequence !== expected) {
      return { action: 'resync', reason: 'sequence_gap' };
    }

    expected += 1;
  }

  return { action: 'apply', events };
}
