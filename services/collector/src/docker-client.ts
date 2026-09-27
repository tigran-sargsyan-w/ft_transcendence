export type DockerVersion = {
  Version: string;
  ApiVersion: string;
  MinAPIVersion: string;
};

export type DockerContainerSummary = {
  Id: string;
};

export type DockerContainerInspect = {
  Id: string;
  Name: string;
  Config: {
    Image: string;
    Labels: Record<string, string> | null;
  };
  State: {
    Status: string;
    Health?: {
      Status: string;
    };
  };
  NetworkSettings: {
    Ports:
      | Record<
          string,
          Array<{
            HostIp: string;
            HostPort: string;
          }> | null
        >
      | null;
    Networks:
      | Record<
          string,
          {
            NetworkID: string;
            IPAddress: string;
          }
        >
      | null;
  };
  Mounts: Array<{
    Type: string;
    Name?: string;
    Source: string;
    Destination: string;
    RW: boolean;
  }>;
};

export type DockerNetwork = {
  Id: string;
  Name: string;
  Driver: string;
  Internal: boolean;
  Labels: Record<string, string> | null;
};

export type DockerVolume = {
  Name: string;
  Driver: string;
  Labels: Record<string, string> | null;
};

export type DockerEvent = {
  Type: string;
  Action: string;
  Actor: {
    ID: string;
    Attributes: Record<string, string>;
  };
  time: number;
  timeNano: number;
};

type DockerVolumeList = {
  Volumes: DockerVolume[] | null;
};

export class DockerClient {
  private readonly baseUrl: string;
  private apiVersion: string | null = null;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
  }

  async ping(): Promise<boolean> {
    try {
      const response = await fetch(`${this.baseUrl}/_ping`);

      if (!response.ok) {
        return false;
      }

      return (await response.text()).trim() === 'OK';
    } catch {
      return false;
    }
  }

  async version(): Promise<DockerVersion> {
    const response = await this.request<DockerVersion>('/version', false);

    this.apiVersion = response.ApiVersion;

    return response;
  }

  async listContainers(): Promise<DockerContainerSummary[]> {
    return this.request<DockerContainerSummary[]>(
      '/containers/json?all=1',
    );
  }

  async inspectContainer(id: string): Promise<DockerContainerInspect> {
    return this.request<DockerContainerInspect>(
      `/containers/${encodeURIComponent(id)}/json`,
    );
  }

  async listNetworks(): Promise<DockerNetwork[]> {
    return this.request<DockerNetwork[]>('/networks');
  }

  async listVolumes(): Promise<DockerVolume[]> {
    const response = await this.request<DockerVolumeList>('/volumes');

    return response.Volumes ?? [];
  }

  private async ensureApiVersion(): Promise<string> {
    if (this.apiVersion !== null) {
      return this.apiVersion;
    }

    const version = await this.version();

    return version.ApiVersion;
  }

  private async request<T>(
    path: string,
    versioned = true,
  ): Promise<T> {
    const prefix = versioned
      ? `/v${await this.ensureApiVersion()}`
      : '';

    let response: Response;

    try {
      response = await fetch(`${this.baseUrl}${prefix}${path}`);
    } catch {
      throw new Error('Docker Engine is unavailable');
    }

    if (!response.ok) {
      throw new Error(
        `Docker API returned HTTP ${response.status}`,
      );
    }

    return response.json() as Promise<T>;
  }

  async inspectNetwork(id: string): Promise<DockerNetwork> {
    return this.request<DockerNetwork>(
      `/networks/${encodeURIComponent(id)}`,
    );
  }

  async inspectVolume(name: string): Promise<DockerVolume> {
    return this.request<DockerVolume>(
      `/volumes/${encodeURIComponent(name)}`,
    );
  }

  async eventStream(): Promise<
    ReadableStream<Uint8Array>
  > {
    const apiVersion = await this.ensureApiVersion();

    const filters = encodeURIComponent(
      JSON.stringify({
        type: ['container', 'network', 'volume'],
      }),
    );

    const response = await fetch(
      `${this.baseUrl}/v${apiVersion}/events?filters=${filters}`,
      {
        headers: {
          Accept: 'application/x-ndjson',
        },
      },
    );

    if (!response.ok) {
      throw new Error(
        `Docker events request failed with HTTP ${response.status}`,
      );
    }

    if (response.body === null) {
      throw new Error('Docker events response has no body');
    }

    return response.body;
  }
}