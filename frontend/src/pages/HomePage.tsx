import { Hero } from "@/components/Hero";
import { LoginCard } from "@/features/auth/LoginCard";
import { RegisterCard } from "@/features/auth/RegisterCard";
import { useState } from "react";

type Mode = "login" | "register";

export function HomePage() {
  const [mode, setMode] = useState<Mode>("login");

  return (
    <main className="flex flex-col gap-6 md:flex-row justify-center align-middle p-6 h-screen w-full">
      <div className="flex md:flex-1 justify-center items-center md:my-auto">
        <Hero />
      </div>
      <div className="flex md:flex-1 justify-center items-center md:my-auto">
        {mode === "login" ? (
          <LoginCard onSwitch={() => setMode("register")} />
        ) : (
          <RegisterCard onSwitch={() => setMode("login")} />
        )}
      </div>
    </main>
  );
}
