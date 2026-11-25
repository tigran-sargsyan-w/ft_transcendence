import { Outlet } from "react-router";
import { AppHeader } from "@/components/header/AppHeader";

export default function DashboardLayout() {
  return (
    <>
      <AppHeader />
      <main>
        <Outlet />
      </main>
    </>
  );
}
