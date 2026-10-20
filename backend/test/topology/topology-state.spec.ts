import { buildGraph } from '../../src/modules/topology/build-graph.js';
import type { CollectorEvent } from '../../src/modules/topology/collector.types.js';
import type { TopologySnapshot } from '../../src/modules/topology/graph.types.js';
import {
  type BatchResult,
  TopologyState,
} from '../../src/modules/topology/topology-state.js';
import {
  API_ID,
  DB_ID,
  ENVIRONMENT_ID,
  STREAM_ID,
  WEB_ID,
  emptySnapshot,
  event,
  eventsCrashAndRecreate,
  eventsUp,
  snapshotAfterRecreate,
  snapshotAfterUp,
} from './fixtures.js';
import { applyOperations } from './helpers.js';

const batch = (...events: CollectorEvent[]) => ({
  streamId: STREAM_ID,
  events,
});

// The delta of a batch that was applied (null: nothing changed).
function applied(result: BatchResult) {
  if (result.status !== 'applied') {
    throw new Error(`expected applied, got resync (${result.reason})`);
  }

  return result.delta;
}

const graphOf = (snapshot: TopologySnapshot) => ({
  nodes: snapshot.nodes,
  edges: snapshot.edges,
});

const expectedAfterUp = buildGraph(ENVIRONMENT_ID, snapshotAfterUp.containers);
const expectedAfterRecreate = buildGraph(
  ENVIRONMENT_ID,
  snapshotAfterRecreate.containers,
);

function stateAfterUp() {
  const state = new TopologyState('epoch-1');

  state.loadSnapshot(snapshotAfterUp);

  return state;
}

