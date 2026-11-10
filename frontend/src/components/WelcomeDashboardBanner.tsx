import { Card, CardContent } from "./ui/card";

export const WelcomeDashboardBanner = () => {
  return (
    <header className="flex flex-col gap-2">
      <span className="text-sm">Organization: 42 Transcendence</span>
      <strong className="text-3xl">Welcome PA !</strong>
    </header>
  );
};
