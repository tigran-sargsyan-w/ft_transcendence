import { useAuth } from "./AuthContext";
import { Navigate, Outlet } from "react-router";

export const RequireAuth = () => {
  const { auth } = useAuth();

  if (auth.status === "loading") return null;
  if (auth.status === "anonymous")
    return <Navigate to="/" replace state={{ from: location }} />;
  return <Outlet />;
};
