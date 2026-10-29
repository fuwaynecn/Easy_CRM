import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { loginRequest, logoutRequest, meRequest } from "../api/auth";
import type { LoginResult } from "../api/auth";
import { ApiError, setAccessToken, setSessionExpiredHandler, tryRefresh } from "../api/client";
import type { User } from "../api/types";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

/** 后端返回 200 但结构不对时，不能直接信任类型断言的结果。 */
function isLoginResult(value: unknown): value is LoginResult {
  if (typeof value !== "object" || value === null) return false;
  const body = value as { access_token?: unknown; user?: unknown };
  if (typeof body.access_token !== "string") return false;
  const user = body.user;
  if (typeof user !== "object" || user === null) return false;
  return typeof (user as { id?: unknown }).id === "number";
}

/**
 * StrictMode 下 effect 会执行两次，`/me` 不像 `tryRefresh` 那样有单飞去重，会白打
 * 一次请求。这里合并同一时刻的在途调用；请求结束即释放，不缓存结果——否则退出后
 * 换账号登录会读到上一个人的 user。
 */
let meInFlight: Promise<User> | null = null;

function meRequestOnce(): Promise<User> {
  if (meInFlight === null) {
    meInFlight = meRequest().finally(() => {
      meInFlight = null;
    });
  }
  return meInFlight;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  // 会话彻底失效（刷新失败、或刷新后重试仍 401）时清掉本地登录态，
  // 让 RequireAuth 把用户送回登录页。回调内不发请求，避免递归触发。
  useEffect(() => {
    setSessionExpiredHandler(() => {
      setUser(null);
    });
    return () => {
      setSessionExpiredHandler(null);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const refreshed = await tryRefresh();
      if (refreshed) {
        try {
          const me = await meRequestOnce();
          if (!cancelled) setUser(me);
        } catch {
          // 读取当前用户失败即视为未登录：token 一并清掉，失败即清干净。
          setAccessToken(null);
          if (!cancelled) setUser(null);
        }
      }
      if (!cancelled) setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      async login(username: string, password: string) {
        const result: unknown = await loginRequest(username, password);
        if (!isLoginResult(result)) {
          setAccessToken(null);
          setUser(null);
          throw new ApiError(500, "invalid_response", "登录响应异常，请稍后重试");
        }
        setAccessToken(result.access_token);
        setUser(result.user);
      },
      async logout() {
        // 先清本地状态再通知后端：后端无响应时用户也必须能退出。
        setAccessToken(null);
        setUser(null);
        void logoutRequest().catch(() => undefined);
      },
    }),
    [user, loading],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (ctx === null) throw new Error("useAuth 必须在 AuthProvider 内使用");
  return ctx;
}
