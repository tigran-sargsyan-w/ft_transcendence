import { Outlet } from "react-router";
import { Navbar } from "@/components/Navbar";

export default function DashboardLayout() {
  return (
    <>
      <Navbar />
      <main>
        <Outlet />
      </main>
    </>
  );
}
