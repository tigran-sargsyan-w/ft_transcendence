import { Navbar } from "./Navbar";
import { UserMenu } from "./UserMenu";
import { Link } from "react-router";

export function AppHeader() {
  return (
    <header className="sticky top-0 z-50 border-b bg-background">
      <div className="flex h-14 items-center gap-6 px-4">
        <Link to="/dashboard" className="font-heading text-lg font-semibold">
          Infra Intelligence
        </Link>
        <Navbar />
        <div className="ml-auto">
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
