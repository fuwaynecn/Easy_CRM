let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

/**
 * “会话彻底失效”回调：仅在上层状态需要同步清理时使用。
 * 回调内不得再发起请求（会再次触发本回调，形成递归）。
 */
let onSessionExpired: (() => void) | null = null;

export function setSessionExpiredHandler(handler: (() => void) | null): void {
  onSessionExpired = handler;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let refreshing: Promise<boolean> | null = null;

export function tryRefresh(): Promise<boolean> {
  if (!refreshing) {
    refreshing = fetch("/api/v1/auth/refresh", {
      method: "POST",
      credentials: "include",
    })
      .then(async (resp) => {
        if (!resp.ok) {
          setAccessToken(null);
          onSessionExpired?.();
          return false;
        }
        const data = (await resp.json()) as { access_token?: unknown };
        if (typeof data.access_token !== "string") {
          setAccessToken(null);
          return false;
        }
        setAccessToken(data.access_token);
        return true;
      })
      .catch(() => {
        setAccessToken(null);
        onSessionExpired?.();
        return false;
      })
      .finally(() => {
        refreshing = null;
      });
  }
  return refreshing;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const send = (): Promise<Response> =>
    fetch(path, {
      ...init,
      credentials: "include",
      headers: {
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        ...(init.headers ?? {}),
      },
    });

  let resp = await send();
  let retriedAfterUnauthorized = false;
  if (resp.status === 401 && accessToken) {
    const ok = await tryRefresh();
    if (ok) {
      resp = await send();
      retriedAfterUnauthorized = true;
    }
  }
  // 刷新成功但重试仍 401：会话已失效，通知上层清理。
  // 没有 token 的 401（例如登录密码错误）不走这里，避免误报会话过期。
  if (retriedAfterUnauthorized && resp.status === 401) {
    onSessionExpired?.();
  }
  if (resp.status === 204) return undefined as T;

  const body = await resp.json().catch(() => null);
  if (!resp.ok) {
    const err = body as { code?: string; message?: string; detail?: unknown } | null;
    throw new ApiError(
      resp.status,
      err?.code ?? "error",
      err?.message ?? "请求失败",
      err?.detail,
    );
  }
  return body as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  del: (path: string) => request<void>(path, { method: "DELETE" }),
};
