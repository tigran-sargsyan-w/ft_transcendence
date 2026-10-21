import { Logger } from '@nestjs/common';
import { firstValueFrom, take, toArray } from 'rxjs';
import type { MockInstance } from 'vitest';
import {
  type CollectorClient,
  SequenceExpiredError,
} from '../../src/modules/topology/collector-client.js';
import { buildGraph } from '../../src/modules/topology/build-graph.js';
import type {
  CollectorSnapshot,
  EventBatch,
} from '../../src/modules/topology/collector.types.js';
import {
  type TopologyChange,
  TopologyService,
} from '../../src/modules/topology/topology.service.js';
import {
  ENVIRONMENT_ID,
  STREAM_ID,
  emptySnapshot,
  event,
  eventsCrashAndRecreate,
  eventsUp,
  snapshotAfterRecreate,
  snapshotAfterUp,
} from './fixtures.js';

// Answers what it is scripted to answer, then behaves like a long poll
// nothing ever ends: it waits until the service stops.
class FakeCollector implements CollectorClient {
  snapshotCalls = 0;
  readonly eventCalls: number[] = [];

  constructor(
    private readonly snapshots: (CollectorSnapshot | Error)[],
    private readonly batches: (EventBatch | Error)[] = [],
  ) {}

  snapshot(signal: AbortSignal) {
    this.snapshotCalls += 1;

    return this.next(this.snapshots, signal);
  }

  events(after: number, signal: AbortSignal) {
    this.eventCalls.push(after);

    return this.next(this.batches, signal);
  }

  private async next<T>(queue: (T | Error)[], signal: AbortSignal) {
    const item = queue.shift();

    if (item === undefined) {
      return new Promise<T>((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(signal.reason), {
          once: true,
        });
      });
    }

    if (item instanceof Error) {
      throw item;
    }

    return item;
  }
}

const timings = { retryMinMs: 10, retryMaxMs: 40, batchWindowMs: 5 };

function setup(
  snapshots: (CollectorSnapshot | Error)[],
  batches: (EventBatch | Error)[] = [],
) {
  const collector = new FakeCollector(snapshots, batches);
  const slept: number[] = [];
  // Pauses are recorded, not waited for.
  const service = new TopologyService(collector, timings, async (ms) => {
    slept.push(ms);
  });

  return { collector, service, slept };
}

