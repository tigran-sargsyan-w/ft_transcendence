import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { getMe } from "./auth";
import type { User } from "@/types/user";

type AuthState =
  | { status: "loading" }
  | { status: "authenticated"; user: User }
  | { status: "anonymous" };

const AuthContext = createContext<{
  auth: AuthState;
  setUser: (user: User | null) => void;
} | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [auth, setAuth] = useState<AuthState>({ status: "loading" });

  useEffect(() => {
    getMe()
      .then((user) => setAuth({ status: "authenticated", user }))
      .catch(() => setAuth({ status: "anonymous" }));
  }, []);

  const setUser = (user: User | null) =>
    setAuth(user ? { status: "authenticated", user } : { status: "anonymous" });

  return (
    <AuthContext.Provider value={{ auth, setUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
