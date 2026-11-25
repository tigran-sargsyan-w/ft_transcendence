import { Link } from "react-router";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/features/auth/AuthContext";

export default function NotFound() {
  const { auth } = useAuth();
  const isLoggedIn = auth.status === "authenticated";

  return (
    <main className="flex min-h-svh flex-col items-center justify-center gap-4 p-6 text-center">
      <p className="text-sm font-medium text-muted-foreground">404</p>
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <p className="max-w-sm text-muted-foreground">
        The page you are looking for doesn't exist or has been moved.
      </p>
      <Button asChild>
        <Link to={isLoggedIn ? "/dashboard" : "/"}>
          {isLoggedIn ? "Back to dashboard" : "Back to home"}
        </Link>
      </Button>
    </main>
  );
}
