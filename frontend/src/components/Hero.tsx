import { Network, Radar, ShieldAlert, Users } from "lucide-react";

const features = [
  {
    icon: Network,
    title: "Live topology",
    description: "Your Docker containers and their dependencies, as a real-time graph.",
  },
  {
    icon: Radar,
    title: "Blast radius",
    description: "When a service goes down, instantly see everything it affects.",
  },
  {
    icon: ShieldAlert,
    title: "Attack paths",
    description: "Spot critical nodes and exploitable routes.",
  },
  {
    icon: Users,
    title: "Incident response",
    description: "Coordinate your team around a single shared view, live.",
  },
];

// Decorative mini-graph: a failing node (red) and its impact zone (amber).
const nodes = [
  { id: "nginx", x: 40, y: 60, state: "ok" },
  { id: "api", x: 140, y: 60, state: "down" },
  { id: "db", x: 240, y: 30, state: "impacted" },
  { id: "worker", x: 240, y: 95, state: "impacted" },
  { id: "cache", x: 140, y: 125, state: "ok" },
] as const;

const edges = [
  ["nginx", "api"],
  ["api", "db"],
  ["api", "worker"],
  ["nginx", "cache"],
] as const;

const nodeColor = {
  ok: "fill-emerald-400",
  down: "fill-red-500",
  impacted: "fill-amber-400",
} as const;

function TopologyPreview() {
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));

  return (
    <svg
      viewBox="0 0 280 155"
      className="w-full max-w-sm"
      role="img"
      aria-label="Preview of an infrastructure graph with a failing service"
    >
      {edges.map(([from, to]) => {
        const a = byId[from];
        const b = byId[to];
        const impacted = a.state !== "ok" && b.state !== "ok";
        return (
          <line
            key={`${from}-${to}`}
            x1={a.x}
            y1={a.y}
            x2={b.x}
            y2={b.y}
            strokeWidth={1.5}
            strokeDasharray={impacted ? "4 3" : undefined}
            className={impacted ? "stroke-amber-400/70" : "stroke-stone-500"}
          />
        );
      })}
      {nodes.map((n) => (
        <g key={n.id}>
          {n.state === "down" && (
            <circle
              cx={n.x}
              cy={n.y}
              r={14}
              className="fill-red-500/30 animate-ping origin-center transform-fill"
            />
          )}
          <circle cx={n.x} cy={n.y} r={8} className={nodeColor[n.state]} />
          <text
            x={n.x}
            y={n.y + 22}
            textAnchor="middle"
            className="fill-stone-300 text-[10px] font-mono"
          >
            {n.id}
          </text>
        </g>
      ))}
    </svg>
  );
}

export function Hero() {
  return (
    <section className="flex max-w-xl flex-col gap-8 px-10 text-stone-100">
      <div className="flex flex-col gap-3">
        <span className="w-fit rounded-full border border-stone-600 px-3 py-1 text-xs uppercase tracking-widest text-stone-400">
          Real-Time Incident Platform
        </span>
        <h1 className="font-heading text-4xl font-semibold leading-tight lg:text-5xl">
          Infrastructure Intelligence
        </h1>
        <p className="text-stone-400">
          A subway map of your infrastructure: every station is a service,
          every line a dependency. When a station closes, you know exactly
          which lines are affected.
        </p>
      </div>

      <TopologyPreview />

      <ul className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {features.map(({ icon: Icon, title, description }) => (
          <li key={title} className="flex gap-3">
            <Icon className="mt-0.5 size-5 shrink-0 text-amber-400" />
            <div>
              <p className="font-medium">{title}</p>
              <p className="text-sm text-stone-400">{description}</p>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
