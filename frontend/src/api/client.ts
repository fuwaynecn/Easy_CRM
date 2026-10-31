let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
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

/**
 * 框架自产错误（404/405/422 等）没有 `message` 字段，或后端给了非字符串/空串，
 * 都不能让界面渲染出 `[object Object]` 或一个空气泡——按状态码给一句中文兜底。
 */
function fallbackMessage(status: number): string {
  switch (status) {
    case 403:
      return "无权执行该操作";
    case 404:
      return "记录不存在或已被删除";
    case 422:
      return "提交内容不合规，请检查后重试";
    case 423:
      return "账号已锁定，请稍后重试";
    default:
      return "请求失败";
  }
}

/** 取一段可直接展示的中文文案：优先后端 `message`，没有可用文案时用调用方兜底。 */
export function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError && err.message.trim() !== "") return err.message;
  return fallback;
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
        // 只有字符串 body 才是我们序列化过的 JSON；`FormData` 上传必须让浏览器
        // 自己写 multipart 边界，手写 Content-Type 会让后端解析失败。
        ...(typeof init.body === "string"
          ? { "Content-Type": "application/json" }
          : {}),
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        ...(init.headers ?? {}),
      },
    });

  // 只有 401 才算会话失效。403（越权）与 423（账号锁定）是必须让用户看见的
  // 业务错误：它们在这里既不刷新、也不登出，由调用方按 `status` 渲染中文提示。
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
    // 逐字段做运行时校验：`as` 断言管不了脏响应，message 不是非空字符串时
    // 直接用它构造 Error 会渲染成 "[object Object]" 或空文本。
    const err = body as { code?: unknown; message?: unknown; detail?: unknown } | null;
    const rawMessage = err?.message;
    const code = typeof err?.code === "string" && err.code !== "" ? err.code : "error";
    const message =
      typeof rawMessage === "string" && rawMessage.trim() !== ""
        ? rawMessage
        : fallbackMessage(resp.status);
    throw new ApiError(resp.status, code, message, err?.detail);
  }
  return body as T;
}

/**
 * 下载二进制响应（模板 `.xlsx` 等）：不能走 `request`——它无条件 `resp.json()`，
 * 会把二进制读坏并吞掉错误文案。401 刷新逻辑与 `request` 保持一致。
 */
async function requestBlob(path: string): Promise<Blob> {
  const send = (): Promise<Response> =>
    fetch(path, {
      credentials: "include",
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    });

  let resp = await send();
  if (resp.status === 401 && accessToken) {
    const ok = await tryRefresh();
    if (ok) resp = await send();
  }
  if (!resp.ok) {
    const body = await resp.json().catch(() => null);
    const err = body as { code?: unknown; message?: unknown; detail?: unknown } | null;
    const rawMessage = err?.message;
    const code = typeof err?.code === "string" && err.code !== "" ? err.code : "error";
    const message =
      typeof rawMessage === "string" && rawMessage.trim() !== ""
        ? rawMessage
        : fallbackMessage(resp.status);
    throw new ApiError(resp.status, code, message, err?.detail);
  }
  return resp.blob();
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
  upload: <T>(path: string, body: FormData) =>
    request<T>(path, { method: "POST", body }),
  blob: (path: string) => requestBlob(path),
};
