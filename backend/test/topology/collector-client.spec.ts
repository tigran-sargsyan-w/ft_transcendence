import {
  type IncomingMessage,
  type Server,
  type ServerResponse,
  createServer,
} from 'node:http';
import type { AddressInfo } from 'node:net';
import {
  HttpCollectorClient,
  SequenceExpiredError,
} from '../../src/modules/topology/collector-client.js';
import { eventsUp, snapshotAfterUp, STREAM_ID } from './fixtures.js';

type Handler = (request: IncomingMessage, response: ServerResponse) => void;

function reply(response: ServerResponse, status: number, body: unknown) {
  response.writeHead(status, { 'content-type': 'application/json' });
  response.end(typeof body === 'string' ? body : JSON.stringify(body));
}

describe('HttpCollectorClient', () => {
  let server: Server;
  let handler: Handler;
  let baseUrl: string;
  const requested: string[] = [];
  const signal = new AbortController().signal;

  beforeAll(async () => {
    server = createServer((request, response) => {
      requested.push(request.url ?? '');
      handler(request, response);
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
    requested.length = 0;
  });

  it('reads the snapshot from the collector API', async () => {
    handler = (_request, response) =>
      reply(response, 200, { data: snapshotAfterUp });

    const client = new HttpCollectorClient(baseUrl);

    expect(await client.snapshot(signal)).toEqual(snapshotAfterUp);
    expect(requested).toEqual(['/api/v1/snapshot']);
  });

  it('asks for the events after a sequence', async () => {
    const batch = { streamId: STREAM_ID, events: eventsUp.slice(0, 2) };

    handler = (_request, response) => reply(response, 200, { data: batch });

    const client = new HttpCollectorClient(baseUrl);

    expect(await client.events(12, signal)).toEqual(batch);
    expect(requested).toEqual(['/api/v1/events?after=12']);
  });

  it('copes with a trailing slash in the base URL', async () => {
    handler = (_request, response) =>
      reply(response, 200, { data: snapshotAfterUp });

    await new HttpCollectorClient(`${baseUrl}/`).snapshot(signal);

    expect(requested).toEqual(['/api/v1/snapshot']);
  });

  it('turns a COLLECTOR_SEQUENCE_EXPIRED answer into its own error', async () => {
    handler = (_request, response) =>
      reply(response, 409, {
        error: { code: 'COLLECTOR_SEQUENCE_EXPIRED', message: 'gone' },
      });

    await expect(
      new HttpCollectorClient(baseUrl).events(500, signal),
    ).rejects.toBeInstanceOf(SequenceExpiredError);
  });

  it('does not take any other failure for an expired sequence', async () => {
    const client = new HttpCollectorClient(baseUrl);

    handler = (_request, response) =>
      reply(response, 503, {
        error: { code: 'COLLECTOR_DOCKER_UNAVAILABLE', message: 'no docker' },
      });

    const unavailable = client.snapshot(signal);

    await expect(unavailable).rejects.toThrow('HTTP 503');
    await expect(unavailable).rejects.not.toBeInstanceOf(SequenceExpiredError);

    handler = (_request, response) =>
      reply(response, 409, { error: { code: 'OTHER', message: 'x' } });

    const conflict = client.events(1, signal);

    await expect(conflict).rejects.toThrow('HTTP 409');
    await expect(conflict).rejects.not.toBeInstanceOf(SequenceExpiredError);
  });

  it('refuses an answer that is not the expected shape', async () => {
    const client = new HttpCollectorClient(baseUrl);

    handler = (_request, response) =>
      reply(response, 200, { data: { containers: [] } });
    await expect(client.snapshot(signal)).rejects.toThrow('malformed snapshot');

    handler = (_request, response) =>
      reply(response, 200, { data: { streamId: STREAM_ID } });
    await expect(client.events(0, signal)).rejects.toThrow('malformed batch');

    handler = (_request, response) => reply(response, 200, 'not json');
    await expect(client.snapshot(signal)).rejects.toThrow('HTTP 200');

    handler = (_request, response) => reply(response, 200, { nothing: true });
    await expect(client.snapshot(signal)).rejects.toThrow('HTTP 200');
  });

  it('fails when the collector cannot be reached', async () => {
    const client = new HttpCollectorClient('http://127.0.0.1:1');

    await expect(client.snapshot(signal)).rejects.toThrow();
  });

  it('gives up on a collector that does not answer in time', async () => {
    handler = () => {
      // Never answers.
    };

    const client = new HttpCollectorClient(baseUrl, 30, 30);

    await expect(client.events(0, signal)).rejects.toThrow();
  });

  it('stops waiting when asked to', async () => {
    handler = () => {
      // Never answers.
    };

    const controller = new AbortController();
    const pending = new HttpCollectorClient(baseUrl).events(
      0,
      controller.signal,
    );

    controller.abort();

    await expect(pending).rejects.toThrow();
  });
});
