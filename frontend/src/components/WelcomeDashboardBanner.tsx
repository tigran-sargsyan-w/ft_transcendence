interface WelcomeDashboardBannerProps {
  displayName: string;
}

export const WelcomeDashboardBanner = ({
  displayName,
}: WelcomeDashboardBannerProps) => {
  return (
    <header className="flex flex-col gap-2">
      <span className="text-sm">Organization: 42 Transcendence</span>
      <strong className="text-3xl">{`Welcome ${displayName} !`}</strong>
    </header>
  );
};
