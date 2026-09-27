import { randomUUID } from 'node:crypto';

import type {
  CollectorEvent,
  CollectorEventType,
  CollectorResourceKind,
} from './types.js';

type PendingEvent = {
  occurredAt: string;
  type: CollectorEventType;
  resource: {
    kind: CollectorResourceKind;
    id: string;
  };
  data: Record<string, unknown>;
};

export class SequenceExpiredError extends Error {}

export class EventStore {
  readonly streamId = randomUUID();

  private sequence = 0;
  private readonly buffer: CollectorEvent[] = [];
  private readonly waiters = new Set<() => void>();

  constructor(
    private readonly environmentId: string,
    private readonly maxBufferSize = 1000,
  ) {}

  currentSequence(): number {
    return this.sequence;
  }

  append(event: PendingEvent): CollectorEvent {
    this.sequence += 1;

    const stored: CollectorEvent = {
      schemaVersion: 1,
      streamId: this.streamId,
      environmentId: this.environmentId,
      eventId: `evt_${String(this.sequence).padStart(8, '0')}`,
      sequence: this.sequence,
      occurredAt: event.occurredAt,
      type: event.type,
      resource: event.resource,
      data: event.data,
    };

    this.buffer.push(stored);

    if (this.buffer.length > this.maxBufferSize) {
      this.buffer.shift();
    }

    for (const wake of this.waiters) {
      wake();
    }

    this.waiters.clear();

    return stored;
  }

  eventsAfter(after: number): CollectorEvent[] {
    if (after > this.sequence) {
      throw new SequenceExpiredError();
    }

    const first = this.buffer[0];

    if (first && after < first.sequence - 1) {
      throw new SequenceExpiredError();
    }

    return this.buffer.filter((event) => event.sequence > after);
  }

  async waitForEvents(
    after: number,
    timeoutMs: number,
  ): Promise<CollectorEvent[]> {
    const existing = this.eventsAfter(after);

    if (existing.length > 0) {
      return existing;
    }

    await new Promise<void>((resolve) => {
      const timeout = setTimeout(() => {
        this.waiters.delete(wake);
        resolve();
      }, timeoutMs);

      const wake = (): void => {
        clearTimeout(timeout);
        resolve();
      };

      this.waiters.add(wake);
    });

    return this.eventsAfter(after);
  }
}