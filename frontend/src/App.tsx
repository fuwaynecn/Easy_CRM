import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { AppShell } from "./components/AppShell";
import CustomerDetailPage from "./pages/CustomerDetailPage";
import CustomerListPage from "./pages/CustomerListPage";
import LoginPage from "./pages/LoginPage";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/" element={<Navigate to="/customers" replace />} />
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
            <Route path="*" element={<Navigate to="/customers" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}