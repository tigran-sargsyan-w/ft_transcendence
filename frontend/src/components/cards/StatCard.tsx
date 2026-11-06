import { Card, CardHeader, CardTitle, CardContent } from "../ui/card";

interface StatCardProps {
  title: string;
  value: number;
  description: string;
}

export const StatCard = ({ title, value, description }: StatCardProps) => {
  return (
    <Card className="flex-1">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-bold">
          {value}{" "}
          <span className="text-sm text-muted-foreground">{description}</span>
        </p>
      </CardContent>
    </Card>
  );
};
