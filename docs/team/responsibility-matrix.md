# Responsibility Matrix

## Purpose

This document defines the current ownership boundaries for the **Infrastructure Intelligence / Real-Time Incident Platform**.

The goal is to make responsibilities explicit, reduce duplicated work, clarify cross-team dependencies, and make it easier to plan tasks with deadlines so that one person's work does not block another person's progress.

This is a **living document**. Ownership can evolve as the architecture changes, new modules are added, or a fourth team member joins.

---

## Product Direction

The project combines three main engineering areas:

- **Cybersecurity / DevOps / Infrastructure**
- **Algorithms / Graph Theory / Logic**
- **Backend / Real-Time / Distributed Systems**

The platform should connect to an infrastructure such as Docker, discover services and dependencies, maintain a live topology, stream infrastructure events, detect incidents and security risks, analyze blast radius and attack paths, and support real-time incident response workflows.

---

## Ownership Legend

| Status | Meaning |
|---|---|
| **Primary** | Main owner of the subsystem and implementation decisions |
| **Shared** | Responsibility is intentionally split between multiple members |
| **Interface** | Depends on or integrates with another member's subsystem |
| **Unassigned** | Required or planned work without a confirmed owner yet |
| **Future** | Useful feature, but not currently part of the immediate implementation scope |

---

# 1. Tigran — Security, Infrastructure & DevSecOps

## Primary ownership

| Area | Responsibility | Status |
|---|---|---|
| Docker Collector | Read Docker state/events and expose normalized infrastructure events | **Primary** |
| Docker access strategy | Secure access to Docker API/socket | **Primary** |
| Read-only socket proxy | Prefer least-privilege access instead of raw `docker.sock` mounting | **Primary** |
| Collector infrastructure | Containerization, runtime wiring, networking and service isolation | **Primary** |
| Nginx | Reverse proxy and infrastructure-facing HTTP entry point | **Primary** |
| HTTPS / proxy layer | Infrastructure-side HTTPS routing and proxy configuration | **Primary** |
| ModSecurity / WAF | WAF setup, CRS rules, tuning and security hardening | **Primary** |
| WAF + WebSocket compatibility | Nginx/ModSecurity side of WebSocket and Socket.IO long-polling validation | **Primary** |
| Vault / secret delivery | HashiCorp Vault integration and secret delivery strategy | **Primary** |
| Vault Agent | Inject secrets into files/config without coupling the app directly to Vault SDK | **Primary** |
| Container hardening | Least privilege, isolation and infrastructure security controls | **Primary** |
| Network hardening | Service/network boundaries and infrastructure exposure | **Primary** |
| Platform / microservices architecture | Infrastructure/service boundary design | **Shared / Lead focus** |

## Shared interfaces

### With Nathan

```text
Docker
  ↓
Tigran: Collector
  ↓
Normalized infrastructure events
  ↓
HTTP / JSON
  ↓
Nathan: NestJS backend
```

```text
Browser
  ↓
HTTPS
  ↓
Tigran: Nginx + ModSecurity
  ↓
Nathan: NestJS + Socket.IO
```

```text
Vault / Vault Agent
  ↓
Tigran: secret delivery path + format
  ↓
Nathan: application reads provided secret
```

### With Camille

```text
Docker infrastructure
  ↓
Collector / backend topology data
  ↓
Camille: graph analysis
```

## Explicitly outside Tigran's primary ownership

The following are not Tigran's main responsibility unless explicitly reassigned later:

- application-level login/logout/session logic
- JWT/session implementation
- current-user endpoint
- NestJS Socket.IO gateway internals
- graph algorithms
- blast-radius implementation
- attack-path graph traversal
- frontend graph rendering
- user/profile UI

---

# 2. Nathan — Application Backend & Real-Time Systems

## Primary ownership

| Area | Responsibility | Status |
|---|---|---|
| Main NestJS backend | Application backend and orchestration | **Primary** |
| Application authentication | Login/logout/session/current-user flow | **Primary** |
| Credential verification | Password verification and application auth logic | **Primary** |
| Prisma runtime integration | Application-side DB integration | **Primary** |
| Prisma migration deploy | Ensure migrations are applied when required | **Primary** |
| API response conventions | Global exception filter / interceptor / API format | **Primary** |
| Socket.IO gateway | Backend real-time gateway | **Primary** |
| WebSocket auth handshake | Authentication at Socket.IO/Nest level | **Primary** |
| Real-time event ingestion | Consume infrastructure events and distribute updates | **Primary** |
| State synchronization | Maintain consistent live application state | **Primary** |
| Reconnect / recovery | Restore correct client state after reconnect | **Primary** |
| Event ordering | Prevent stale or out-of-order realtime state | **Primary** |
| Topology construction | Convert normalized infrastructure events into nodes/edges | **Primary** |
| Incident response room | Realtime coordination, timeline, runbooks and collaboration backend | **Primary** |

