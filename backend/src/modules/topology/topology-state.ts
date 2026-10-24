import { randomUUID } from 'node:crypto';
import { buildGraph } from './build-graph.js';
import type {
  CollectorContainer,
  CollectorEvent,
  CollectorSnapshot,
  EventBatch,
} from './collector.types.js';
import { diffGraph } from './diff-graph.js';
import type { Graph, TopologyDelta, TopologySnapshot } from './graph.types.js';
import {
  type ResyncReason,
  type StreamCursor,
  planBatch,
} from './stream-cursor.js';

export type BatchResult =
  | { status: 'applied'; delta: TopologyDelta | null }
  | { status: 'resync'; reason: ResyncReason };

// An event object is only trusted when it names its container.
function readContainer(data: Record<string, unknown>) {
  const { container } = data;

  if (typeof container !== 'object' || container === null) {
    return null;
  }

  return typeof (container as { id?: unknown }).id === 'string'
    ? (container as CollectorContainer)
    : null;
}

// The live topology of one environment, in memory, with no dependency on
// Nest. It keeps the containers the collector reports and derives the graph
// from them, so a delta is only ever the difference between two whole graphs.
//
// - `epoch` names this in-memory state (a restart gets another one), and
//   `revision` counts the batches it applied. Both are Nest's own: neither
//   follows the collector's `streamId` nor its `sequence`.
// - Whoever drives it loads a snapshot first, then feeds batches read from
//   `cursor`. A `resync` answer means: load a new snapshot. Until then the
//   state stays as it was, so nothing wrong is ever published.
export class TopologyState {
  private containers = new Map<string, CollectorContainer>();
  private graph: Graph = { nodes: [], edges: [] };
  private environmentId = '';
  private updatedAt = '';
  private position: StreamCursor | null = null;
  private count = 0;

  constructor(readonly epoch: string = randomUUID()) {}

  // 0 until the first snapshot is loaded, then 1, 2, ... and never back.
  get revision() {
    return this.count;
  }

  // Where to resume reading the collector from; null before a snapshot.
  get cursor(): StreamCursor | null {
    return this.position && { ...this.position };
  }

  snapshot(): TopologySnapshot | null {
    if (!this.position) {
      return null;
    }

    return {
      schemaVersion: 1,
      environmentId: this.environmentId,
      // The latest moment this state reflects.
      capturedAt: this.updatedAt,
      epoch: this.epoch,
      revision: this.count,
      nodes: [...this.graph.nodes],
      edges: [...this.graph.edges],
    };
  }

  // Replaces everything. A resynchronization is a new revision, never a
  // reset: clients that hold an older one simply adopt this snapshot.
  // All or nothing: the graph is built before anything is replaced, so a
  // snapshot that cannot be built leaves the state as it was.
  loadSnapshot(snapshot: CollectorSnapshot): TopologySnapshot {
    const containers = new Map(
      snapshot.containers.map((container) => [container.id, container]),
    );
    const graph = buildGraph(snapshot.environmentId, containers.values());

    this.containers = containers;
    this.graph = graph;
    this.environmentId = snapshot.environmentId;
    this.updatedAt = snapshot.capturedAt;
    this.position = {
      streamId: snapshot.streamId,
      sequence: snapshot.sequence,
    };
    this.count += 1;

    return this.snapshot()!;
  }

  // A batch read from `cursor`. `delta` is null when nothing changed (an
  // empty long poll, or events that had no effect): no revision is spent.
  applyBatch(batch: EventBatch): BatchResult {
    const plan = planBatch(this.position, batch);

    if (plan.action === 'resync') {
      return { status: 'resync', reason: plan.reason };
    }

    for (const event of plan.events) {
      this.apply(event);
    }

    const last = plan.events.at(-1);

    if (last) {
      this.position = { streamId: last.streamId, sequence: last.sequence };

      if (last.occurredAt > this.updatedAt) {
        this.updatedAt = last.occurredAt;
      }
    }

    const next = buildGraph(this.environmentId, this.containers.values());
    const operations = diffGraph(this.graph, next);

    this.graph = next;

    if (operations.length === 0) {
      return { status: 'applied', delta: null };
    }

    const baseRevision = this.count;

    this.count += 1;

    return {
      status: 'applied',
      delta: {
        epoch: this.epoch,
        baseRevision,
        revision: this.count,
        operations,
      },
    };
  }

  private apply(event: CollectorEvent) {
    switch (event.type) {
      case 'container.created':
      case 'container.started':
      case 'container.stopped':
      case 'container.health_changed':
      case 'container.died': {
        // The collector inspects the container after each of these events.
        // `container.died` can arrive without it (the container vanished
        // first): the last known state is kept, never read as "down".
        const container = readContainer(event.data);

        if (container) {
          this.containers.set(container.id, container);
        }

        break;
      }

      case 'container.destroyed':
        this.containers.delete(event.resource.id);
        break;

      default:
        // Network and volume events add nothing: the containers already
        // list their networks and mounts.
        break;
    }
  }
}
