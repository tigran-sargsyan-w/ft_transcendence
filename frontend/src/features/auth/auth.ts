import { api } from "@/services/api";
import type { User } from "@/types/user";

export interface Credentials {
  email: string;
  password: string;
}

export const registerUser = (input: Credentials) =>
  api<User>("/auth/register", { method: "POST", body: JSON.stringify(input) });

export const loginUser = (input: Credentials) =>
  api<User>("/auth/login", { method: "POST", body: JSON.stringify(input) });

export const logoutUser = () => api<void>("/auth/logout", { method: "POST" });

export const getMe = () => api<User>("/auth/me");
