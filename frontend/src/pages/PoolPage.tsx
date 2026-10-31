import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ApiError, errorMessage } from "../api/client";
import { claimCustomer, listPool } from "../api/pools";
import type { PoolCustomer } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { QueryError } from "../components/QueryError";
import { formatDay } from "../constants/datetime";

const PAGE_SIZE = 20;

/**
 * 公海列表（spec §3.11）。
 *
 * 领取只对 sales/manager 开放；admin 不领取，统一走客户详情的「分配」——
 * 后端对 admin 领取返回 403，前端不把按钮摆出来。
 */
export default function PoolPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [keyword, setKeyword] = useState("");
  const [submittedKeyword, setSubmittedKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [actionError, setActionError] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["pool", submittedKeyword, page],
    queryFn: () =>
      listPool({
        keyword: submittedKeyword || undefined,
        page,
        page_size: PAGE_SIZE,
      }),
  });

  const claim = useMutation({
    mutationFn: (customer: PoolCustomer) => claimCustomer(customer.id),
    onMutate: () => setActionError(null),
    onSuccess: async (customer) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["pool"] }),
        queryClient.invalidateQueries({ queryKey: ["customers"] }),
        queryClient.invalidateQueries({ queryKey: ["home"] }),
      ]);
      navigate(`/customers/${customer.id}`);
    },
    onError: (err) => {
      setActionError(errorMessage(err, "领取失败"));
      // 409 = 已被别人领走：这一行已经是死数据，失效 ["pool"] 让列表自己收敛，
      // 后端文案仍照常显示在红条里。
      if (err instanceof ApiError && err.status === 409) {
        void queryClient.invalidateQueries({ queryKey: ["pool"] });
      }
    },
  });

  const total = query.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // 总数缩小（别人领取/回收）导致当前页越界时收回最后一页，
  // 避免停在「第 2 / 1 页」且列表为空。
  // 只在数据已就绪时收敛：翻到第 2 页时 query.data 尚未返回（total 为 0，
  // totalPages 为 1），若无此守卫会把页码立刻打回第 1 页，第 2 页永远到不了。
  useEffect(() => {
    if (query.isSuccess && page > totalPages) setPage(totalPages);
  }, [page, totalPages, query.isSuccess]);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-slate-900">公海</h1>
        <p className="mt-1 text-sm text-slate-500">
          无负责人的客户。领取后归你所有；主管/管理员可在客户详情再分配。
        </p>
      </div>

      <form
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          setPage(1);
          setSubmittedKeyword(keyword.trim());
        }}
      >
        <input
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          placeholder="搜索客户名 / 简称"
          className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
        />
        <button
          type="submit"
          className="shrink-0 rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700"
        >
          搜索
        </button>
      </form>

      {actionError ? (
        <p className="break-words rounded-md bg-red-50 p-2 text-sm text-red-600">
          {actionError}
        </p>
      ) : null}

      {query.isLoading ? <p className="text-sm text-slate-500">加载中…</p> : null}

      {query.isError ? (
        <QueryError
          error={query.error}
          fallback="公海加载失败"
          onRetry={() => void query.refetch()}
        />
      ) : null}

      {query.isSuccess && total === 0 ? (
        <p className="rounded-md bg-slate-50 p-3 text-sm text-slate-500">
          {submittedKeyword ? "没有符合条件的客户。" : "公海暂时没有客户。"}
        </p>
      ) : null}

      <ul className="space-y-2">
        {(query.data?.items ?? []).map((customer) => (
          <li
            key={customer.id}
            className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-slate-200 bg-white p-3"
          >
            <div className="min-w-0 flex-1">
              <Link
                to={`/customers/${customer.id}`}
                className="break-words font-medium text-slate-900 hover:text-brand-700"
              >
                {customer.name}
              </Link>
              <p className="mt-1 break-words text-xs text-slate-500">
                {customer.industry ?? "—"} · {customer.region ?? "—"} · {customer.level} 级
                {customer.last_activity_at
                  ? ` · 最后跟进 ${formatDay(customer.last_activity_at)}`
                  : " · 从未跟进"}
                {customer.days_inactive !== null
                  ? ` · ${customer.days_inactive} 天未跟进`
                  : ""}
              </p>
            </div>
            {user?.role === "admin" ? (
              <span className="text-xs text-slate-400">管理员请到客户详情分配</span>
            ) : (
              <button
                type="button"
                disabled={claim.isPending}
                onClick={() => claim.mutate(customer)}
                className="shrink-0 rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
              >
                {claim.isPending && claim.variables?.id === customer.id ? "领取中…" : "领取"}
              </button>
            )}
          </li>
        ))}
      </ul>

      <div className="flex items-center justify-between text-sm text-slate-600">
        <button
          type="button"
          disabled={page <= 1 || query.isFetching}
          onClick={() => setPage((value) => value - 1)}
          className="rounded border border-slate-300 px-2.5 py-1 disabled:opacity-50"
        >
          上一页
        </button>
        <span>
          第 {page} / {totalPages} 页 · 共 {total} 条
        </span>
        <button
          type="button"
          disabled={page >= totalPages || query.isFetching}
          onClick={() => setPage((value) => value + 1)}
          className="rounded border border-slate-300 px-2.5 py-1 disabled:opacity-50"
        >
          下一页
        </button>
      </div>
    </div>
  );
}
