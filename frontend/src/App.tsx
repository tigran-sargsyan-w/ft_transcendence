import { useEffect, useMemo, useState } from 'react';
import { io } from 'socket.io-client';

type Service = {
  id: string;
  name: string;
  status: string;
};

type TimelineEvent = {
  name: string;
  detail: string;
  timestamp: string;
};

const backendUrl = import.meta.env.VITE_BACKEND_URL ?? 'http://localhost:3000';

export function App() {
  const [apiStatus, setApiStatus] = useState('checking');
  const [socketStatus, setSocketStatus] = useState('connecting');
  const [services, setServices] = useState<Service[]>([]);
  const [events, setEvents] = useState<TimelineEvent[]>([]);

  const socket = useMemo(() => io(`${backendUrl}/realtime`), []);

  useEffect(() => {
    fetch(`${backendUrl}/api/v1/health`)
      .then((response) => response.json())
      .then((body) => setApiStatus(body.data?.status ?? 'unknown'))
      .catch(() => setApiStatus('unavailable'));

    socket.on('connect', () => setSocketStatus('connected'));
    socket.on('disconnect', () => setSocketStatus('disconnected'));

    socket.on('infrastructure.snapshot', (message) => {
      setServices(message.payload.services);
      setEvents((current) => [
        {
          name: message.event,
          detail: `${message.payload.services.length} services discovered`,
          timestamp: message.timestamp,
        },
        ...current,
      ]);
    });

    socket.on('telemetry.tick', (message) => {
      setEvents((current) => [
        {
          name: message.event,
          detail: `${message.payload.serviceId}: ${message.payload.latencyMs} ms / ${message.payload.requestRate} req·s⁻¹`,
          timestamp: message.timestamp,
        },
        ...current,
      ].slice(0, 12));
    });

    return () => {
      socket.disconnect();
    };
  }, [socket]);

  return (
    <main className="shell">
      <header className="hero">
        <div>
          <p className="eyebrow">Infrastructure Intelligence</p>
          <h1>Live Operations Console</h1>
          <p className="subtitle">First end-to-end vertical slice: API health + realtime infrastructure events.</p>
        </div>
        <div className="connection-panel">
          <span>API: <strong>{apiStatus}</strong></span>
          <span>Realtime: <strong>{socketStatus}</strong></span>
        </div>
      </header>

      <section>
        <div className="section-heading">
          <h2>Infrastructure</h2>
          <span>{services.length} services</span>
        </div>
        <div className="service-grid">
          {services.map((service) => (
            <article className="service-card" key={service.id}>
              <div className="status-dot" />
              <div>
                <h3>{service.name}</h3>
                <p>{service.id}</p>
              </div>
              <span className="status-label">{service.status}</span>
            </article>
          ))}
        </div>
      </section>

      <section className="timeline-section">
        <div className="section-heading">
          <h2>Live event stream</h2>
          <span>WebSocket</span>
        </div>
        <div className="timeline">
          {events.length === 0 && <p className="empty">Waiting for the first event…</p>}
          {events.map((event, index) => (
            <article className="event-row" key={`${event.timestamp}-${index}`}>
              <time>{new Date(event.timestamp).toLocaleTimeString()}</time>
              <strong>{event.name}</strong>
              <span>{event.detail}</span>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}
