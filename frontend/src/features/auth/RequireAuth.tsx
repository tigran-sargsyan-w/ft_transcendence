import { useEffect, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router";
import { getMe } from "./auth";

export const RequireAuth = () => {
  const location = useLocation();
  const [state, setState] = useState<"loading" | "ok" | "anonymous">("loading");

  useEffect(() => {
    getMe()
      .then(() => setState("ok"))
      .catch(() => setState("anonymous"));
  }, []);

  if (state === "loading") return null;
  if (state === "anonymous")
    return <Navigate to="/" replace state={{ from: location }} />;
  return <Outlet />;
};
