import { api } from "./client";
import type { User } from "./types";

export interface LoginResult {
  access_token: string;
  expires_in: number;
  user: User;
}

export function loginRequest(username: string, password: string) {
  return api.post<LoginResult>("/api/v1/auth/login", { username, password });
}

export function meRequest() {
  return api.get<User>("/api/v1/me");
}

export function logoutRequest() {
  return api.post<void>("/api/v1/auth/logout");
}
