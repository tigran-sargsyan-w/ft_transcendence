// The rules of the incident timeline (docs/adr/0004, docs/architecture/
// incidents-api.md). Nothing here touches the database.

export const STATUSES = ['open', 'investigating', 'resolved'] as const;
export const SEVERITIES = ['sev1', 'sev2', 'sev3', 'sev4'] as const;
// The kinds of the contract. In v1 no entry is an `infra_event` or an
// `analysis`: filtering on them gives an empty timeline.
export const TIMELINE_KINDS = [
  'infra_event',
  'status_change',
  'severity_change',
  'analysis',
  'action',
  'note',
] as const;

export type UserRef = {
  id: string;
  displayName: string;
  avatarUrl: string | null;
};

// The fields of an incident that an update can change.
export type EditableFields = {
  title: string;
  severity: string;
  status: string;
  commanderId: string;
};

type Change = { from: string; to: string };

// `data` is `{ "<field>": { from, to } }`.
export type NewEvent = { type: string; data: Record<string, Change> };

// The event of each field, in the order they are written.
const EVENT_OF_FIELD: Record<keyof EditableFields, string> = {
  title: 'title_changed',
  severity: 'severity_changed',
  status: 'status_changed',
  commanderId: 'commander_changed',
};

// One event for each field whose value really changes: sending the current
// value changes nothing.
export function changedFields(
  current: EditableFields,
  wanted: Partial<EditableFields>,
): NewEvent[] {
  const events: NewEvent[] = [];

  for (const field of Object.keys(EVENT_OF_FIELD) as (keyof EditableFields)[]) {
    const to = wanted[field];

    if (to !== undefined && to !== current[field]) {
      events.push({
        type: EVENT_OF_FIELD[field],
        data: { [field]: { from: current[field], to } },
      });
    }
  }

  return events;
}

// What a stored event gives back: the author is already a UserRef.
export type StoredEvent = {
  id: string;
  seq: number;
  incidentId: string;
  type: string;
  data: unknown;
  createdAt: Date;
  actor: UserRef;
};

const changeOf = (data: unknown, field: string) =>
  (data as Record<string, Change>)[field];

// How an event is told to the screens: its `kind` and what `data` holds
// depend on its type. `created`, `title_changed` and `commander_changed` are
// all `action`; the screens fall back on `summary` for the ones they do not
// know.
function describe(event: StoredEvent) {
  const who = event.actor.displayName;

  switch (event.type) {
    case 'status_changed': {
      const { from, to } = changeOf(event.data, 'status');

      return {
        kind: 'status_change',
        summary: `${who} changed the status from ${from} to ${to}`,
        data: { kind: 'status_change', from, to },
      };
    }
    case 'severity_changed': {
      const { from, to } = changeOf(event.data, 'severity');

      return {
        kind: 'severity_change',
        summary: `${who} changed the severity from ${from} to ${to}`,
        data: { kind: 'severity_change', from, to },
      };
    }
    case 'note_added':
      return {
        kind: 'note',
        summary: (event.data as { body: string }).body,
        data: { kind: 'note' },
      };
    case 'commander_changed':
      return {
        kind: 'action',
        summary: `${who} changed the commander`,
        data: {
          kind: 'action',
          action: 'role_changed',
          userId: changeOf(event.data, 'commanderId').to,
        },
      };
    case 'title_changed':
      return {
        kind: 'action',
        summary: `${who} renamed the incident to "${changeOf(event.data, 'title').to}"`,
        data: { kind: 'action', action: 'title_changed' },
      };
    case 'created':
      return {
        kind: 'action',
        summary: `${who} opened the incident`,
        data: { kind: 'action', action: 'created' },
      };
    default:
      // Only the types above are written.
      throw new Error(`Unknown incident event type: ${event.type}`);
  }
}

export function toTimelineEntry(event: StoredEvent) {
  return {
    id: event.id,
    seq: event.seq,
    incidentId: event.incidentId,
    occurredAt: event.createdAt.toISOString(),
    author: event.actor,
    // The events of v1 concern the incident, not a node.
    nodeIds: [] as string[],
    ...describe(event),
  };
}

// The statuses the incident went through, oldest first: the first one is in
// the `created` event, the next ones are the status changes. `events` are in
// timeline order.
export function statusHistory(
  events: Pick<StoredEvent, 'type' | 'data' | 'createdAt'>[],
) {
  const history: { status: string; at: string }[] = [];

  for (const event of events) {
    if (event.type === 'created') {
      history.push({
        status: (event.data as { status: string }).status,
        at: event.createdAt.toISOString(),
      });
    } else if (event.type === 'status_changed') {
      history.push({
        status: changeOf(event.data, 'status').to,
        at: event.createdAt.toISOString(),
      });
    }
  }

  return history;
}

// The date of the last change to `resolved`, while the incident is resolved:
// the history ends on the current status.
export function resolvedAt(
  status: string,
  history: { status: string; at: string }[],
) {
  return status === 'resolved' ? (history.at(-1)?.at ?? null) : null;
}
