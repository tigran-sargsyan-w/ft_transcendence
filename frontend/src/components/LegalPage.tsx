import type { ReactNode } from "react";
import { Link } from "react-router";
import { Footer } from "@/components/Footer";
import { useAuth } from "@/features/auth/AuthContext";

export function ContactLink() {
  return (
    <a
      href="https://github.com/tigran-sargsyan-w/ft_transcendence/issues"
      target="_blank"
      rel="noopener noreferrer"
    >
      open an issue on the project repository
    </a>
  );
}

interface LegalPageProps {
  title: string;
  updated: string;
  children: ReactNode;
}

// Used by public routes: these pages must be readable without signing in.
export function LegalPage({ title, updated, children }: LegalPageProps) {
  const { auth } = useAuth();
  const isLoggedIn = auth.status === "authenticated";

  return (
    <div className="flex min-h-svh flex-col">
      <main className="mx-auto w-full max-w-3xl flex-1 p-6">
        <Link
          to={isLoggedIn ? "/dashboard" : "/"}
          className="text-sm text-muted-foreground hover:text-foreground hover:underline"
        >
          {isLoggedIn ? "Back to dashboard" : "Back to home"}
        </Link>
        <h1 className="mt-4 font-heading text-3xl font-semibold">{title}</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Last updated: {updated}
        </p>
        {/* The pages write plain h2, ul and a tags: they are styled here, once. */}
        <div className="mt-6 space-y-4 leading-relaxed [&_a]:underline [&_h2]:mt-8 [&_h2]:font-heading [&_h2]:text-xl [&_h2]:font-semibold [&_ul]:list-disc [&_ul]:space-y-2 [&_ul]:pl-6">
          {children}
        </div>
      </main>
      <Footer />
    </div>
  );
}
