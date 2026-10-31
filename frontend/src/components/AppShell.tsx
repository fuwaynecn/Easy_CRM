import { useState } from "react";
import type { ReactNode } from "react";
import { Navigate, NavLink } from "react-router-dom";

import { useAuth } from "../auth/AuthProvider";

/**
 * `end` 给「首页」与「机会」：
 * - `/` 是所有路径的前缀，不加 `end` 会让"首页"在每个页面都高亮；
 * - `/opportunities` 是 `/opportunities/board` 的前缀，不加会让看板页两个导航项同亮。
 */
const NAV: {
  to: string;
  label: string;
  end?: boolean;
  adminOnly?: boolean;
  managerOrAdmin?: boolean;
}[] = [
  { to: "/", label: "首页", end: true },
  { to: "/customers", label: "客户" },
  { to: "/pool", label: "公海" },
  { to: "/opportunities", label: "机会", end: true },
  { to: "/opportunities/board", label: "看板" },
  { to: "/reports", label: "报表" },
  { to: "/import", label: "导入", managerOrAdmin: true },
  { to: "/admin/pool-rules", label: "公海规则", adminOnly: true },
];

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);

  // 第二道保险：会话彻底失效时 client.ts 会回调 AuthProvider 把 user 清空，
  // RequireAuth 已在外层把人送回登录页；这里拦住"将来某个受保护页不套
  // RequireAuth"的情况，避免页面在无身份状态下继续渲染。
  if (user === null) return <Navigate to="/login" replace />;

  return (
    <div className="flex min-h-full flex-col">
      <header className="flex items-center justify-between gap-2 border-b border-slate-200 bg-white px-4 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            className="shrink-0 rounded p-1 text-slate-600 md:hidden"
            onClick={() => setOpen((v) => !v)}
            aria-label="菜单"
          >
            ☰
          </button>
          <span className="min-w-0 truncate font-semibold text-slate-900">Easy CRM</span>
        </div>
        <div className="flex min-w-0 items-center gap-3 text-sm text-slate-600">
          {/* 姓名可能是不可断行的长串：min-w-0 + truncate 保证它只在自己那一格里
              收缩并省略，不会把整个 header 顶宽（375px 无横向滚动条）。 */}
          <span className="min-w-0 truncate">{user?.display_name}</span>
          <button
            type="button"
            onClick={() => void logout()}
            className="shrink-0 rounded-md border border-slate-300 px-2.5 py-1 hover:bg-slate-100"
          >
            退出
          </button>
        </div>
      </header>
      <div className="flex flex-1 flex-col md:flex-row">
        <nav
          className={`${open ? "block" : "hidden"} w-full border-b border-slate-200 bg-white p-2 md:block md:w-48 md:border-b-0 md:border-r`}
        >
          {NAV.filter(
            (item) =>
              (!item.adminOnly || user?.role === "admin") &&
              // `managerOrAdmin`：隐藏「导入」等仅主管/管理员可用的项。
              // 隐藏本身不是权限，`ImportPage` 内还有一道页面级闸门。
              (!item.managerOrAdmin || user?.role !== "sales"),
          ).map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
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
