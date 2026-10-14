import { buildGraph } from '../../src/modules/topology/build-graph.js';
import type { Graph } from '../../src/modules/topology/graph.types.js';
import {
  ENVIRONMENT_ID,
  api,
  apiRecreated,
  container,
  db,
  snapshotAfterUp,
  web,
} from './fixtures.js';

const ids = (items: { id: string }[]) => items.map((item) => item.id);

const node = (graph: Graph, id: string) =>
  graph.nodes.find((candidate) => candidate.id === id);

describe('buildGraph', () => {
  it('builds the demo project as services, a network, a volume, a host and the outside', () => {
    const graph = buildGraph(ENVIRONMENT_ID, snapshotAfterUp.containers);

    expect(ids(graph.nodes)).toEqual([
      'ext:internet',
      'host:env_local_compose',
      'net:demo_default',
      'svc:demo:api',
      'svc:demo:db',
      'svc:demo:web',
      'vol:demo_db_data',
    ]);
    expect(ids(graph.edges)).toEqual([
      'connects_to:svc:demo:api>net:demo_default',
      'connects_to:svc:demo:db>net:demo_default',
      'connects_to:svc:demo:web>net:demo_default',
      'depends_on:svc:demo:api>svc:demo:db',
      'depends_on:svc:demo:web>svc:demo:api',
      'exposes:ext:internet>svc:demo:web',
      'mounts:svc:demo:db>vol:demo_db_data',
      'runs_on:svc:demo:api>host:env_local_compose',
      'runs_on:svc:demo:db>host:env_local_compose',
      'runs_on:svc:demo:web>host:env_local_compose',
    ]);
  });

  it('points every edge from the one that depends to its dependency', () => {
    const graph = buildGraph(ENVIRONMENT_ID, snapshotAfterUp.containers);
    const edge = (id: string) => graph.edges.find((item) => item.id === id);

    expect(edge('depends_on:svc:demo:api>svc:demo:db')).toMatchObject({
      source: 'svc:demo:api',
      target: 'svc:demo:db',
    });
    expect(edge('exposes:ext:internet>svc:demo:web')).toMatchObject({
      source: 'ext:internet',
      target: 'svc:demo:web',
    });
  });

  it('labels a service by its Compose name and keeps its details', () => {
    const graph = buildGraph(ENVIRONMENT_ID, snapshotAfterUp.containers);

    expect(node(graph, 'svc:demo:web')).toEqual({
      id: 'svc:demo:web',
      kind: 'service',
      label: 'web',
      status: 'healthy',
      metadata: {
        image: 'busybox:1.37',
        composeProject: 'demo',
        composeService: 'web',
        ports: [
          {
            containerPort: 80,
            protocol: 'tcp',
            hostPort: 8080,
            loopbackOnly: false,
          },
        ],
      },
    });
  });

  it('produces the same graph whatever the order of the containers', () => {
    const forward = buildGraph(ENVIRONMENT_ID, [db(), api(), web()]);
    const backward = buildGraph(ENVIRONMENT_ID, [web(), api(), db()]);

    expect(backward).toEqual(forward);
  });

  it('is empty without containers: no host, no outside', () => {
    expect(buildGraph(ENVIRONMENT_ID, [])).toEqual({ nodes: [], edges: [] });
  });

  it('keeps one service node while Compose recreates its container', () => {
    const graph = buildGraph(ENVIRONMENT_ID, [
      db(),
      api({ state: 'exited' }),
      apiRecreated({ name: 'tmp_demo-api-1', state: 'created' }),
      web(),
    ]);

    expect(ids(graph.nodes).filter((id) => id === 'svc:demo:api')).toHaveLength(
      1,
    );
    expect(node(graph, 'svc:demo:api')?.status).toBe('unknown');
  });

  it('shows the best status when several instances run', () => {
    const graph = buildGraph(ENVIRONMENT_ID, [
      db(),
      api({ id: 'a1', name: 'demo-api-1', state: 'exited' }),
      api({ id: 'a2', name: 'demo-api-2', health: 'healthy' }),
    ]);

    expect(node(graph, 'svc:demo:api')?.status).toBe('healthy');
  });

  it('gives a container outside Compose its own service node', () => {
    const graph = buildGraph(ENVIRONMENT_ID, [
      container({ id: 'c1', name: 'redis' }),
    ]);

    expect(node(graph, 'ctr:redis')).toMatchObject({
      kind: 'service',
      label: 'redis',
      status: 'healthy',
    });
    expect(ids(graph.edges)).toEqual([
      'runs_on:ctr:redis>host:env_local_compose',
    ]);
  });

  it('draws no depends_on edge toward a service that does not exist yet', () => {
    const graph = buildGraph(ENVIRONMENT_ID, [api()]);

    expect(ids(graph.edges)).not.toContainEqual(
      expect.stringContaining('depends_on'),
    );
  });

  it('never leaves an orphan edge', () => {
    for (const containers of [
      [],
      [api()],
      [web(), api()],
      [db(), api(), web()],
    ]) {
      const graph = buildGraph(ENVIRONMENT_ID, containers);
      const known = new Set(ids(graph.nodes));

      for (const edge of graph.edges) {
        expect(known).toContain(edge.source);
        expect(known).toContain(edge.target);
      }
    }
  });

  it('ignores a bind mount: its source is redacted, so it has no identity', () => {
    const graph = buildGraph(ENVIRONMENT_ID, [
      container({
        id: 'c1',
        name: 'proxy',
        mounts: [
          {
            type: 'bind',
            source: '[redacted]',
            destination: '/etc/nginx/conf.d/default.conf',
            readOnly: true,
          },
        ],
      }),
    ]);

    expect(graph.nodes.filter((item) => item.kind === 'volume')).toEqual([]);
  });

  it('marks a volume read-only only when every mount of it is', () => {
    const mount = (readOnly: boolean) => ({
      type: 'volume',
      source: 'shared',
      destination: '/shared',
      readOnly,
    });
    const edgeOf = (readOnly: boolean[]) =>
      buildGraph(
        ENVIRONMENT_ID,
        readOnly.map((value, index) =>
          container({
            id: `c${index}`,
            name: `app-${index}`,
            compose: { project: 'p', service: 's', dependsOn: [] },
            mounts: [mount(value)],
          }),
        ),
      ).edges.find((edge) => edge.kind === 'mounts');

    expect(edgeOf([true, true])?.metadata).toEqual({ readOnly: true });
    expect(edgeOf([true, false])?.metadata).toEqual({ readOnly: false });
  });

  describe('published ports', () => {
    const published = (hostIps: string[]) =>
      buildGraph(ENVIRONMENT_ID, [
        container({
          id: 'c1',
          name: 'nginx',
          ports: hostIps.map((hostIp) => ({
            containerPort: 443,
            protocol: 'tcp',
            hostIp,
            hostPort: 8443,
          })),
        }),
      ]);

    it('counts a port bound on IPv4 and IPv6 once', () => {
      const graph = published(['0.0.0.0', '::']);
      const exposes = graph.edges.filter((edge) => edge.kind === 'exposes');

      expect(exposes).toHaveLength(1);
      expect(exposes[0].metadata).toEqual({
        ports: [{ containerPort: 443, protocol: 'tcp', hostPort: 8443 }],
      });
      expect(node(graph, 'ctr:nginx')?.metadata?.ports).toHaveLength(1);
    });

    it('does not expose a port bound to loopback', () => {
      const graph = published(['127.0.0.1']);

      expect(graph.edges.filter((edge) => edge.kind === 'exposes')).toEqual([]);
      expect(node(graph, 'ext:internet')).toBeUndefined();
      expect(node(graph, 'ctr:nginx')?.metadata?.ports).toEqual([
        {
          containerPort: 443,
          protocol: 'tcp',
          hostPort: 8443,
          loopbackOnly: true,
        },
      ]);
    });

    it('exposes a port as soon as one of its bindings is not loopback', () => {
      const graph = published(['127.0.0.1', '0.0.0.0']);

      expect(
        graph.edges.filter((edge) => edge.kind === 'exposes'),
      ).toHaveLength(1);
    });
  });
});
