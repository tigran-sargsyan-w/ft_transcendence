import { Card, CardHeader, CardTitle, CardContent } from "../ui/card";

const toneClasses = {
  default: "",
  danger: "text-severity-sev1",
  warning: "text-amber-400",
  success: "text-emerald-400",
} as const;

interface StatCardProps {
  title: string;
  value: number;
  description?: string;
  tone?: keyof typeof toneClasses;
}

export const StatCard = ({
  title,
  value,
  description,
  tone = "default",
}: StatCardProps) => {
  return (
    <Card className="flex-1">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <strong className={`text-2xl font-bold ${toneClasses[tone]}`}>
          {value}{" "}
        </strong>
        <span className="text-sm text-muted-foreground">{description}</span>
      </CardContent>
    </Card>
  );
};
