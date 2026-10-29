import { useState } from "react";
import type { ReactNode } from "react";
import { Navigate, NavLink } from "react-router-dom";

import { useAuth } from "../auth/AuthProvider";

const NAV = [{ to: "/customers", label: "客户" }];

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);

  // 会话中途失效的统一兜底：client.ts 在 401 且刷新失败后只清了内存 token，
  // 这里在 user 变成 null 时把人送回登录页，避免用户卡在受保护页反复报错。
  if (user === null) return <Navigate to="/login" replace />;

  return (
    <div className="flex min-h-full flex-col">
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3">
        <div className="flex items-center gap-3">
          <button
            className="rounded p-1 text-slate-600 md:hidden"
            onClick={() => setOpen((v) => !v)}
            aria-label="菜单"
          >
            ☰
          </button>
          <span className="font-semibold text-slate-900">Easy CRM</span>
        </div>
        <div className="flex items-center gap-3 text-sm text-slate-600">
          <span>{user?.display_name}</span>
          <button
            onClick={() => void logout()}
            className="rounded-md border border-slate-300 px-2.5 py-1 hover:bg-slate-100"
          >
            退出
          </button>
        </div>
      </header>
      <div className="flex flex-1 flex-col md:flex-row">
        <nav
          className={`${open ? "block" : "hidden"} w-full border-b border-slate-200 bg-white p-2 md:block md:w-48 md:border-b-0 md:border-r`}
        >
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                `block rounded-md px-3 py-2 text-sm ${
                  isActive ? "bg-brand-50 font-medium text-brand-700" : "text-slate-700 hover:bg-slate-100"
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <main className="flex-1 p-4">{children}</main>
      </div>
    </div>
  );
}