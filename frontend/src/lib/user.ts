import type { UserRef } from "@/types/user";

export function getInitial({ displayName }: UserRef) {
  return displayName ? displayName[0].toUpperCase() : "NC";
}