## Shared interfaces

### With Tigran

- consumes normalized collector events
- defines collector event needs together with Tigran
- owns Nest/Socket.IO side of the WAF + WebSocket spike
- consumes secrets delivered by Tigran's Vault infrastructure

### With Camille

```text
Normalized infrastructure state
  ↓
Nathan: topology nodes/edges
  ↓
HTTP / JSON
  ↓
Camille: graph engine
  ↓
analysis results
  ↓
Nathan: backend orchestration / realtime delivery
```

Nathan owns topology construction; Camille's Python service should remain analysis-focused.

---

# 3. Camille — Frontend, Graph Visualization & Algorithms

## Primary ownership

| Area | Responsibility | Status |
|---|---|---|
| React frontend | Main frontend implementation | **Primary** |
| React + TypeScript + Vite | Frontend framework/tooling | **Primary** |
| Tailwind / shadcn UI | Frontend styling/component layer | **Primary** |
| React Flow | Interactive infrastructure topology visualization | **Primary** |
| Graph visualization | Nodes, edges, statuses, risk overlays, blast-radius visualization | **Primary** |
| Python graph engine | Separate analysis microservice | **Primary** |
| FastAPI / NetworkX service | Graph-analysis service implementation | **Primary** |
| Blast-radius algorithm | Graph-based incident impact calculation | **Primary** |
| Attack-path analysis | Graph traversal / attack-path calculations | **Primary** |
| Critical-node analysis | Ranking / detection of critical infrastructure nodes | **Primary** |
| Graph-analysis contracts | Analysis input/output structures together with backend | **Shared / Lead focus** |
| Auth / profile UI | User-facing auth/profile screens | **Primary frontend side** |

## Shared interfaces

### With Nathan

- Nathan provides topology nodes/edges
- Camille consumes topology snapshots
- graph engine returns analysis results
- Nathan handles backend orchestration and realtime delivery
- reconnect/state recovery remains backend-owned

### With Tigran

- security/infrastructure facts originate from the infrastructure side
- graph algorithms can use this information for risk/attack-path analysis

---

# 4. Cross-Team Features

Some features intentionally span multiple domains and should not be treated as belonging entirely to one person.

## Security Risk Map

| Part | Owner |
|---|---|
| Infrastructure/security facts | **Tigran** |
| Topology representation and backend delivery | **Nathan** |
| Attack-path / graph analysis | **Camille** |
| Frontend visualization | **Camille** |
| Realtime updates | **Nathan** |

Conceptual flow:

```text
Tigran
Infrastructure / security findings
        ↓
Nathan
Topology + backend state
        ↓
Camille
Attack-path / risk analysis
        ↓
Nathan
Realtime result delivery
        ↓
Camille
Risk-map visualization
```

---

## Live Infrastructure Graph

| Part | Owner |
|---|---|
| Docker discovery | **Tigran** |
| Infrastructure events | **Tigran** |
| Event ingestion | **Nathan** |
| Topology node/edge construction | **Nathan** |
| Realtime synchronization | **Nathan** |
| React Flow visualization | **Camille** |

---

## Incident Summary & Blast Radius

| Part | Owner |
|---|---|
| Infrastructure incident event | **Tigran / Nathan interface** |
| Incident state/orchestration | **Nathan** |
| Blast-radius computation | **Camille** |
| Result delivery | **Nathan** |
| Visualization | **Camille** |

---

## Incident Response Room

| Part | Owner |
|---|---|
| Realtime backend | **Nathan** |
| Timeline/event synchronization | **Nathan** |
| Collaboration state | **Nathan** |
| Frontend UI | **Camille / shared frontend work** |
| Infrastructure events feeding timeline | **Tigran** |

---

# 5. Currently Unassigned / Needs Explicit Decision

These areas are required, planned, or likely necessary, but do not currently have a confirmed primary owner.

| Area | Current status | Notes |
|---|---|---|
| Organization / Workspace system | **Unassigned** | Backend, membership model and UI need ownership |
| RBAC / Advanced Permissions | **Unassigned** | Security design + backend enforcement + UI |
| User profile backend | **Partially assigned** | UI naturally fits Camille; backend owner not explicitly fixed |
| Chat / Friends / User Interactions | **Unassigned** | Likely intersects Nathan's realtime work but not formally assigned |
| Incident Replay | **Unassigned** | Important architectural feature; needs explicit owner split |
| Replay event storage | **Unassigned** | Append-only history / topology revisions / reconstruction |
| Incident detection engine | **Unassigned** | Rules for crashes, restart loops, CPU spikes, security events, etc. |
| Event correlation / root cause | **Unassigned / Future** | Could become part of graph/analysis work |
| Metrics collection | **Unassigned** | Naturally close to Tigran's DevOps area, but not formally assigned |
| Monitoring / observability | **Unassigned** | Could include Prometheus/Grafana later |
| Shared TypeScript contracts package | **Shared / No owner** | Events, REST types and WebSocket messages |
| Integration Setup UI | **Unassigned** | Docker configuration page |
| Overview / Workspace UI | **Unassigned** | Standard dashboard/admin work |
| CI/CD | **Unassigned** | Build/test/deploy automation |
| Cross-service integration testing | **Shared / No coordinator** | Collector ↔ Nest, Nest ↔ Python, WAF ↔ Socket.IO, Nest ↔ PostgreSQL |
| Notifications | **Unassigned / Future** | Possible module/bonus feature |
| Domain DB models beyond User | **Shared / Unassigned by domain** | Incident, topology, event, organization, membership, etc. |

