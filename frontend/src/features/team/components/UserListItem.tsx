import { Badge } from "@/components/ui/badge";
import {
  Avatar,
  AvatarBadge,
  AvatarFallback,
  AvatarImage,
} from "@/components/ui/avatar";
import type { TeamMember } from "../type";
import { getInitial } from "@/lib/user";

interface UserListItemProps {
  member: TeamMember;
}

export const UserListItem = ({ member }: UserListItemProps) => {
  const avatar = member.user.avatarUrl ?? undefined;
  const initial = getInitial(member.user);
  const isOnline = member.online;
  const displayName = member.user.displayName;

  const onlineClassName = isOnline
    ? "bg-green-600 dark:bg-green-800"
    : "bg-red-600 dark:bg-red-800";

  return (
    <Badge variant={"secondary"} className="h-auto py-1">
      <Avatar>
        <AvatarImage src={avatar} alt="@shadcn" />
        <AvatarFallback>{initial}</AvatarFallback>
        <AvatarBadge className={onlineClassName} />{" "}
      </Avatar>
      {displayName}
    </Badge>
  );
};
