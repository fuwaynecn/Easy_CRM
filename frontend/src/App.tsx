import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider, useAuth } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import LoginPage from "./pages/LoginPage";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

function HomePage() {
  const { user, logout } = useAuth();
  return (
    <div className="p-6">
      <h1 className="text-lg font-semibold">欢迎，{user?.display_name}</h1>
      <p className="mt-1 text-sm text-slate-500">角色：{user?.role}</p>
      <button
        onClick={() => void logout()}
        className="mt-4 rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100"
      >
        退出登录
      </button>
    </div>
  );
}

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
                  <HomePage />
                </RequireAuth>
              }
            />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}