---

# 6. Dependency Map

The main engineering dependency chain currently looks like this:

```text
Tigran
Docker Collector
     ↓
normalized infrastructure events
     ↓
Nathan
NestJS ingestion + topology state
     ↓
     ├──────────────→ Socket.IO → Frontend
     │
     └──────────────→ Camille Graph Engine
                           ↓
                    analysis results
                           ↓
                        Nathan
                           ↓
                        Frontend
```

Infrastructure entry path:

```text
Browser
  ↓
HTTPS
  ↓
Tigran
Nginx + ModSecurity
  ↓
Nathan
NestJS / Socket.IO
```

Secret delivery path:

```text
Vault
  ↓
Tigran
Vault Agent / secret delivery
  ↓
Application containers
  ↓
Nathan backend consumes secret
```

---

# 7. Task Planning Rules

To avoid blocking each other, each member should maintain a small task list with:

- task
- owner
- dependency
- expected output / contract
- deadline
- blocking status

Recommended format:

| Task | Owner | Depends on | Deliverable | Deadline | Status |
|---|---|---|---|---|---|
| Example: collector event contract review | Tigran + Nathan | Draft event schema | Agreed JSON contract | TBD | Todo |
| Example: Docker collector POC | Tigran | Event contract | Container lifecycle events sent to Nest | TBD | Todo |
| Example: topology builder POC | Nathan | Collector event format | Nodes/edges snapshot | TBD | Todo |
| Example: graph engine integration | Camille | Topology snapshot contract | Blast-radius result | TBD | Todo |

The important rule is:

> If Task B depends on Task A, the owner of Task A should expose the smallest usable contract/output as early as possible so the owner of Task B can continue without waiting for the entire subsystem to be finished.

---

# 8. Ownership Principles

1. **Primary ownership does not mean working alone.**  
   It means one person is responsible for driving the subsystem and keeping its decisions coherent.

2. **Interfaces are shared decisions.**  
   Contracts between Collector ↔ Nest, Nest ↔ Graph Engine, and Nginx/WAF ↔ Socket.IO must be agreed by both sides.

3. **Avoid duplicated ownership.**  
   For example, application auth belongs to Nathan while infrastructure security belongs to Tigran.

4. **Keep the Python graph engine analysis-only.**  
   Topology state construction remains in the backend; the graph engine consumes topology snapshots and returns analysis.

5. **Do not over-expand the POC.**  
   The current goal is to prove the core end-to-end flow first, then harden and extend it.

6. **Unassigned work should stay visibly unassigned until the team agrees.**  
   Do not silently assume ownership based only on technical proximity.

7. **Revisit this document when a fourth team member joins.**  
   Prefer giving the new member a coherent independent vertical rather than breaking already-working ownership boundaries unless the team explicitly wants to rebalance them.

---

# 9. Current High-Level Team Split

```text
TIGRAN
Security / Infrastructure / DevSecOps
│
├── Docker Collector
├── Docker access security
├── Nginx
├── ModSecurity / WAF
├── WAF + WebSocket infra side
├── Vault / secrets
├── container/network hardening
└── platform architecture

NATHAN
Application Backend / Real-Time Systems
│
├── NestJS backend
├── application auth
├── login / logout / session / current user
├── Prisma application integration
├── API conventions
├── Socket.IO
├── topology construction
├── realtime synchronization
├── reconnect / recovery
└── incident response backend

CAMILLE
Frontend / Algorithms
│
├── React frontend
├── React Flow
├── graph visualization
├── Python graph engine
├── blast radius
├── attack paths
├── critical nodes
└── auth/profile frontend

UNASSIGNED / TO DECIDE
│
├── Organizations / workspaces
├── RBAC
├── chat / friends
├── profile backend
├── incident replay
├── incident detection
├── metrics / observability
├── CI/CD
├── notifications
├── shared contracts ownership
└── cross-service testing coordination
```

---

## Next Review

This matrix should be reviewed again:

- after the fourth member's role is known;
- after the first Collector ↔ Nest contract is agreed;
- after the WAF + WebSocket spike;
- before implementing Incident Replay;
- whenever ownership changes significantly.
