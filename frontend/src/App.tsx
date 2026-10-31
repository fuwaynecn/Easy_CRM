import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { ApiError } from "./api/client";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { AppShell } from "./components/AppShell";
import CustomerDetailPage from "./pages/CustomerDetailPage";
import CustomerListPage from "./pages/CustomerListPage";
import HomePage from "./pages/HomePage";
import LoginPage from "./pages/LoginPage";
import OpportunityBoardPage from "./pages/OpportunityBoardPage";
import OpportunityDetailPage from "./pages/OpportunityDetailPage";
import OpportunityListPage from "./pages/OpportunityListPage";
import PoolPage from "./pages/PoolPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 只有 5xx 与网络异常值得重试：4xx 是确定性错误（403 越权、404 不存在、422 参数
      // 不合规），重试只会让用户多等一次同样的失败；status = 0 是前端自产的契约错误
      // （登录响应结构不对，见 AuthProvider），同样不重试。
      retry: (failureCount, error) =>
        failureCount < 1 && (!(error instanceof ApiError) || error.status >= 500),
      refetchOnWindowFocus: false,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route
              path="/"
              element={
                <RequireAuth>
                  <AppShell>
                    <HomePage />
                  </AppShell>
                </RequireAuth>
              }
            />
            {/* `/` 已经是真实首页，不再把它重定向到客户列表；`/home` 留个别名，
                避免旧书签/外部链接落到 404 兜底。 */}
            <Route path="/home" element={<Navigate to="/" replace />} />
            <Route
              path="/customers"
              element={
                <RequireAuth>
                  <AppShell>
                    <CustomerListPage />
                  </AppShell>
                </RequireAuth>
              }
            />
            <Route
              path="/customers/:id"
              element={
                <RequireAuth>
                  <AppShell>
                    <CustomerDetailPage />
                  </AppShell>
                </RequireAuth>
              }
            />
            <Route
              path="/pool"
              element={
                <RequireAuth>
                  <AppShell>
                    <PoolPage />
                  </AppShell>
                </RequireAuth>
              }
            />
            {/* 静态路径 /opportunities/board 与 /opportunities 互不遮蔽（Router 6 按
                具体度匹配），但两条放一起便于阅读；`*` 必须留在最后。 */}
            <Route
              path="/opportunities"
              element={
                <RequireAuth>
                  <AppShell>
                    <OpportunityListPage />
                  </AppShell>
                </RequireAuth>
              }
            />
            <Route
              path="/opportunities/board"
              element={
                <RequireAuth>
                  <AppShell>
                    <OpportunityBoardPage />
                  </AppShell>
                </RequireAuth>
              }
            />
            <Route
              path="/opportunities/:id"
              element={
                <RequireAuth>
                  <AppShell>
                    <OpportunityDetailPage />
                  </AppShell>
                </RequireAuth>
              }
            />
            <Route path="*" element={<Navigate to="/customers" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}
