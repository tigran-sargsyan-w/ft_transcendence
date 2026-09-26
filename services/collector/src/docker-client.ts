export type DockerVersion = {
  Version: string;
  ApiVersion: string;
  MinAPIVersion: string;
};

export class DockerClient {
  private readonly baseUrl: string;

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
    const response = await fetch(`${this.baseUrl}/version`);

    if (!response.ok) {
      throw new Error(
        `Docker version request failed with status ${response.status}`,
      );
    }

    return response.json() as Promise<DockerVersion>;
  }
}