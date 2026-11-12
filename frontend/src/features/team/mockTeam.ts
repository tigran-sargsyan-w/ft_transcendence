import type { TeamMember } from "./type";

export const mockTeam: TeamMember[] = [
  {
    user: { id: "usr-001", displayName: "Nathan", avatarUrl: null },
    online: true,
    lastSeenAt: null,
  },
  {
    user: { id: "usr-002", displayName: "Camille", avatarUrl: null },
    online: true,
    lastSeenAt: null,
  },
  {
    user: { id: "usr-003", displayName: "Tigran", avatarUrl: null },
    online: false,
    lastSeenAt: "2026-11-12T09:40:00Z",
  },
  {
    user: { id: "usr-004", displayName: "PA", avatarUrl: null },
    online: false,
    lastSeenAt: null,
  },
];
