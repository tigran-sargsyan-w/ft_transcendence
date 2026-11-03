import {
  type IncomingMessage,
  type Server,
  type ServerResponse,
  createServer,
} from 'node:http';
import type { AddressInfo } from 'node:net';
import {
  GraphEngineError,
  HttpGraphEngineClient,
} from '../../src/modules/analysis/graph-engine-client.js';
import type { EngineTopology } from '../../src/modules/topology/graph.types.js';

type Handler = (request: IncomingMessage, response: ServerResponse) => void;

function reply(response: ServerResponse, status: number, body: unknown) {
  response.writeHead(status, { 'content-type': 'application/json' });
  response.end(typeof body === 'string' ? body : JSON.stringify(body));
}

const topology: EngineTopology = {
  schemaVersion: 1,
  environmentId: 'env_test',
  capturedAt: '2026-11-02T10:00:00.000Z',
  topologyRevision: 'epoch:3',
  nodes: [],
  edges: [],
};

const request = {
  topology,
  analyses: ['blast_radius'],
  options: { seedNodeIds: ['a'] },
};

const data = {
  schemaVersion: 1,
  environmentId: 'env_test',
  topologyRevision: 'epoch:3',
  analyzedAt: '2026-11-02T10:00:01Z',
  results: { blastRadius: { seedNodeIds: ['a'] } },
};

describe('HttpGraphEngineClient', () => {
  let server: Server;
  let handler: Handler;
  let baseUrl: string;
  const received: {
    method?: string;
    url?: string;
    type?: string;
    body?: string;
  }[] = [];

  beforeAll(async () => {
    server = createServer((incoming, response) => {
      let body = '';

      incoming.on('data', (chunk) => (body += chunk));
      incoming.on('end', () => {
        received.push({
          method: incoming.method,
          url: incoming.url,
          type: incoming.headers['content-type'],
          body,
        });
        handler(incoming, response);
      });
    });

    await new Promise<void>((resolve) =>
      server.listen(0, '127.0.0.1', resolve),
    );

    baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });

  afterAll(async () => {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  });

  beforeEach(() => {
    received.length = 0;
  });

  it('posts the request to the analyze endpoint and returns its data', async () => {
    handler = (_request, response) => reply(response, 200, { data });

    const client = new HttpGraphEngineClient(`${baseUrl}/`);

    expect(await client.analyze(request)).toEqual(data);
    expect(received).toHaveLength(1);
    expect(received[0].method).toBe('POST');
    expect(received[0].url).toBe('/api/v1/analyze');
    expect(received[0].type).toBe('application/json');
    expect(JSON.parse(received[0].body ?? '')).toEqual(request);
  });

  it('accepts an answer without a revision echo', async () => {
    const { topologyRevision: _revision, ...withoutRevision } = data;

    handler = (_request, response) =>
      reply(response, 200, { data: withoutRevision });

    const result = await new HttpGraphEngineClient(baseUrl).analyze(request);

    expect(result.topologyRevision).toBeUndefined();
  });

  it('turns an error of the engine into a GraphEngineError', async () => {
    handler = (_request, response) =>
      reply(response, 400, {
        error: {
          code: 'GRAPH_INVALID_TOPOLOGY',
          message: 'Unknown seed node id: a',
        },
      });

    const failure = await new HttpGraphEngineClient(baseUrl)
      .analyze(request)
      .catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(GraphEngineError);
    expect(failure).toMatchObject({
      status: 400,
      code: 'GRAPH_INVALID_TOPOLOGY',
      message: 'Unknown seed node id: a',
    });
  });

  it('keeps the status of a failure of the engine', async () => {
    handler = (_request, response) =>
      reply(response, 500, {
        error: { code: 'GRAPH_ENGINE_FAILURE', message: 'boom' },
      });

    await expect(
      new HttpGraphEngineClient(baseUrl).analyze(request),
    ).rejects.toMatchObject({ status: 500, code: 'GRAPH_ENGINE_FAILURE' });
  });

  it('fails with a plain error when an error has no contract body', async () => {
    handler = (_request, response) =>
      reply(response, 500, 'Internal Server Error');

    const failure = await new HttpGraphEngineClient(baseUrl)
      .analyze(request)
      .catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(Error);
    expect(failure).not.toBeInstanceOf(GraphEngineError);
  });

  it.each([
    ['no data', {}],
    ['no results', { data: { ...data, results: undefined } }],
    ['another schema version', { data: { ...data, schemaVersion: 2 } }],
    [
      'a revision that is not a string',
      { data: { ...data, topologyRevision: 12 } },
    ],
  ])('rejects an answer with %s', async (_name, body) => {
    handler = (_request, response) => reply(response, 200, body);

    await expect(
      new HttpGraphEngineClient(baseUrl).analyze(request),
    ).rejects.toThrow('malformed');
  });

  it('rejects an answer that is not JSON', async () => {
    handler = (_request, response) => reply(response, 200, '<html></html>');

    await expect(
      new HttpGraphEngineClient(baseUrl).analyze(request),
    ).rejects.toThrow('malformed');
  });

  it('gives up when the engine does not answer in time', async () => {
    handler = () => {
      // Never answers.
    };

    await expect(
      new HttpGraphEngineClient(baseUrl, 50).analyze(request),
    ).rejects.toThrow();
  });

  it('fails when nothing listens', async () => {
    const closed = createServer();

    await new Promise<void>((resolve) =>
      closed.listen(0, '127.0.0.1', resolve),
    );

    const url = `http://127.0.0.1:${(closed.address() as AddressInfo).port}`;

    await new Promise((resolve) => closed.close(resolve));

    await expect(
      new HttpGraphEngineClient(url).analyze(request),
    ).rejects.toThrow();
  });
});
