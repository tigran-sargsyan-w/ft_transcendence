import { api } from "@/services/api";

export interface Credentials {
  email: string;
  password: string;
}

export interface User {
  id: string;
  email: string;
  createdAt: string;
  updatedAt: string;
}

export const registerUser = (input: Credentials) =>
  api<User>("/auth/regiser", { method: "POST", body: JSON.stringify(input) });

export const loginUser = (input: Credentials) =>
  api<User>("auth/login", { method: "POST", body: JSON.stringify(input) });

export const logoutUser = () => api<void>("auth/logout", { method: "POST" });

export const getMe = () => api<User>("/auth/me");
