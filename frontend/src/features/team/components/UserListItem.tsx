import { Badge } from "@/components/ui/badge";
import {
  Avatar,
  AvatarBadge,
  AvatarFallback,
  AvatarImage,
} from "@/components/ui/avatar";
import type { UserRef } from "@/types/user";

export const UserListItem = (user: UserRef) => {
  return (
    <Badge>
      <Avatar>
        <AvatarImage src="https://github.com/shadcn.png" alt="@shadcn" />
        <AvatarFallback>CN</AvatarFallback>
        {}
        <AvatarBadge className="bg-green-600 dark:bg-green-800" />{" "}
      </Avatar>
    </Badge>
  );
};
