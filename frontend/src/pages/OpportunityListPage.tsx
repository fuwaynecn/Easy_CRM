import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { errorMessage } from "../api/client";
import { changeStage, listOpportunities } from "../api/opportunities";
import type { Opportunity } from "../api/types";
import { OpportunityFormModal } from "../components/OpportunityFormModal";
import { QueryError } from "../components/QueryError";
import {
  STAGE_BADGE_CLASS,
  STAGE_LABELS,
  STAGE_ORDER,
  formatAmount,
  formatDateOnly,
  isTerminalStage,
} from "../constants/opportunityLabels";

/**
 * 机会列表（design §5）。
 *
 * 列表本身只做展示与两个动作：编辑（走 PATCH）与「推进」（走 /stage）。
 * 阶段变更没有放进编辑弹窗——它要写 `stage_histories`，只能走专用端点；
 * 完整的输单/重开流程属看板页（Task 9）。
 */
export default function OpportunityListPage() {
  const queryClient = useQueryClient();
  const [keyword, setKeyword] = useState("");
  const [stage, setStage] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Opportunity | null>(null);

  const query = useQuery({
    queryKey: ["opportunities", keyword, stage, page],
    queryFn: () => listOpportunities({ keyword, stage, page, page_size: 20 }),
    placeholderData: keepPreviousData,
  });

  const advance = useMutation({
    // 目标是推进阶段（非 lost）时，输单字段必须显式传 null：后端对
    // 「非 lost 却带非空 lost_reason_id / lost_note」直接 400，传 null 才是明确的意思。
    // `note` 与 `lost_note` 是两码事：`note` 记进阶段历史（重开时必填），
    // `lost_note` 记进机会本身（输单备注），普通推进两者都不写。
    mutationFn: ({ id, next }: { id: number; next: string }) =>
      changeStage(id, { stage: next, lost_reason_id: null, lost_note: null }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["opportunities"] });
      await queryClient.invalidateQueries({ queryKey: ["board"] });
    },
  });

  const data = query.data;
  // 页码一律以服务端回显为准（后端可能把越界页钳制回最后一页）。
  const currentPage = data?.page ?? page;
  const pageCount = Math.max(1, Math.ceil((data?.total ?? 0) / Math.max(1, data?.page_size ?? 20)));

  // 越界回落：停止筛选后第 2 页只剩 1 条、这条又被推进出当前筛选范围时，
  // 本地 page 会停在一个不存在的页上——分页栏因为 total 变小而消失，用户就卡在
  // 只有「暂无机会」的死胡同里。与 CustomerListPage 同一写法。
  // 占位数据（keepPreviousData）是上一组查询的结果，不能拿它来同步页码。
  useEffect(() => {
    if (!data || query.isPlaceholderData) return;
    if (data.page !== page) {
      setPage(data.page);
      return;
    }
    if (data.items.length === 0 && page > 1) setPage(pageCount);
  }, [data, page, pageCount, query.isPlaceholderData]);

  function stageBadge(value: string) {
    return (
      <span className={`inline-flex shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${STAGE_BADGE_CLASS[value] ?? "bg-slate-100 text-slate-700"}`}>
        {STAGE_LABELS[value] ?? value}
      </span>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <input
          aria-label="搜索机会"
          value={keyword}
          onChange={(e) => {
            setPage(1);
            setKeyword(e.target.value);
          }}
          // 后端 keyword 同时命中机会名称、竞争对手与所属客户名称
          placeholder="搜索机会名称 / 客户 / 竞争对手"
          className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm sm:w-72"
        />
        <select
          aria-label="按阶段筛选"
          value={stage}
          onChange={(e) => {
            setPage(1);
            setStage(e.target.value);
          }}
          className="rounded-md border border-slate-300 px-3 py-2 text-sm"
        >
          <option value="">全部阶段</option>
          {STAGE_ORDER.map((value) => (
            <option key={value} value={value}>
              {STAGE_LABELS[value]}
            </option>
          ))}
        </select>
        {/* 看板页属 Task 9：这里先给入口，链接到尚未实现的路由会落到 * 兜底，
            与 P1 的「先有链接后有页面」同一做法。 */}
        <Link
          to="/opportunities/board"
          className="rounded-md border border-slate-300 px-3 py-2 text-sm hover:bg-slate-100"
        >
          看板视图
        </Link>
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="ml-auto rounded-md bg-brand-500 px-3 py-2 text-sm font-medium text-white hover:bg-brand-600"
        >
          + 新建机会
        </button>
      </div>

      {query.isLoading ? <p className="text-sm text-slate-500">加载中…</p> : null}
      {query.isError ? (
        <QueryError error={query.error} fallback="加载失败，请重试" onRetry={() => void query.refetch()} />
      ) : null}
      {/* 推进是行内动作，失败提示放在列表上方：用户刚点的那一行可能已经滚出视野 */}
      {advance.isError ? (
        <p className="break-words text-sm text-red-600">
          {errorMessage(advance.error, "阶段变更失败，请重试")}
        </p>
      ) : null}

      {/* 出错时连占位数据一起藏起来：状态切换期间不能把上一个查询的机会留在屏幕上 */}
      {query.isError ? null : (
        <>
          <div className="hidden overflow-x-auto rounded-lg border border-slate-200 bg-white md:block">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-slate-500">
                <tr>
                  <th className="px-3 py-2 font-medium">机会名称</th>
                  <th className="px-3 py-2 font-medium">客户</th>
                  <th className="px-3 py-2 font-medium">阶段</th>
                  <th className="px-3 py-2 font-medium">金额</th>
                  <th className="px-3 py-2 font-medium">预计成交</th>
                  <th className="px-3 py-2 font-medium">负责人</th>
                  <th className="px-3 py-2 font-medium">赢单率</th>
                  <th className="px-3 py-2 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {(data?.items ?? []).map((item) => (
                  <tr key={item.id} className="border-t border-slate-100 hover:bg-slate-50">
                    <td className="break-words px-3 py-2">{item.name}</td>
                    <td className="break-words px-3 py-2">
                      <Link to={`/customers/${item.customer_id}`} className="text-brand-600 hover:underline">
                        {item.customer_name}
                      </Link>
                    </td>
                    <td className="px-3 py-2">{stageBadge(item.stage)}</td>
                    {/* 金额只格式化、不做数值运算：后端保证两位小数的定点字符串 */}
                    <td className="whitespace-nowrap px-3 py-2">{formatAmount(item.amount)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-slate-600">
                      {formatDateOnly(item.expected_close_date)}
                    </td>
                    <td className="break-words px-3 py-2 text-slate-600">{item.owner_name}</td>
                    <td className="whitespace-nowrap px-3 py-2">
                      {item.win_rate}%{item.win_rate_manual ? <span className="ml-1 text-xs text-slate-400">手</span> : null}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-xs">
                      <button type="button" onClick={() => setEditing(item)} className="text-slate-500 hover:text-brand-600">
                        编辑
                      </button>
                      {/* 只有未关闭的机会能一键前进到下一阶段；终态要走看板里的重开流程 */}
                      {isTerminalStage(item.stage) ? null : (
                        <button
                          type="button"
                          // 用 isPending 守卫：同一行的双击会发出两个 POST，第二个必然
                          // 400「机会已处于该阶段」，红字看起来像真出错了。
                          disabled={advance.isPending}
                          className="ml-2 text-slate-500 hover:text-brand-600 disabled:opacity-40"
                          onClick={() => {
                            if (advance.isPending) return;
                            const index = (STAGE_ORDER as readonly string[]).indexOf(item.stage);
                            const next = STAGE_ORDER[index + 1];
                            if (!next) return;
                            // 赢单是终态且会写入 won_at（之后只能由 manager/admin 重开），
                            // 点错一下就要走重开流程，所以先确认。
                            if (next === "won" && !window.confirm("确认标记为赢单？此操作会写入赢单时间")) {
                              return;
                            }
                            advance.mutate({ id: item.id, next });
                          }}
                        >
                          推进
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="space-y-2 md:hidden">
            {(data?.items ?? []).map((item) => (
              <div key={item.id} className="space-y-1 rounded-lg border border-slate-200 bg-white p-3">
                <div className="flex min-w-0 items-center justify-between gap-2">
                  <span className="min-w-0 break-words font-medium text-slate-900">{item.name}</span>
                  {stageBadge(item.stage)}
                </div>
                <p className="break-words text-xs text-slate-500">
                  <Link to={`/customers/${item.customer_id}`} className="text-brand-600">
                    {item.customer_name}
                  </Link>
                  {" · "}
                  {formatAmount(item.amount)} · {formatDateOnly(item.expected_close_date)}
                </p>
                <p className="break-words text-xs text-slate-500">
                  负责人：{item.owner_name} · 赢单率 {item.win_rate}%
                </p>
                <button
                  type="button"
                  onClick={() => setEditing(item)}
                  className="text-xs text-slate-500 hover:text-brand-600"
                >
                  编辑
                </button>
              </div>
            ))}
          </div>

          {/* 空态放在桌面/移动两条分支之外：md 以下桌面容器是 display:none，
              空态若留在里面，移动端搜不到结果时会看到整页空白。 */}
          {data && data.items.length === 0 ? (
            <p className="rounded-lg border border-slate-200 bg-white p-6 text-center text-sm text-slate-500">
              暂无机会
            </p>
          ) : null}

          {data && data.total > data.page_size ? (
            <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-slate-600">
              <span>
                共 {data.total} 条，第 {currentPage} / {pageCount} 页
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={currentPage <= 1}
                  onClick={() => setPage(Math.max(1, currentPage - 1))}
                  className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40"
                >
                  上一页
                </button>
                <button
                  type="button"
                  disabled={currentPage >= pageCount}
                  onClick={() => setPage(currentPage + 1)}
                  className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40"
                >
                  下一页
                </button>
              </div>
            </div>
          ) : null}
        </>
      )}

      {creating ? (
        <OpportunityFormModal mode="create" onClose={() => setCreating(false)} />
      ) : null}
      {editing ? (
        <OpportunityFormModal mode="edit" opportunity={editing} onClose={() => setEditing(null)} />
      ) : null}
    </div>
  );
}
