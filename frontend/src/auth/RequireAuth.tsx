import { Navigate, useLocation } from "react-router-dom";
import type { ReactNode } from "react";

import { useAuth } from "./AuthProvider";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return <div className="flex h-full items-center justify-center text-slate-500">加载中…</div>;
  }
  // 严格判定：只有拿到真实 user 才放行，undefined 不能被当成已登录。
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}
