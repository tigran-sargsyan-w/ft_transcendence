import {
  Logger,
  type OnModuleDestroy,
  type OnModuleInit,
} from '@nestjs/common';
import { type Observable, Subject } from 'rxjs';
import {
  type CollectorClient,
  SequenceExpiredError,
} from './collector-client.js';
import type { TopologyDelta, TopologySnapshot } from './graph.types.js';
import { TopologyState } from './topology-state.js';

// What the realtime gateway (T07) relays to the clients of an environment.
export type TopologyChange =
  | { type: 'snapshot'; snapshot: TopologySnapshot }
  | { type: 'delta'; delta: TopologyDelta };

export type TopologyTimings = {
  // Pause before asking again after a failure, doubled up to the maximum.
  retryMinMs: number;
  retryMaxMs: number;
  // After a change, wait this long before reading on: a burst of events (a
  // `docker compose up`) then comes back as one batch, hence one delta.
  batchWindowMs: number;
};

const DEFAULT_TIMINGS: TopologyTimings = {
  retryMinMs: 1_000,
  retryMaxMs: 30_000,
  batchWindowMs: 300,
};

type Sleep = (ms: number, signal: AbortSignal) => Promise<void>;

// Resolves early, without error, when `signal` aborts, at once if it has.
const sleepUnlessAborted: Sleep = (ms, signal) =>
  new Promise((resolve) => {
    // An abort that already happened does not fire again.
    if (signal.aborted) {
      resolve();
      return;
    }

    const done = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', done);
      resolve();
    };
    const timer = setTimeout(done, ms);

    signal.addEventListener('abort', done, { once: true });
  });

// `fetch failed` says nothing: the reason (ECONNREFUSED, ENOTFOUND...) is in
// its `cause`, as a code, or only as a message.
function errorMessage(error: unknown) {
  if (!(error instanceof Error)) {
    return String(error);
  }

  const { cause } = error;
  const reason =
    cause instanceof Error
      ? ((cause as { code?: string }).code ?? cause.message)
      : '';

  return reason ? `${error.message} (${reason})` : error.message;
}

// Keeps the topology of the environment up to date from the collector:
// a snapshot, then the events after it, read by long polling.
//
// Whatever goes wrong, nothing wrong is published: while the collector is
// unreachable the last topology stays as it was, and as soon as it answers
// again a new snapshot replaces everything. A stream reset, a gap or an
// expired sequence all end the same way: a new snapshot.
export class TopologyService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(TopologyService.name);
  private readonly state = new TopologyState();
  private readonly changes = new Subject<TopologyChange>();
  private readonly stop = new AbortController();
  private running: Promise<void> | null = null;
  // The pause before the next try; grows while the collector keeps failing.
  private retryMs: number;

  readonly changes$: Observable<TopologyChange> = this.changes.asObservable();

  // `client` is null when no collector is configured: nothing is built.
  constructor(
    private readonly client: CollectorClient | null,
    private readonly timings: TopologyTimings = DEFAULT_TIMINGS,
    private readonly sleep: Sleep = sleepUnlessAborted,
  ) {
    this.retryMs = timings.retryMinMs;
  }

  // The current topology, for a client that connects between two changes.
  snapshot() {
    return this.state.snapshot();
  }

  onModuleInit() {
    if (!this.client) {
      this.logger.log('COLLECTOR_URL is not set: the topology is not built');
      return;
    }

    // Not awaited: the loop runs for as long as the application does.
    this.running = this.run(this.client);
  }

  async onModuleDestroy() {
    this.stop.abort();
    await this.running;
    this.changes.complete();
  }

  private async run(client: CollectorClient) {
    const { signal } = this.stop;

    while (!signal.aborted) {
      try {
        const snapshot = this.state.loadSnapshot(await client.snapshot(signal));

        this.changes.next({ type: 'snapshot', snapshot });

        // Returns when a new snapshot is needed.
        await this.follow(client, signal);
      } catch (error) {
        if (signal.aborted) {
          return;
        }

        this.logger.warn(
          `Collector unavailable, retrying in ${this.retryMs} ms: ${errorMessage(error)}`,
        );
      }

      // Whichever way it ended, a new snapshot comes next. A snapshot alone
      // proves nothing (the events may be what fails): the pause only goes
      // back to its minimum once events have been read again.
      await this.sleep(this.retryMs, signal);
      this.retryMs = Math.min(this.retryMs * 2, this.timings.retryMaxMs);
    }
  }

  // Reads and applies events until a new snapshot is needed (returns), or
  // until something fails (throws).
  private async follow(client: CollectorClient, signal: AbortSignal) {
    while (!signal.aborted) {
      const cursor = this.state.cursor;

      if (!cursor) {
        return;
      }

      let result;

      try {
        result = this.state.applyBatch(
          await client.events(cursor.sequence, signal),
        );
      } catch (error) {
        if (error instanceof SequenceExpiredError) {
          return;
        }

        throw error;
      }

      if (result.status === 'resync') {
        return;
      }

      // Events are being read: the collector is serving.
      this.retryMs = this.timings.retryMinMs;

      if (result.delta) {
        this.changes.next({ type: 'delta', delta: result.delta });
        await this.sleep(this.timings.batchWindowMs, signal);
      }
    }
  }
}
