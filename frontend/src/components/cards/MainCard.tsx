import type { ReactNode } from "react";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  CardAction,
} from "../ui/card";
import { Link } from "react-router";

interface MainCardProps {
  title: string;
  linkLabel?: string;
  linkTarget?: string;
  children: ReactNode;
}

export const MainCard = ({
  title,
  linkLabel,
  linkTarget,
  children,
}: MainCardProps) => {
  return (
    <Card className="flex-1">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {linkTarget && (
          <CardAction>
            <Link to={linkTarget}>{linkLabel}</Link>
          </CardAction>
        )}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
};
