interface SectionBannerProps {
  title: string;
}

export const SectionBanner = ({ title }: SectionBannerProps) => {
  return (
    <header className="flex flex-col gap-2">
      <span className="text-sm">Organization: 42 Transcendence</span>
      <strong className="text-3xl">{title}</strong>
    </header>
  );
};
