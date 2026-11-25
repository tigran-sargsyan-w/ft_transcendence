import { useAuth } from "./AuthContext";
import { Navigate, Outlet, useLocation } from "react-router";

export const RequireAuth = () => {
  const { auth } = useAuth();
  const location = useLocation();

  if (auth.status === "loading") return null;
  if (auth.status === "anonymous")
    return <Navigate to="/" replace state={{ from: location }} />;
  return <Outlet />;
};
