import {
  changedFields,
  resolvedAt,
  statusHistory,
  toTimelineEntry,
  type EditableFields,
  type StoredEvent,
} from '../../src/modules/incidents/incident-events.js';

const current: EditableFields = {
  title: 'API down',
  severity: 'sev2',
  status: 'open',
  commanderId: 'user-a',
};

const ada = { id: 'user-a', displayName: 'Ada', avatarUrl: null };

function stored(
  type: string,
  data: unknown,
  createdAt = '2026-11-12T10:00:00Z',
) {
  return {
    id: `id-${type}`,
    seq: 7,
    incidentId: 'incident-1',
    type,
    data,
    createdAt: new Date(createdAt),
    actor: ada,
  } satisfies StoredEvent;
}

describe('changedFields', () => {
  it('writes nothing when every value is the current one', () => {
    expect(changedFields(current, { ...current })).toEqual([]);
  });

  it('writes nothing when no field is sent', () => {
    expect(changedFields(current, {})).toEqual([]);
  });

  it('writes one event for each changed field, with its from and to', () => {
    expect(
      changedFields(current, {
        status: 'investigating',
        severity: 'sev1',
        title: 'API down',
      }),
    ).toEqual([
      {
        type: 'severity_changed',
        data: { severity: { from: 'sev2', to: 'sev1' } },
      },
      {
        type: 'status_changed',
        data: { status: { from: 'open', to: 'investigating' } },
      },
    ]);
  });

  it('writes the events in a fixed order: title, severity, status, commander', () => {
    const types = changedFields(current, {
      commanderId: 'user-b',
      status: 'resolved',
      severity: 'sev4',
      title: 'Back up',
    }).map((event) => event.type);

    expect(types).toEqual([
      'title_changed',
      'severity_changed',
      'status_changed',
      'commander_changed',
    ]);
  });
});

describe('toTimelineEntry', () => {
  it('tells a status change as a status_change', () => {
    const entry = toTimelineEntry(
      stored('status_changed', {
        status: { from: 'open', to: 'investigating' },
      }),
    );

    expect(entry).toMatchObject({
      id: 'id-status_changed',
      seq: 7,
      incidentId: 'incident-1',
      kind: 'status_change',
      occurredAt: '2026-11-12T10:00:00.000Z',
      summary: 'Ada changed the status from open to investigating',
      author: ada,
      nodeIds: [],
      data: { kind: 'status_change', from: 'open', to: 'investigating' },
    });
  });

  it('tells a severity change as a severity_change', () => {
    const entry = toTimelineEntry(
      stored('severity_changed', { severity: { from: 'sev2', to: 'sev1' } }),
    );

    expect(entry.kind).toBe('severity_change');
    expect(entry.data).toEqual({
      kind: 'severity_change',
      from: 'sev2',
      to: 'sev1',
    });
  });

  it('tells a note as a note whose summary is the text', () => {
    const entry = toTimelineEntry(
      stored('note_added', { body: 'Restarted db' }),
    );

    expect(entry.kind).toBe('note');
    expect(entry.summary).toBe('Restarted db');
    expect(entry.data).toEqual({ kind: 'note' });
  });

  it('tells the other events as actions', () => {
    expect(toTimelineEntry(stored('created', {})).data).toEqual({
      kind: 'action',
      action: 'created',
    });
    expect(
      toTimelineEntry(
        stored('title_changed', { title: { from: 'a', to: 'b' } }),
      ).data,
    ).toEqual({ kind: 'action', action: 'title_changed' });
    expect(
      toTimelineEntry(
        stored('commander_changed', {
          commanderId: { from: 'user-a', to: 'user-b' },
        }),
      ).data,
    ).toEqual({ kind: 'action', action: 'role_changed', userId: 'user-b' });
  });

  it('refuses a type it does not know', () => {
    expect(() => toTimelineEntry(stored('deleted', {}))).toThrow(
      'Unknown incident event type: deleted',
    );
  });
});

describe('statusHistory and resolvedAt', () => {
  const events = [
    stored('created', { status: 'open' }, '2026-11-12T10:00:00Z'),
    stored('note_added', { body: 'hi' }, '2026-11-12T10:01:00Z'),
    stored(
      'status_changed',
      { status: { from: 'open', to: 'investigating' } },
      '2026-11-12T10:02:00Z',
    ),
    stored(
      'status_changed',
      { status: { from: 'investigating', to: 'resolved' } },
      '2026-11-12T10:03:00Z',
    ),
    stored(
      'status_changed',
      { status: { from: 'resolved', to: 'open' } },
      '2026-11-12T10:04:00Z',
    ),
    stored(
      'status_changed',
      { status: { from: 'open', to: 'resolved' } },
      '2026-11-12T10:05:00Z',
    ),
  ];

  it('lists the statuses in order, from the created event and the changes', () => {
    expect(statusHistory(events).map((entry) => entry.status)).toEqual([
      'open',
      'investigating',
      'resolved',
      'open',
      'resolved',
    ]);
  });

  it('gives the date of the last change to resolved while it is resolved', () => {
    expect(resolvedAt('resolved', statusHistory(events))).toBe(
      '2026-11-12T10:05:00.000Z',
    );
  });

  it('gives null when the history does not end on resolved', () => {
    // A read that saw `resolved` while a reopen was being written.
    expect(
      resolvedAt('resolved', statusHistory(events.slice(0, 5))),
    ).toBeNull();
  });

  it('gives null when the incident is not resolved, even if it was once', () => {
    expect(resolvedAt('open', statusHistory(events.slice(0, 5)))).toBeNull();
  });
});
