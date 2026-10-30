import type { ReactNode } from "react";

import { ApiError, errorMessage } from "../api/client";

interface QueryErrorProps {
  error: unknown;
  /** 非 403 时的兜底文案（后端 `message` 优先）。 */
  fallback: string;
  /**
   * 重试动作。只有 5xx 与网络异常才真的渲染按钮：4xx 是确定性错误，重试同样的
   * 请求只会再失败一次；403 越权更不能把重试摆到用户面前。
   */
  onRetry?: () => void;
  /** 追加在提示下方的内容（详情页的「返回客户列表」）。 */
  children?: ReactNode;
}

/**
 * 查询失败的统一错误块（列表页与详情页共用，此前详情页整段照抄列表页）。
 *
 * 403 分支的行为逐字保留：只显示「无权查看该客户」（后端给了 `message` 时用后端的），
 * **不渲染任何客户字段**、不重试、不登出、不跳登录页。404 同样不给重试。
 *
 * 两处调用方的 403 兜底文案本来就相同，所以这里固定成一个常量，避免哪天只改一处。
 */
export function QueryError({ error, fallback, onRetry, children }: QueryErrorProps) {
  // 401 与 403 都不是"重试就能过"的错误，文案也不能通用：401 是会话过期
  // （client.ts 的 refresh 已经试过一次），要明确让用户重新登录；403 是越权。
  const unauthorized = error instanceof ApiError && error.status === 401;
  const forbidden = error instanceof ApiError && error.status === 403;
  const message = unauthorized
    ? "登录已过期，请重新登录"
    : forbidden
      ? errorMessage(error, "无权查看该客户")
      : errorMessage(error, fallback);
  // ApiError 的 status 是 HTTP 状态码。不属于 ApiError 的错误是网络/DNS 中断（没有状态码），
  // 那种情况值得重试一次；0 是前端自产的契约错误（见 AuthProvider），同样不该重试。
  const retryable = !(error instanceof ApiError) || error.status >= 500;

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
      <p className="break-words text-red-600">{message}</p>
      {onRetry && retryable ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 rounded border border-slate-300 px-2 py-1"
        >
          重试
        </button>
      ) : null}
      {children}
    </div>
  );
}