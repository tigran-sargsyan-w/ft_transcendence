import { Link } from "react-router";

export function Footer() {
  return (
    <footer className="border-t px-4 py-4 text-sm text-muted-foreground">
      <nav aria-label="Legal" className="flex justify-center gap-6">
        <Link to="/privacy" className="hover:text-foreground hover:underline">
          Privacy Policy
        </Link>
        <Link to="/terms" className="hover:text-foreground hover:underline">
          Terms of Service
        </Link>
      </nav>
    </footer>
  );
}
