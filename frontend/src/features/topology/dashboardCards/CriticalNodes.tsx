import { MainCard } from "@/components/cards/MainCard";
import type { CriticalNodeRow } from "../types";
import { Progress } from "@/components/ui/progress";
import {
  criticalityStyles,
  getCriticalityLevel,
  getReasonLabel,
} from "../criticaly";
import { Badge } from "@/components/ui/badge";

interface CriticalNodesProps {
  criticalNodes: CriticalNodeRow[];
}

export const CriticalNodes = ({ criticalNodes }: CriticalNodesProps) => {
  const top3 = [...criticalNodes].sort((a, b) => b.score - a.score).slice(0, 3);

  return (
    <MainCard title="Critical Nodes">
      <div className="flex flex-col gap-4">
        {top3.map((c, index) => {
          const level = getCriticalityLevel(c.score);
          const style = criticalityStyles[level];

          return (
            <div key={c.id} className="flex flex-col gap-2">
              <div className="flex justify-between">
                <strong>
                  {index + 1} {c.label}
                </strong>
                <div className="flex gap-2">
                  {c.score}
                  <Badge variant={style.badge} className="uppercase">
                    {level}
                  </Badge>
                </div>
              </div>
              <Progress value={c.score * 100} className={`${style.bar}`} />
              <div>
                {c.reasons.map((r) => (
                  <Badge key={r} variant="outline">
                    {getReasonLabel(r)}
                  </Badge>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </MainCard>
  );
};
