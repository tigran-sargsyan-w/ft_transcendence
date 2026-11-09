import { buildGraph } from '../../src/modules/topology/build-graph.js';
import { diffGraph } from '../../src/modules/topology/diff-graph.js';
import {
  ENVIRONMENT_ID,
  api,
  apiRecreated,
  db,
  snapshotAfterRecreate,
  snapshotAfterUp,
  web,
} from './fixtures.js';
import { applyOperations } from './helpers.js';

const empty = buildGraph(ENVIRONMENT_ID, []);
const up = buildGraph(ENVIRONMENT_ID, snapshotAfterUp.containers);
const recreated = buildGraph(ENVIRONMENT_ID, snapshotAfterRecreate.containers);

describe('diffGraph', () => {
  it('finds nothing between two equal graphs', () => {
    expect(
      diffGraph(up, buildGraph(ENVIRONMENT_ID, [web(), api(), db()])),
    ).toEqual([]);
  });

  it('upserts the one node whose status changed', () => {
    const crashed = buildGraph(ENVIRONMENT_ID, [
      db(),
      api({ state: 'exited' }),
      web(),
    ]);
    const operations = diffGraph(up, crashed);

    expect(operations).toEqual([
      {
        op: 'node.upsert',
        node: expect.objectContaining({ id: 'svc:demo:api', status: 'down' }),
      },
    ]);
  });

  it('upserts an edge whose metadata changed', () => {
    const changed = buildGraph(ENVIRONMENT_ID, [
      db(),
      api(),
      web({
        ports: [
          {
            containerPort: 80,
            protocol: 'tcp',
            hostIp: '0.0.0.0',
            hostPort: 9090,
          },
        ],
      }),
    ]);

    expect(diffGraph(up, changed).map((operation) => operation.op)).toEqual([
      'node.upsert',
      'edge.upsert',
    ]);
  });

  it('keeps the same node id when a container is recreated', () => {
    const operations = diffGraph(up, recreated);

    expect(operations).not.toContainEqual(
      expect.objectContaining({ op: 'node.remove', id: 'svc:demo:api' }),
    );
  });

  it('removes edges before nodes, and adds nodes before edges', () => {
    const adding = diffGraph(empty, up).map((operation) => operation.op);
    const removing = diffGraph(up, empty).map((operation) => operation.op);

    expect(adding.lastIndexOf('node.upsert')).toBeLessThan(
      adding.indexOf('edge.upsert'),
    );
    expect(removing.lastIndexOf('edge.remove')).toBeLessThan(
      removing.indexOf('node.remove'),
    );
  });

  it('turns any graph into the next one when applied', () => {
    const graphs = [
      empty,
      up,
      recreated,
      buildGraph(ENVIRONMENT_ID, [db()]),
      buildGraph(ENVIRONMENT_ID, [apiRecreated({ state: 'exited' }), web()]),
      empty,
    ];

    for (const from of graphs) {
      for (const to of graphs) {
        expect(applyOperations(from, diffGraph(from, to))).toEqual(to);
      }
    }
  });
});
