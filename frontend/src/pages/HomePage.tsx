import { Hero } from "@/components/Hero";
import { LoginCard } from "@/components/LoginCard";
import { RegisterCard } from "@/components/RegisterCard";
import { useState } from "react";

type Mode = "login" | "register";

export function HomePage() {
  const [mode, setMode] = useState<Mode>("login");

  return (
    <main className="flex w-screen">
      <div className="hidden md:flex flex-1 justify-center items-center h-screen">
        <Hero />
      </div>
      <div className="p-6 flex-1 flex justify-center items-center h-screen">
        {mode === "login" ? (
          <LoginCard onSwitch={() => setMode("register")} />
        ) : (
          <RegisterCard onSwitch={() => setMode("login")} />
        )}
      </div>
    </main>
  );
}
