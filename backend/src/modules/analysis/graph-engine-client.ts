import type { EngineTopology } from '../topology/graph.types.js';

// What Nest sends to the Graph Engine (docs/architecture/graph-contract.md).
export type AnalyzeRequest = {
  topology: EngineTopology;
  analyses: string[];
  options?: { seedNodeIds?: string[]; maxDepth?: number; maxPaths?: number };
};

// The `data` of a successful answer. `results` is passed on as the engine
// sends it: its keys depend on the analyses asked for.
export type AnalyzeData = {
  schemaVersion: 1;
  environmentId: string;
  analyzedAt: string;
  // The echo of the topology's revision; absent if the engine did not echo it.
  topologyRevision?: string;
  results: Record<string, unknown>;
};

// The engine answered with an error of its own: { error: { code, message } }.
export class GraphEngineError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

// Nest asks the Graph Engine. Whatever else goes wrong (unreachable, timeout,
// an answer that is not the contract's) is a plain Error.
export interface GraphEngineClient {
  analyze(request: AnalyzeRequest): Promise<AnalyzeData>;
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null;

function isAnalyzeData(data: unknown): data is AnalyzeData {
  return (
    isObject(data) &&
    data.schemaVersion === 1 &&
    typeof data.environmentId === 'string' &&
    typeof data.analyzedAt === 'string' &&
    isObject(data.results) &&
    (data.topologyRevision === undefined ||
      typeof data.topologyRevision === 'string')
  );
}

export class HttpGraphEngineClient implements GraphEngineClient {
  private readonly baseUrl: string;

  constructor(
    baseUrl: string,
    private readonly timeoutMs = 5_000,
  ) {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
  }

  async analyze(request: AnalyzeRequest) {
    const response = await fetch(`${this.baseUrl}/api/v1/analyze`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(request),
      signal: AbortSignal.timeout(this.timeoutMs),
    });
    const body: unknown = await response.json().catch(() => null);

    if (!response.ok) {
      if (
        isObject(body) &&
        isObject(body.error) &&
        typeof body.error.code === 'string' &&
        typeof body.error.message === 'string'
      ) {
        throw new GraphEngineError(
          response.status,
          body.error.code,
          body.error.message,
        );
      }

      throw new Error(`The Graph Engine answered HTTP ${response.status}`);
    }

    if (!isObject(body) || !isAnalyzeData(body.data)) {
      throw new Error('The Graph Engine sent a malformed answer');
    }

    return body.data;
  }
}
