import type {
  DockerContainerInspect,
  DockerEvent,
} from './docker-client.js';

import { DockerClient } from './docker-client.js';
import { EventStore } from './event-store.js';

import {
  normalizeContainer,
  normalizeNetwork,
  normalizeVolume,
} from './normalize.js';

export class EventWatcher {
  constructor(
    private readonly docker: DockerClient,
    private readonly store: EventStore,
  ) {}

  async run(): Promise<void> {
    while (true) {
            try {
            await this.consumeStream();

            console.warn(
                'Docker event stream closed; resetting stream session',
            );
            } catch (error) {
            console.error(
                'Docker event stream failed:',
                error,
            );
            }

            this.store.resetStream();

            await new Promise((resolve) =>
            setTimeout(resolve, 1000),
            );
        }
    }

  private async consumeStream(): Promise<void> {
    const stream = await this.docker.eventStream();
    const reader = stream.getReader();
    const decoder = new TextDecoder();

    let pending = '';

    while (true) {
      const chunk = await reader.read();

      if (chunk.done) {
        return;
      }

      pending += decoder.decode(chunk.value, {
        stream: true,
      });

      let newline = pending.indexOf('\n');

      while (newline !== -1) {
        const line = pending.slice(0, newline).trim();

        pending = pending.slice(newline + 1);

        if (line.length > 0) {
          const event = JSON.parse(line) as DockerEvent;

          await this.handle(event);
        }

        newline = pending.indexOf('\n');
      }
    }
  }

  private async handle(event: DockerEvent): Promise<void> {
    if (event.Type === 'container') {
      await this.handleContainer(event);
      return;
    }

    if (event.Type === 'network') {
      await this.handleNetwork(event);
      return;
    }

    if (event.Type === 'volume') {
      await this.handleVolume(event);
    }
  }

  private async inspectContainer(
    id: string,
  ): Promise<DockerContainerInspect | null> {
    try {
      return await this.docker.inspectContainer(id);
    } catch {
      return null;
    }
  }

  private occurredAt(event: DockerEvent): string {
    return new Date(event.time * 1000).toISOString();
  }

  private async handleContainer(
    event: DockerEvent,
    ): Promise<void> {
    if (event.Action === 'destroy') {
        this.store.append({
        occurredAt: this.occurredAt(event),
        type: 'container.destroyed',
        resource: {
            kind: 'container',
            id: event.Actor.ID,
        },
        data: {},
        });

        return;
    }

    const eventType = this.containerEventType(
        event.Action,
    );

    if (eventType === null) {
        return;
    }

    const inspected = await this.inspectContainer(
        event.Actor.ID,
    );

    if (eventType === 'container.died') {
        const data: Record<string, unknown> = {};

        const exitCode = Number(
        event.Actor.Attributes.exitCode,
        );

        if (Number.isInteger(exitCode)) {
        data.exitCode = exitCode;
        }

        if (inspected !== null) {
        data.container = normalizeContainer(inspected);
        }

        this.store.append({
        occurredAt: this.occurredAt(event),
        type: eventType,
        resource: {
            kind: 'container',
            id: event.Actor.ID,
        },
        data,
        });

        return;
    }

    if (inspected === null) {
        return;
    }

    this.store.append({
        occurredAt: this.occurredAt(event),
        type: eventType,
        resource: {
        kind: 'container',
        id: event.Actor.ID,
        },
        data: {
        container: normalizeContainer(inspected),
        },
    });
  }

  private containerEventType(
    action: string,
  ):
    | 'container.created'
    | 'container.started'
    | 'container.stopped'
    | 'container.died'
    | 'container.health_changed'
    | null {
    if (action === 'create') {
      return 'container.created';
    }

    if (action === 'start') {
      return 'container.started';
    }

    if (action === 'stop') {
      return 'container.stopped';
    }

    if (action === 'die') {
      return 'container.died';
    }

    if (
      action === 'health_status: healthy' ||
      action === 'health_status: unhealthy'
    ) {
      return 'container.health_changed';
    }

    return null;
  }

  private async handleNetwork(
    event: DockerEvent,
  ): Promise<void> {
    if (event.Action === 'create') {
      const network = await this.docker.inspectNetwork(
        event.Actor.ID,
      );

      this.store.append({
        occurredAt: this.occurredAt(event),
        type: 'network.created',
        resource: {
          kind: 'network',
          id: event.Actor.ID,
        },
        data: {
          network: normalizeNetwork(network),
        },
      });

      return;
    }

    if (event.Action === 'destroy') {
      this.store.append({
        occurredAt: this.occurredAt(event),
        type: 'network.removed',
        resource: {
          kind: 'network',
          id: event.Actor.ID,
        },
        data: {},
      });

      return;
    }

    if (
      event.Action !== 'connect' &&
      event.Action !== 'disconnect'
    ) {
      return;
    }

    const containerId =
      event.Actor.Attributes.container;

    if (!containerId) {
      return;
    }

    this.store.append({
      occurredAt: this.occurredAt(event),
      type:
        event.Action === 'connect'
          ? 'network.connected'
          : 'network.disconnected',
      resource: {
        kind: 'network',
        id: event.Actor.ID,
      },
      data: {
        containerId,
      },
    });
  }

  private async handleVolume(
    event: DockerEvent,
  ): Promise<void> {
    const name =
      event.Actor.ID ||
      event.Actor.Attributes.name;

    if (!name) {
      return;
    }

    if (event.Action === 'create') {
      const volume =
        await this.docker.inspectVolume(name);

      this.store.append({
        occurredAt: this.occurredAt(event),
        type: 'volume.created',
        resource: {
          kind: 'volume',
          id: name,
        },
        data: {
          volume: normalizeVolume(volume),
        },
      });

      return;
    }

    if (event.Action === 'destroy') {
      this.store.append({
        occurredAt: this.occurredAt(event),
        type: 'volume.removed',
        resource: {
          kind: 'volume',
          id: name,
        },
        data: {},
      });
    }
  }
}