import { Navbar } from "./Navbar";
import { MobileNav } from "./MobileNav";
import { UserMenu } from "./UserMenu";
import { Link } from "react-router";

export function AppHeader() {
  return (
    <header className="sticky top-0 z-50 border-b bg-background">
      <div className="flex h-14 items-center gap-2 px-4 md:gap-6">
        <MobileNav />
        <Link to="/dashboard" className="font-heading text-lg font-semibold">
          Infra Intelligence
        </Link>
        <div className="hidden md:block">
          <Navbar />
        </div>
        <div className="ml-auto">
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
