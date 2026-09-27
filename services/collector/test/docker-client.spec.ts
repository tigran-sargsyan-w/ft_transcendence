import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest';

import { DockerClient } from '../src/docker-client.js';

function jsonResponse(
  body: unknown,
  status = 200,
): Response {
  return new Response(
    JSON.stringify(body),
    {
      status,
      headers: {
        'Content-Type': 'application/json',
      },
    },
  );
}

const dockerVersion = {
  Version: '28.0.0',
  ApiVersion: '1.48',
  MinAPIVersion: '1.24',
};

describe('DockerClient', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('returns true when Docker ping responds OK', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response('OK\n', {
        status: 200,
      }),
    );

    const client = new DockerClient(
      'http://docker-proxy:2375/',
    );

    await expect(
      client.ping(),
    ).resolves.toBe(true);

    expect(fetchMock).toHaveBeenCalledWith(
      'http://docker-proxy:2375/_ping',
    );
  });

  it('returns false when Docker ping returns a non-success status', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response('error', {
        status: 503,
      }),
    );

    const client = new DockerClient(
      'http://docker-proxy:2375',
    );

    await expect(
      client.ping(),
    ).resolves.toBe(false);
  });

  it('returns false when Docker ping cannot connect', async () => {
    fetchMock.mockRejectedValueOnce(
      new Error('connection refused'),
    );

    const client = new DockerClient(
      'http://docker-proxy:2375',
    );

    await expect(
      client.ping(),
    ).resolves.toBe(false);
  });

  it('loads Docker version without an API version prefix', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(dockerVersion),
    );

    const client = new DockerClient(
      'http://docker-proxy:2375',
    );

    const result =
      await client.version();

    expect(result).toEqual(
      dockerVersion,
    );

    expect(fetchMock).toHaveBeenCalledWith(
      'http://docker-proxy:2375/version',
    );
  });

  it('discovers the API version before the first versioned request', async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(dockerVersion),
      )
      .mockResolvedValueOnce(
        jsonResponse([
          {
            Id: 'container-1',
          },
        ]),
      );

    const client = new DockerClient(
      'http://docker-proxy:2375',
    );

    const containers =
      await client.listContainers();

    expect(containers).toEqual([
      {
        Id: 'container-1',
      },
    ]);

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://docker-proxy:2375/version',
    );

    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://docker-proxy:2375/v1.48/containers/json?all=1',
    );
  });

  it('reuses the discovered API version for later requests', async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(dockerVersion),
      )
      .mockResolvedValueOnce(
        jsonResponse([]),
      )
      .mockResolvedValueOnce(
        jsonResponse([]),
      );

    const client = new DockerClient(
      'http://docker-proxy:2375',
    );

    await client.listContainers();
    await client.listNetworks();

    expect(fetchMock).toHaveBeenCalledTimes(
      3,
    );

    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      'http://docker-proxy:2375/v1.48/networks',
    );
  });

  it('encodes container identifiers in inspect URLs', async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(dockerVersion),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          Id: 'container/id one',
        }),
      );

    const client = new DockerClient(
      'http://docker-proxy:2375',
    );

    await client.inspectContainer(
      'container/id one',
    );

    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://docker-proxy:2375/v1.48/containers/container%2Fid%20one/json',
    );
  });

  it('returns an empty volume list when Docker returns null', async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(dockerVersion),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          Volumes: null,
        }),
      );

    const client = new DockerClient(
      'http://docker-proxy:2375',
    );

    await expect(
      client.listVolumes(),
    ).resolves.toEqual([]);
  });

  it('throws a normalized error when a Docker API request cannot connect', async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(dockerVersion),
      )
      .mockRejectedValueOnce(
        new Error('ECONNREFUSED'),
      );

    const client = new DockerClient(
      'http://docker-proxy:2375',
    );

    await expect(
      client.listNetworks(),
    ).rejects.toThrow(
      'Docker Engine is unavailable',
    );
  });

  it('throws when Docker API returns an error status', async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(dockerVersion),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          {
            message: 'failure',
          },
          500,
        ),
      );

    const client = new DockerClient(
      'http://docker-proxy:2375',
    );

    await expect(
      client.listNetworks(),
    ).rejects.toThrow(
      'Docker API returned HTTP 500',
    );
  });

  it('opens the Docker event stream', async () => {
    const stream =
      new ReadableStream<Uint8Array>();

    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(dockerVersion),
      )
      .mockResolvedValueOnce(
        new Response(stream, {
          status: 200,
        }),
      );

    const client = new DockerClient(
      'http://docker-proxy:2375',
    );

    const result =
      await client.eventStream();

    expect(result).toBe(stream);

    const [
      eventUrl,
      eventOptions,
    ] = fetchMock.mock.calls[1] ?? [];

    expect(String(eventUrl)).toContain(
      'http://docker-proxy:2375/v1.48/events?filters=',
    );

    expect(eventOptions).toEqual({
      headers: {
        Accept:
          'application/x-ndjson',
      },
    });
  });

  it('rejects a failed Docker event-stream request', async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(dockerVersion),
      )
      .mockResolvedValueOnce(
        new Response(null, {
          status: 503,
        }),
      );

    const client = new DockerClient(
      'http://docker-proxy:2375',
    );

    await expect(
      client.eventStream(),
    ).rejects.toThrow(
      'Docker events request failed with HTTP 503',
    );
  });

  it('rejects an event-stream response without a body', async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(dockerVersion),
      )
      .mockResolvedValueOnce(
        new Response(null, {
          status: 200,
        }),
      );

    const client = new DockerClient(
      'http://docker-proxy:2375',
    );

    await expect(
      client.eventStream(),
    ).rejects.toThrow(
      'Docker events response has no body',
    );
  });
});