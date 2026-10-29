import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { errorMessage } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { Field } from "../components/Field";

export default function LoginPage() {
  const { user, loading, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // RequireAuth 把人踢到登录页时会带上原地址（含 query），登录成功后回到那里；
  // 没有就进客户列表。过滤掉非字符串与 /login 自身，避免登录页自我重定向成死循环。
  const fromState = (location.state as { from?: unknown } | null)?.from;
  const from =
    typeof fromState === "string" && !fromState.startsWith("/login") ? fromState : "/customers";

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
      navigate(from, { replace: true });
    } catch (err) {
      // 后端的中文文案原样展示：423 账号锁定的 message 里已带剩余分钟数，
      // 401/403/400 的 message 也是可直接给用户看的。
      setError(errorMessage(err, "登录失败，请重试"));
    } finally {
      setSubmitting(false);
    }
  }

  // bootstrap 未完成时不要先渲染可交互的登录表单，否则已登录用户刷新 /login
  // 会看到一闪而过的表单，还可能抢先输入（登录与在途 /me 的竞态）。
  if (loading) {
    return <div className="flex min-h-full items-center justify-center text-slate-500">加载中…</div>;
  }
  // 已登录用户再访问 /login 不停留在登录页，直接送到目标地址。
  if (user !== null) return <Navigate to={from} replace />;

  return (
    <div className="flex min-h-full items-center justify-center px-4">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm space-y-4 rounded-xl bg-white p-6 shadow-sm ring-1 ring-slate-200"
      >
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Easy CRM</h1>
          <p className="mt-1 text-sm text-slate-500">请使用公司分配的账号登录</p>
        </div>
        <Field
          label="登录名"
          value={username}
          autoComplete="username"
          onChange={(e) => setUsername(e.target.value)}
          required
        />
        <Field
          label="密码"
          type="password"
          value={password}
          autoComplete="current-password"
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        {error ? <p className="break-words text-sm text-red-600">{error}</p> : null}
        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-md bg-brand-500 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-60"
        >
          {submitting ? "登录中…" : "登录"}
        </button>
      </form>
    </div>
  );
}
