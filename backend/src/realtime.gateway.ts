import {
  OnGatewayConnection,
  OnGatewayDisconnect,
  WebSocketGateway,
  WebSocketServer,
} from '@nestjs/websockets';
import { Server, Socket } from 'socket.io';

const services = [
  { id: 'api-gateway', name: 'API Gateway', status: 'healthy' },
  { id: 'event-processor', name: 'Event Processor', status: 'healthy' },
  { id: 'database', name: 'PostgreSQL', status: 'healthy' },
];

@WebSocketGateway({
  namespace: '/realtime',
  cors: { origin: '*' },
})
export class RealtimeGateway implements OnGatewayConnection, OnGatewayDisconnect {
  @WebSocketServer()
  server!: Server;

  private timer?: NodeJS.Timeout;

  afterInit(): void {
    this.timer = setInterval(() => {
      this.server.emit('telemetry.tick', {
        event: 'telemetry.tick',
        payload: {
          serviceId: services[Math.floor(Math.random() * services.length)].id,
          latencyMs: Math.floor(15 + Math.random() * 150),
          requestRate: Math.floor(100 + Math.random() * 900),
        },
        timestamp: new Date().toISOString(),
      });
    }, 3000);
  }

  handleConnection(client: Socket): void {
    client.emit('infrastructure.snapshot', {
      event: 'infrastructure.snapshot',
      payload: { services },
      timestamp: new Date().toISOString(),
    });
  }

  handleDisconnect(): void {
    // Connection lifecycle is intentionally explicit; domain-specific recovery comes next.
  }
}