async function until(condition: () => boolean) {
  while (!condition()) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

// Starts the service and returns its first `count` changes, once `settled`.
async function run(
  service: TopologyService,
  count: number,
  settled: () => boolean = () => true,
): Promise<TopologyChange[]> {
  const changes = firstValueFrom(service.changes$.pipe(take(count), toArray()));

  service.onModuleInit();

  const received = await changes;

  await until(settled);
  await service.onModuleDestroy();

  return received;
}

const revisions = (changes: TopologyChange[]) =>
  changes.map((change) =>
    change.type === 'snapshot'
      ? change.snapshot.revision
      : change.delta.revision,
  );

const batchOf = (...events: EventBatch['events']) => ({
  streamId: STREAM_ID,
  events,
});

describe('TopologyService', () => {
  // A failure is logged as a warning; a normal resynchronization is not.
  let warn: MockInstance<Logger['warn']>;

  beforeEach(() => {
    warn = vi.spyOn(Logger.prototype, 'warn').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('publishes the snapshot, then the changes that follow it', async () => {
    const { collector, service, slept } = setup(
      [emptySnapshot],
      [batchOf(...eventsUp)],
    );

    const changes = await run(
      service,
      2,
      () => collector.eventCalls.length >= 2,
    );

    expect(changes.map((change) => change.type)).toEqual(['snapshot', 'delta']);
    expect(revisions(changes)).toEqual([1, 2]);
    expect(changes[1]).toMatchObject({
      type: 'delta',
      delta: { baseRevision: 1, revision: 2 },
    });
    // It reads on from where the snapshot and the batch left it.
    expect(collector.eventCalls).toEqual([0, 12]);
    // And lets the next burst gather before reading again.
    expect(slept).toEqual([timings.batchWindowMs]);
  });

  it('hands the current topology to whoever asks', async () => {
    const { service } = setup([emptySnapshot], [batchOf(...eventsUp)]);

    expect(service.snapshot()).toBeNull();

    await run(service, 2);

    expect(service.snapshot()).toMatchObject({ revision: 2 });
    expect(service.snapshot()?.nodes).toEqual(
      buildGraph(ENVIRONMENT_ID, snapshotAfterUp.containers).nodes,
    );
  });

  it('publishes nothing for a batch that changes nothing', async () => {
    const noop = event(
      13,
      '2026-09-23T15:40:00.000Z',
      'network.connected',
      { kind: 'network', id: 'e5b899c59c6a' },
      { containerId: 'x' },
    );
    const { collector, service, slept } = setup(
      [snapshotAfterUp],
      [
        batchOf(),
        batchOf(noop),
        batchOf(
          event(
            14,
            '2026-09-23T15:41:00.000Z',
            'container.destroyed',
            { kind: 'container', id: 'ghost' },
            {},
          ),
        ),
      ],
    );

    const changes = firstValueFrom(service.changes$.pipe(take(1), toArray()));

    service.onModuleInit();
    await changes;
    // Until the three batches have been read.
    await until(() => collector.eventCalls.length >= 4);
    await service.onModuleDestroy();

    expect(collector.eventCalls).toEqual([12, 12, 13, 14]);
    expect(slept).toEqual([]);
    expect(service.snapshot()?.revision).toBe(1);
  });

  it('asks for a new snapshot when the collector no longer holds the sequence', async () => {
    const { collector, service, slept } = setup(
      [snapshotAfterUp, snapshotAfterRecreate],
      [new SequenceExpiredError()],
    );

    const changes = await run(service, 2);

    expect(changes.map((change) => change.type)).toEqual([
      'snapshot',
      'snapshot',
    ]);
    expect(revisions(changes)).toEqual([1, 2]);
    expect(collector.snapshotCalls).toBe(2);
    // Not at full speed: it pauses before asking again.
    expect(slept).toEqual([timings.retryMinMs]);
    // A reset is normal life, not an outage.
    expect(warn).not.toHaveBeenCalled();
  });

  it('asks for a new snapshot when the stream changed, applying none of it', async () => {
    const afterReset = {
      ...snapshotAfterRecreate,
      streamId: 'stream_after_reset',
      sequence: 3,
    };
    // Would remove the db if it were applied.
    const destroyDb = {
      ...event(
        13,
        '2026-09-23T16:00:00.000Z',
        'container.destroyed',
        { kind: 'container', id: '6c28d5dff0b8' },
        {},
      ),
      streamId: 'stream_after_reset',
    };
    const { service } = setup(
      [snapshotAfterUp, afterReset],
      [{ streamId: 'stream_after_reset', events: [destroyDb] }],
    );

    const changes = await run(service, 2);

    expect(changes.map((change) => change.type)).toEqual([
      'snapshot',
      'snapshot',
    ]);
    expect(service.snapshot()?.nodes.map((node) => node.id)).toContain(
      'svc:demo:db',
    );
  });

  it('publishes nothing while the collector is down, and backs off', async () => {
    const { collector, service, slept } = setup([
      new Error('connect ECONNREFUSED'),
      new Error('connect ECONNREFUSED'),
      new Error('connect ECONNREFUSED'),
      new Error('connect ECONNREFUSED'),
      snapshotAfterUp,
    ]);

    const changes = await run(service, 1);

    // The first thing published is the first thing it was really told.
    expect(changes).toHaveLength(1);
    expect(revisions(changes)).toEqual([1]);
    expect(collector.snapshotCalls).toBe(5);
    // Doubled each time, up to the maximum.
    expect(slept).toEqual([10, 20, 40, 40]);
    expect(warn).toHaveBeenCalledTimes(4);
  });

  it('starts again from a snapshot when reading the events fails', async () => {
    const { collector, service, slept } = setup(
      [snapshotAfterUp, snapshotAfterUp],
      [new Error('socket hang up')],
    );

    const changes = await run(service, 2);

    expect(changes.map((change) => change.type)).toEqual([
      'snapshot',
      'snapshot',
    ]);
    expect(revisions(changes)).toEqual([1, 2]);
    expect(collector.snapshotCalls).toBe(2);
    // The pause after a failure starts short again once it answered.
    expect(slept).toEqual([timings.retryMinMs]);
  });

  it('says why the collector could not be reached', async () => {
    const failure = (message: string, cause?: Error) =>
      new Error(message, { cause });
    const refused = Object.assign(new AggregateError([]), {
      code: 'ECONNREFUSED',
    });
    const { service } = setup([
      failure('fetch failed', refused),
      failure('fetch failed', new Error('getaddrinfo ENOTFOUND collector')),
      failure('The collector answered HTTP 503'),
      snapshotAfterUp,
    ]);

    await run(service, 1);

    expect(warn.mock.calls.map((call) => String(call[0]))).toEqual([
      expect.stringContaining('fetch failed (ECONNREFUSED)'),
      expect.stringContaining('fetch failed (getaddrinfo ENOTFOUND collector)'),
      expect.stringMatching(/: The collector answered HTTP 503$/),
    ]);
  });

  it('keeps backing off while the events keep failing, although the snapshots work', async () => {
    const { service, slept } = setup(
      [
        snapshotAfterUp,
        snapshotAfterUp,
        snapshotAfterUp,
        snapshotAfterUp,
        snapshotAfterUp,
      ],
      [
        new Error('HTTP 500'),
        new Error('HTTP 500'),
        new Error('HTTP 500'),
        new Error('HTTP 500'),
      ],
    );

    await run(service, 5);

    // 10, 20, 40, then the maximum. Never the minimum again: a collector that
    // serves snapshots but not events is not asked again every 10 ms, nor
    // are the clients sent a new snapshot each time.
    expect(slept).toEqual([10, 20, 40, 40]);
  });

  it('backs off as well when the collector keeps resetting', async () => {
    const { service, slept } = setup(
      [snapshotAfterUp, snapshotAfterUp, snapshotAfterUp, snapshotAfterUp],
      [
        new SequenceExpiredError(),
        new SequenceExpiredError(),
        new SequenceExpiredError(),
      ],
    );

    await run(service, 4);

    expect(slept).toEqual([10, 20, 40]);
  });

  it('goes back to the shortest pause once events are read again', async () => {
    const { service, slept } = setup(
      [new Error('connect ECONNREFUSED'), snapshotAfterUp, snapshotAfterUp],
      [
        batchOf(...eventsCrashAndRecreate.slice(0, 2)),
        new Error('socket hang up'),
      ],
    );

    await run(service, 3);

    // Down (10). Then a snapshot, events that apply (the batch window, 5),
    // and a new failure: back to 10, not on to 20.
    expect(slept).toEqual([10, 5, 10]);
  });

  it('builds nothing without a collector', async () => {
    const service = new TopologyService(null);

    service.onModuleInit();
    await service.onModuleDestroy();

    expect(service.snapshot()).toBeNull();
  });

  it('stops for good, even in the middle of a long poll', async () => {
    const { collector, service } = setup([snapshotAfterUp]);
    const changes = firstValueFrom(service.changes$.pipe(toArray()));

    service.onModuleInit();
    // The snapshot is read, then the service waits on the events.
    await until(() => collector.eventCalls.length >= 1);
    await service.onModuleDestroy();

    expect(await changes).toHaveLength(1);
    expect(collector.eventCalls).toEqual([12]);
  });

  it('does not wait out a pause when it is stopped while publishing', async () => {
    // The real pause and the real clock: waited out, the pause lasts 1 s.
    const service = new TopologyService(new FakeCollector([snapshotAfterUp]));
    let stopping: Promise<void> | undefined;

    service.changes$.subscribe(() => {
      stopping = service.onModuleDestroy();
    });
    service.onModuleInit();

    await until(() => stopping !== undefined);

    const started = Date.now();

    await stopping;

    expect(Date.now() - started).toBeLessThan(500);
  });
});
