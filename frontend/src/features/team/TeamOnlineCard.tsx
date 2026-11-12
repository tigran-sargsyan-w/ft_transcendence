import { MainCard } from "../../components/cards/MainCard";
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
      <span>{`${totalOnlineMembers}/${totalMembers} membre(s) connecté(s)`}</span>{" "}
      <div className="flex flex-col gap-2">
        {onlineMembers.map((m) => (
          <div></div>
        ))}
      </div>
    </MainCard>
  );
};
