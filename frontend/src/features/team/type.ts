import type { UserRef } from "@/types/user";

export interface TeamMember {
  user: UserRef;
  online: boolean;
  lastSeenAt: string | null;
}
