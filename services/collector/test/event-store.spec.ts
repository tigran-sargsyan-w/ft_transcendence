import {
  describe,
  expect,
  it,
} from 'vitest';

import {
  EventStore,
  SequenceExpiredError,
} from '../src/event-store.js';

function appendStartedEvent(
  store: EventStore,
): void {
  store.append({
    occurredAt: '2026-09-27T12:00:00.000Z',
    type: 'container.started',
    resource: {
      kind: 'container',
      id: 'container-1',
    },
    data: {},
  });
}

describe('EventStore', () => {
  it('starts with sequence 0', () => {
    const store = new EventStore('env-test');

    expect(store.checkpoint()).toEqual({
      streamId: store.streamId,
      sequence: 0,
    });
  });

  it('increments sequence for appended events', () => {
    const store = new EventStore('env-test');

    const first = store.append({
      occurredAt: '2026-09-27T12:00:00.000Z',
      type: 'container.created',
      resource: {
        kind: 'container',
        id: 'container-1',
      },
      data: {},
    });

    const second = store.append({
      occurredAt: '2026-09-27T12:00:01.000Z',
      type: 'container.started',
      resource: {
        kind: 'container',
        id: 'container-1',
      },
      data: {},
    });

    expect(first.sequence).toBe(1);
    expect(second.sequence).toBe(2);

    expect(first.eventId).toBe('evt_00000001');
    expect(second.eventId).toBe('evt_00000002');
  });

  it('returns buffered events after a sequence', async () => {
    const store = new EventStore('env-test');

    appendStartedEvent(store);
    appendStartedEvent(store);

    const result = await store.waitForEvents(
      0,
      10,
    );

    expect(result.streamId).toBe(
      store.streamId,
    );

    expect(
      result.events.map((event) => event.sequence),
    ).toEqual([1, 2]);
  });

  it('expires sequences outside the buffer', async () => {
    const store = new EventStore(
      'env-test',
      2,
    );

    appendStartedEvent(store);
    appendStartedEvent(store);
    appendStartedEvent(store);

    await expect(
      store.waitForEvents(0, 10),
    ).rejects.toBeInstanceOf(
      SequenceExpiredError,
    );
  });

  it('rejects a sequence ahead of the current stream', async () => {
    const store = new EventStore('env-test');

    await expect(
      store.waitForEvents(10, 10),
    ).rejects.toBeInstanceOf(
      SequenceExpiredError,
    );
  });

  it('returns an empty batch after timeout', async () => {
    const store = new EventStore('env-test');

    const result = await store.waitForEvents(
      0,
      5,
    );

    expect(result.events).toEqual([]);
    expect(result.streamId).toBe(
      store.streamId,
    );
  });

  it('wakes a long poll when an event arrives', async () => {
    const store = new EventStore('env-test');

    const waiting = store.waitForEvents(
      0,
      1000,
    );

    appendStartedEvent(store);

    const result = await waiting;

    expect(result.events).toHaveLength(1);
    expect(result.events[0]?.sequence).toBe(1);
  });

  it('resets sequence and changes streamId', () => {
    const store = new EventStore('env-test');

    appendStartedEvent(store);

    const previousStreamId =
      store.streamId;

    store.resetStream();

    expect(store.streamId).not.toBe(
      previousStreamId,
    );

    expect(
      store.checkpoint().sequence,
    ).toBe(0);
  });

  it('invalidates an active long poll after stream reset', async () => {
    const store = new EventStore('env-test');

    const waiting = store.waitForEvents(
      0,
      1000,
    );

    store.resetStream();

    await expect(waiting).rejects.toBeInstanceOf(
      SequenceExpiredError,
    );
  });
});