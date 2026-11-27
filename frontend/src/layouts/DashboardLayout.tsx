import { Outlet } from "react-router";
import { AppHeader } from "@/components/header/AppHeader";
import { Footer } from "@/components/Footer";

export default function DashboardLayout() {
  return (
    <div className="flex min-h-svh flex-col">
      <AppHeader />
      <main className="flex-1">
        <Outlet />
      </main>
      <Footer />
    </div>
  );
}
