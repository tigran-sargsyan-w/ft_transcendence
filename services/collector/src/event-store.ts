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

export type StreamCheckpoint = {
  streamId: string;
  sequence: number;
};

export type EventBatch = {
  streamId: string;
  events: CollectorEvent[];
};

export class SequenceExpiredError extends Error {}

export class EventStore {
  private _streamId = randomUUID();
  private sequence = 0;

  private readonly buffer: CollectorEvent[] = [];
  private readonly waiters = new Set<() => void>();

  constructor(
    private readonly environmentId: string,
    private readonly maxBufferSize = 1000,
  ) {}

  get streamId(): string {
    return this._streamId;
  }

  checkpoint(): StreamCheckpoint {
    return {
      streamId: this._streamId,
      sequence: this.sequence,
    };
  }

  resetStream(): void {
    this._streamId = randomUUID();
    this.sequence = 0;
    this.buffer.length = 0;

    for (const wake of this.waiters) {
      wake();
    }

    this.waiters.clear();
  }

  append(event: PendingEvent): CollectorEvent {
    this.sequence += 1;

    const stored: CollectorEvent = {
      schemaVersion: 1,
      streamId: this._streamId,
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

  private eventsAfter(after: number): CollectorEvent[] {
    if (after > this.sequence) {
      throw new SequenceExpiredError();
    }

    const first = this.buffer[0];

    if (first && after < first.sequence - 1) {
      throw new SequenceExpiredError();
    }

    return this.buffer.filter(
      (event) => event.sequence > after,
    );
  }

  async waitForEvents(
    after: number,
    timeoutMs: number,
  ): Promise<EventBatch> {
    const initialStreamId = this._streamId;

    const existing = this.eventsAfter(after);

    if (existing.length > 0) {
      return {
        streamId: initialStreamId,
        events: existing,
      };
    }

    await new Promise<void>((resolve) => {
      let resolved = false;

      const finish = (): void => {
        if (resolved) {
          return;
        }

        resolved = true;
        clearTimeout(timeout);
        this.waiters.delete(wake);
        resolve();
      };

      const wake = (): void => {
        finish();
      };

      const timeout = setTimeout(
        finish,
        timeoutMs,
      );

      this.waiters.add(wake);

      // Avoid a race where an event/reset happens
      // just before the waiter is registered.
      if (
        this._streamId !== initialStreamId ||
        this.eventsAfter(after).length > 0
      ) {
        finish();
      }
    });

    if (this._streamId !== initialStreamId) {
      throw new SequenceExpiredError();
    }

    return {
      streamId: this._streamId,
      events: this.eventsAfter(after),
    };
  }
}