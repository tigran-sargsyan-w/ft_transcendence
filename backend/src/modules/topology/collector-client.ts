import type { CollectorSnapshot, EventBatch } from './collector.types.js';

// The collector no longer holds the sequence asked for: its buffer moved on,
// or it restarted. The remedy is the same as for a stream reset: a snapshot.
export class SequenceExpiredError extends Error {}

// Nest reads from the collector (docs/architecture/collector-contract.md).
export interface CollectorClient {
  snapshot(signal: AbortSignal): Promise<CollectorSnapshot>;
  // A long poll: waits for news, then answers with the events after `after`.
  events(after: number, signal: AbortSignal): Promise<EventBatch>;
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null;

function isSnapshot(data: unknown): data is CollectorSnapshot {
  return (
    isObject(data) &&
    typeof data.streamId === 'string' &&
    typeof data.environmentId === 'string' &&
    typeof data.capturedAt === 'string' &&
    typeof data.sequence === 'number' &&
    Array.isArray(data.containers)
  );
}

function isBatch(data: unknown): data is EventBatch {
  return (
    isObject(data) &&
    typeof data.streamId === 'string' &&
    Array.isArray(data.events)
  );
}

export class HttpCollectorClient implements CollectorClient {
  private readonly baseUrl: string;

  constructor(
    baseUrl: string,
    private readonly snapshotTimeoutMs = 10_000,
    // The collector holds a long poll for about 25 s: wait longer than that.
    private readonly eventsTimeoutMs = 35_000,
  ) {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
  }

  async snapshot(signal: AbortSignal) {
    const data = await this.get('/snapshot', this.snapshotTimeoutMs, signal);

    if (!isSnapshot(data)) {
      throw new Error('The collector sent a malformed snapshot');
    }

    return data;
  }

  async events(after: number, signal: AbortSignal) {
    const data = await this.get(
      `/events?after=${after}`,
      this.eventsTimeoutMs,
      signal,
    );

    if (!isBatch(data)) {
      throw new Error('The collector sent a malformed batch of events');
    }

    return data;
  }

  // The `data` of a successful answer. Anything else is an error: the loop
  // that calls this retries, and never publishes what it did not get.
  private async get(path: string, timeoutMs: number, signal: AbortSignal) {
    const response = await fetch(`${this.baseUrl}/api/v1${path}`, {
      signal: AbortSignal.any([signal, AbortSignal.timeout(timeoutMs)]),
    });
    const body: unknown = await response.json().catch(() => null);

    if (
      response.status === 409 &&
      isObject(body) &&
      isObject(body.error) &&
      body.error.code === 'COLLECTOR_SEQUENCE_EXPIRED'
    ) {
      throw new SequenceExpiredError();
    }

    if (!response.ok || !isObject(body) || body.data === undefined) {
      throw new Error(`The collector answered HTTP ${response.status}`);
    }

    return body.data;
  }
}
