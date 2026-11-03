import { NotFoundException, ServiceUnavailableException } from '@nestjs/common';
import type { TopologySnapshot } from './graph.types.js';

// The topology of `environmentId` (the one the server follows when absent),
// or the error a client gets when there is none. The realtime gateway and the
// analyses answer the same way.
export function knownSnapshot(
  snapshot: TopologySnapshot | null,
  environmentId?: string,
) {
  if (!snapshot) {
    throw new ServiceUnavailableException('The topology is not available yet', {
      errorCode: 'TOPOLOGY_UNAVAILABLE',
    });
  }

  if (environmentId !== undefined && environmentId !== snapshot.environmentId) {
    throw new NotFoundException('Unknown environment', {
      errorCode: 'TOPOLOGY_UNKNOWN_ENVIRONMENT',
    });
  }

  return snapshot;
}
