import { planBatch } from '../../src/modules/topology/stream-cursor.js';
import { STREAM_ID, event } from './fixtures.js';

const cursor = { streamId: STREAM_ID, sequence: 12 };

// A container.destroyed with the given sequence, in any stream.
const at = (sequence: number, streamId = STREAM_ID) => ({
  ...event(
    sequence,
    '2026-09-23T15:39:00.000Z',
    'container.destroyed',
    { kind: 'container', id: 'c1' },
    {},
  ),
  streamId,
});

const sequences = (plan: ReturnType<typeof planBatch>) =>
  plan.action === 'apply' ? plan.events.map((item) => item.sequence) : null;

describe('planBatch', () => {
  it('applies the events that follow the cursor, in order', () => {
    const plan = planBatch(cursor, {
      streamId: STREAM_ID,
      events: [at(13), at(14), at(15)],
    });

    expect(sequences(plan)).toEqual([13, 14, 15]);
  });

  it('applies nothing when the long poll came back empty', () => {
    expect(planBatch(cursor, { streamId: STREAM_ID, events: [] })).toEqual({
      action: 'apply',
      events: [],
    });
  });

  it('drops the events it already applied', () => {
    const plan = planBatch(cursor, {
      streamId: STREAM_ID,
      events: [at(11), at(12), at(13)],
    });

    expect(sequences(plan)).toEqual([13]);
  });

  it('applies nothing when every event was already applied', () => {
    const plan = planBatch(cursor, {
      streamId: STREAM_ID,
      events: [at(10), at(11), at(12)],
    });

    expect(sequences(plan)).toEqual([]);
  });

  it('asks for a snapshot when there is a gap after the cursor', () => {
    const plan = planBatch(cursor, {
      streamId: STREAM_ID,
      events: [at(14), at(15)],
    });

    expect(plan).toEqual({ action: 'resync', reason: 'sequence_gap' });
  });

  it('asks for a snapshot when there is a gap inside the batch', () => {
    const plan = planBatch(cursor, {
      streamId: STREAM_ID,
      events: [at(13), at(15)],
    });

    expect(plan).toEqual({ action: 'resync', reason: 'sequence_gap' });
  });

  it('asks for a snapshot when the batch is out of order', () => {
    const plan = planBatch(cursor, {
      streamId: STREAM_ID,
      events: [at(14), at(13)],
    });

    expect(plan).toEqual({ action: 'resync', reason: 'sequence_gap' });
  });

  // A reset restarts the numbering: the new stream can be lower or higher.
  it.each([1, 13, 12, 500])(
    'asks for a snapshot on another stream, whatever its sequence (%i)',
    (sequence) => {
      const plan = planBatch(cursor, {
        streamId: 'other-stream',
        events: [at(sequence, 'other-stream')],
      });

      expect(plan).toEqual({ action: 'resync', reason: 'stream_changed' });
    },
  );

  it('asks for a snapshot when an event comes from another stream than its batch', () => {
    const plan = planBatch(cursor, {
      streamId: STREAM_ID,
      events: [at(13), at(14, 'other-stream')],
    });

    expect(plan).toEqual({ action: 'resync', reason: 'stream_changed' });
  });

  it('asks for a snapshot before any snapshot was loaded', () => {
    expect(planBatch(null, { streamId: STREAM_ID, events: [at(1)] })).toEqual({
      action: 'resync',
      reason: 'no_snapshot',
    });
  });
});
