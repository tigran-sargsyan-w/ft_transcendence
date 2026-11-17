import { MainCard } from "../../components/cards/MainCard";
import { UserListItem } from "./components/UserListItem";
import type { TeamMember } from "./type";

interface TeamOnlineCardProps {
  team: TeamMember[];
}

export const TeamOnlineCard = ({ team }: TeamOnlineCardProps) => {
  const totalMembers = team.length;
  const onlineMembers = team.filter((m) => m.online === true);
  const totalOnlineMembers = onlineMembers.length;

  return (
    <MainCard title="Team">
      <div className="flex flex-col gap-2">
        <span>{`${totalOnlineMembers}/${totalMembers} membre(s) connecté(s)`}</span>
        <div className="flex gap-2">
          {onlineMembers.map((m) => (
            <UserListItem member={m} key={m.user.id} />
          ))}
        </div>
      </div>
    </MainCard>
  );
};
