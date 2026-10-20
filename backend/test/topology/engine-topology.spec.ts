import {
  toEngineTopology,
  topologyRevisionOf,
} from '../../src/modules/topology/engine-topology.js';
import { TopologyState } from '../../src/modules/topology/topology-state.js';
import {
  ENVIRONMENT_ID,
  STREAM_ID,
  eventsCrashAndRecreate,
  snapshotAfterUp,
} from './fixtures.js';

function loaded(epoch = 'epoch-1') {
  const state = new TopologyState(epoch);

  state.loadSnapshot(snapshotAfterUp);

  return state;
}

describe('toEngineTopology', () => {
  it('has exactly the fields the Graph Engine reads, once serialized', () => {
    const sent = JSON.parse(
      JSON.stringify(toEngineTopology(loaded().snapshot()!)),
    );

    // No `epoch`, no `revision`: the engine would drop them without a word.
    expect(Object.keys(sent).sort()).toEqual([
      'capturedAt',
      'edges',
      'environmentId',
      'nodes',
      'schemaVersion',
      'topologyRevision',
    ]);
  });

  it('sends the revision as one string, epoch first', () => {
    const { topologyRevision } = toEngineTopology(loaded().snapshot()!);

    expect(topologyRevision).toBe('epoch-1:1');
    expect(typeof topologyRevision).toBe('string');
  });

  it('keeps the graph and the contract fields as they are', () => {
    const snapshot = loaded().snapshot()!;
    const sent = toEngineTopology(snapshot);

    expect(sent.schemaVersion).toBe(1);
    expect(sent.environmentId).toBe(ENVIRONMENT_ID);
    expect(sent.capturedAt).toBe(snapshot.capturedAt);
    expect(sent.nodes).toEqual(snapshot.nodes);
    expect(sent.edges).toEqual(snapshot.edges);
  });

  it('tells two lives of Nest apart, even at the same revision', () => {
    const before = toEngineTopology(loaded('epoch-1').snapshot()!);
    const after = toEngineTopology(loaded('epoch-2').snapshot()!);

    expect(before.topologyRevision).not.toBe(after.topologyRevision);
  });

  it('changes with each revision, and a delta names the same value', () => {
    const state = loaded();
    const first = toEngineTopology(state.snapshot()!).topologyRevision;
    const result = state.applyBatch({
      streamId: STREAM_ID,
      events: eventsCrashAndRecreate.slice(0, 2),
    });

    if (result.status !== 'applied' || !result.delta) {
      throw new Error('the crash should have changed the topology');
    }

    const second = toEngineTopology(state.snapshot()!).topologyRevision;

    expect(second).toBe('epoch-1:2');
    expect(second).not.toBe(first);
    // A stale check holding the delta compares like with like.
    expect(topologyRevisionOf(result.delta)).toBe(second);
  });
});