describe('TopologyState', () => {
  describe('loading a snapshot', () => {
    it('has nothing before a snapshot is loaded', () => {
      const state = new TopologyState('epoch-1');

      expect(state.snapshot()).toBeNull();
      expect(state.revision).toBe(0);
      expect(state.cursor).toBeNull();
    });

    it('exposes the graph with its epoch, revision and stream position', () => {
      const state = new TopologyState('epoch-1');
      const snapshot = state.loadSnapshot(snapshotAfterUp);

      expect(snapshot).toEqual({
        schemaVersion: 1,
        environmentId: ENVIRONMENT_ID,
        capturedAt: snapshotAfterUp.capturedAt,
        epoch: 'epoch-1',
        revision: 1,
        ...expectedAfterUp,
      });
      expect(state.snapshot()).toEqual(snapshot);
      expect(state.cursor).toEqual({ streamId: STREAM_ID, sequence: 12 });
    });

    it('takes the next revision on a resynchronization, never a reset', () => {
      const state = stateAfterUp();
      const second = state.loadSnapshot(snapshotAfterRecreate);

      expect(second.revision).toBe(2);
      expect(second.epoch).toBe('epoch-1');
      expect(graphOf(second)).toEqual(expectedAfterRecreate);
    });

    it('gives each state its own epoch by default', () => {
      expect(new TopologyState().epoch).not.toBe(new TopologyState().epoch);
    });

    it('hands out copies: changing a snapshot does not change the state', () => {
      const state = stateAfterUp();
      const snapshot = state.snapshot()!;

      snapshot.nodes.length = 0;

      expect(state.snapshot()!.nodes).toEqual(expectedAfterUp.nodes);
    });
  });

  describe('replaying the docker compose up', () => {
    it('reaches the snapshot taken after it, as one batch', () => {
      const state = new TopologyState('epoch-1');
      const start = state.loadSnapshot(emptySnapshot);
      const delta = applied(state.applyBatch(batch(...eventsUp)));

      expect(delta).not.toBeNull();
      expect(graphOf(state.snapshot()!)).toEqual(expectedAfterUp);
      expect(applyOperations(start, delta!.operations)).toEqual(
        expectedAfterUp,
      );
      expect(state.cursor).toEqual({ streamId: STREAM_ID, sequence: 12 });
    });

    it('reaches it as well one event at a time, with deltas that add up', () => {
      const state = new TopologyState('epoch-1');
      let client = state.loadSnapshot(emptySnapshot);
      const revisions = [client.revision];

      for (const next of eventsUp) {
        const delta = applied(state.applyBatch(batch(next)));

        if (delta) {
          // Each delta continues exactly where the client stands.
          expect(delta.epoch).toBe('epoch-1');
          expect(delta.baseRevision).toBe(client.revision);
          expect(delta.operations).not.toEqual([]);

          client = {
            ...client,
            ...applyOperations(client, delta.operations),
            revision: delta.revision,
          };
          revisions.push(delta.revision);
        }

        expect(graphOf(client)).toEqual(graphOf(state.snapshot()!));
      }

      expect(graphOf(client)).toEqual(expectedAfterUp);
      // Strictly increasing: a revision is never reused nor lowered.
      expect(revisions.every((r, i) => i === 0 || r > revisions[i - 1])).toBe(
        true,
      );
    });

    it('spends no revision on an event that changes nothing', () => {
      const state = stateAfterUp();
      const noop = event(
        13,
        '2026-09-23T15:38:50.000Z',
        'network.connected',
        { kind: 'network', id: 'e5b899c59c6a' },
        { containerId: API_ID },
      );

      expect(applied(state.applyBatch(batch(noop)))).toBeNull();
      expect(state.revision).toBe(1);
      expect(state.cursor).toEqual({ streamId: STREAM_ID, sequence: 13 });
    });

    it('keeps the later of the snapshot time and the events time', () => {
      const early = new TopologyState('epoch-1');

      early.loadSnapshot(emptySnapshot);
      early.applyBatch(batch(...eventsUp));

      expect(early.snapshot()!.capturedAt).toBe('2026-09-23T15:38:47.923Z');

      // The snapshot (15:38:47.960Z) is later than its last events.
      const late = new TopologyState('epoch-1');

      late.loadSnapshot({ ...snapshotAfterUp, sequence: 10 });
      late.applyBatch(batch(eventsUp[10], eventsUp[11]));

      expect(late.snapshot()!.capturedAt).toBe(snapshotAfterUp.capturedAt);
    });
  });

  describe('the crash and the recreation of a container', () => {
    it('keeps the service node, and ends on the recreated container', () => {
      const state = stateAfterUp();
      const statuses: string[] = [];

      for (const next of eventsCrashAndRecreate) {
        const delta = applied(state.applyBatch(batch(next)));

        for (const operation of delta?.operations ?? []) {
          expect(operation).not.toEqual(
            expect.objectContaining({ op: 'node.remove' }),
          );

          if (
            operation.op === 'node.upsert' &&
            operation.node.id === 'svc:demo:api'
          ) {
            statuses.push(operation.node.status);
          }
        }
      }

      // Crashed, then a new container exists but has not started, then up.
      expect(statuses).toEqual(['down', 'unknown', 'healthy']);
      expect(graphOf(state.snapshot()!)).toEqual(expectedAfterRecreate);
      expect(state.revision).toBe(4);
    });
  });

  describe('what it refuses to apply', () => {
    it('ignores events it already applied', () => {
      const state = stateAfterUp();
      const events = eventsCrashAndRecreate.slice(0, 2);

      expect(applied(state.applyBatch(batch(...events)))).not.toBeNull();
      expect(applied(state.applyBatch(batch(...events)))).toBeNull();
      expect(state.revision).toBe(2);
    });

    it('asks for a snapshot on a gap, and leaves the state as it was', () => {
      const state = stateAfterUp();
      const before = state.snapshot();
      const result = state.applyBatch(batch(eventsCrashAndRecreate[2]));

      expect(result).toEqual({ status: 'resync', reason: 'sequence_gap' });
      expect(state.snapshot()).toEqual(before);
      expect(state.cursor).toEqual({ streamId: STREAM_ID, sequence: 12 });
    });

    // A reset restarts the numbering, lower or higher than where we were.
    it.each([1, 13, 99])(
      'asks for a snapshot when the stream changed (sequence %i)',
      (sequence) => {
        const state = stateAfterUp();
        const before = state.snapshot();
        // Would remove the db if it were applied.
        const destroy = {
          ...event(
            sequence,
            '2026-09-23T16:00:00.000Z',
            'container.destroyed',
            { kind: 'container', id: DB_ID },
            {},
          ),
          streamId: 'stream_after_reset',
        };

        const result = state.applyBatch({
          streamId: 'stream_after_reset',
          events: [destroy],
        });

        expect(result).toEqual({ status: 'resync', reason: 'stream_changed' });
        expect(state.snapshot()).toEqual(before);
        expect(state.cursor).toEqual({ streamId: STREAM_ID, sequence: 12 });
      },
    );

    it('adopts the new stream once a snapshot of it is loaded', () => {
      const state = stateAfterUp();
      const snapshot = {
        ...snapshotAfterRecreate,
        streamId: 'stream_after_reset',
        sequence: 3,
      };

      state.loadSnapshot(snapshot);

      expect(state.cursor).toEqual({
        streamId: 'stream_after_reset',
        sequence: 3,
      });
      expect(state.revision).toBe(2);
    });

    it('asks for a snapshot before one was ever loaded', () => {
      const state = new TopologyState('epoch-1');

      expect(state.applyBatch(batch(eventsUp[0]))).toEqual({
        status: 'resync',
        reason: 'no_snapshot',
      });
    });
  });

  describe('removing a container for good', () => {
    const destroyed = (id: string) =>
      event(
        13,
        '2026-09-23T15:40:00.000Z',
        'container.destroyed',
        { kind: 'container', id },
        {},
      );

    it('removes the service, its edges and what only it was using', () => {
      const state = stateAfterUp();
      const delta = applied(state.applyBatch(batch(destroyed(WEB_ID))));

      // The web service was the only one published: the outside goes too.
      expect(delta!.operations).toEqual([
        { op: 'edge.remove', id: 'connects_to:svc:demo:web>net:demo_default' },
        { op: 'edge.remove', id: 'depends_on:svc:demo:web>svc:demo:api' },
        { op: 'edge.remove', id: 'exposes:ext:internet>svc:demo:web' },
        {
          op: 'edge.remove',
          id: 'runs_on:svc:demo:web>host:env_local_compose',
        },
        { op: 'node.remove', id: 'ext:internet' },
        { op: 'node.remove', id: 'svc:demo:web' },
      ]);
      expect(delta!.revision).toBe(2);
    });

    it('drops a volume that nobody mounts any more', () => {
      const state = stateAfterUp();
      const delta = applied(state.applyBatch(batch(destroyed(DB_ID))));

      expect(delta!.operations).toContainEqual({
        op: 'node.remove',
        id: 'vol:demo_db_data',
      });
      expect(state.snapshot()!.edges.map((edge) => edge.id)).not.toContainEqual(
        expect.stringContaining('svc:demo:db'),
      );
    });
  });

  describe('events that carry no usable container', () => {
    const died = (id: string, data: Record<string, unknown>) =>
      event(
        13,
        '2026-09-23T15:39:00.000Z',
        'container.died',
        { kind: 'container', id },
        data,
      );

    it('keeps the last known status of a container that died unseen', () => {
      const state = stateAfterUp();
      const delta = applied(
        state.applyBatch(batch(died(API_ID, { exitCode: 137 }))),
      );
      const api = state.snapshot()!.nodes.find((n) => n.id === 'svc:demo:api');

      // Never read as "down": the collector could not inspect it.
      expect(api?.status).toBe('healthy');
      expect(delta).toBeNull();
    });

    it('adds no node for a container it never heard of', () => {
      const state = stateAfterUp();
      const delta = applied(
        state.applyBatch(batch(died('ghost', { exitCode: 1 }))),
      );

      expect(delta).toBeNull();
      expect(graphOf(state.snapshot()!)).toEqual(expectedAfterUp);
    });

    it('ignores an event whose container has no id', () => {
      const state = stateAfterUp();
      const started = event(
        13,
        '2026-09-23T15:39:00.000Z',
        'container.started',
        { kind: 'container', id: 'c9' },
        { container: { name: 'nameless' } },
      );

      expect(applied(state.applyBatch(batch(started)))).toBeNull();
    });
  });

  describe('a snapshot that is ahead of its sequence', () => {
    it('lands on the same graph when the events it reflects arrive', () => {
      const state = new TopologyState('epoch-1');

      // Taken after event 12, but stamped with the checkpoint of event 10.
      state.loadSnapshot({ ...snapshotAfterUp, sequence: 10 });

      const delta = applied(
        state.applyBatch(batch(eventsUp[10], eventsUp[11])),
      );

      expect(delta).toBeNull();
      expect(graphOf(state.snapshot()!)).toEqual(expectedAfterUp);
      expect(state.cursor).toEqual({ streamId: STREAM_ID, sequence: 12 });
    });

    it('ends on the final state even when older objects are replayed', () => {
      const state = new TopologyState('epoch-1');

      // Events 15 to 18 replay objects older than this snapshot holds.
      state.loadSnapshot({ ...snapshotAfterRecreate, sequence: 14 });

      const delta = applied(
        state.applyBatch(batch(...eventsCrashAndRecreate.slice(2))),
      );

      expect(delta).toBeNull();
      expect(graphOf(state.snapshot()!)).toEqual(expectedAfterRecreate);
    });
  });
});
