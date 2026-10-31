import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getOpportunity } from "../api/opportunities";
import { QueryError } from "../components/QueryError";
import { OpportunityFormModal } from "../components/OpportunityFormModal";
import { formatDateTime } from "../constants/datetime";
import {
  STAGE_BADGE_CLASS,
  STAGE_LABELS,
  formatAmount,
  formatDateOnly,
} from "../constants/opportunityLabels";

/**
 * 机会详情（P4 补 P3 已知限制 #7）。
 *
 * 只读展示 + 编辑弹窗；阶段推进仍在列表/看板，避免这里长出第二条写路径。
 * 公海客户的机会是只读的：后端 update_opportunity 会 403，前端不额外猜权限。
 */
export default function OpportunityDetailPage() {
  const { id } = useParams();
  const opportunityId = Number(id);
  const [editing, setEditing] = useState(false);

  const query = useQuery({
    // 挂在 ["opportunities"] 前缀下：OpportunityFormModal 保存后失效该前缀，
    // 详情页自动刷新，不需要额外的缓存键约定。
    queryKey: ["opportunities", "detail", opportunityId],
    queryFn: () => getOpportunity(opportunityId),
    enabled: Number.isInteger(opportunityId) && opportunityId > 0,
  });

  if (query.isLoading) return <p className="text-sm text-slate-500">加载中…</p>;

  if (query.isError) {
    return (
      <QueryError
        error={query.error}
        fallback="机会加载失败"
        onRetry={() => void query.refetch()}
      >
        <Link to="/opportunities" className="mt-2 block text-brand-600 hover:underline">
          返回机会列表
        </Link>
      </QueryError>
    );
  }

  const opportunity = query.data;
  if (!opportunity) {
    return <p className="text-sm text-slate-500">机会不存在。</p>;
  }

  return (
    <div className="space-y-4">
      <Link to="/opportunities" className="inline-block text-sm text-brand-600 hover:underline">
        ← 返回机会列表
      </Link>

      <section className="space-y-4 rounded-lg border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="break-words text-lg font-semibold text-slate-900">
              {opportunity.name}
            </h1>
            <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
              <span className={`rounded px-1.5 py-0.5 ${STAGE_BADGE_CLASS[opportunity.stage]}`}>
                {STAGE_LABELS[opportunity.stage]}
              </span>
              <span>· {formatAmount(opportunity.amount)}</span>
              <span>· 负责人 {opportunity.owner_name}</span>
            </p>
          </div>
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="shrink-0 rounded-md border border-slate-300 px-2.5 py-1 text-sm hover:bg-slate-100"
          >
            编辑
          </button>
        </div>

        <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-slate-500">所属客户</dt>
            <dd className="mt-0.5 break-words">
              <Link
                to={`/customers/${opportunity.customer_id}`}
                className="text-brand-600 hover:underline"
              >
                {opportunity.customer_name}
              </Link>
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">预计成交日</dt>
            <dd className="mt-0.5">
              {opportunity.expected_close_date
                ? formatDateOnly(opportunity.expected_close_date)
                : "—"}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">赢单率</dt>
            <dd className="mt-0.5">
              {opportunity.win_rate}%{opportunity.win_rate_manual ? "（手工）" : "（阶段默认）"}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">竞争对手</dt>
            <dd className="mt-0.5 break-words">{opportunity.competitor ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-slate-500">赢单时间</dt>
            <dd className="mt-0.5">
              {opportunity.won_at ? formatDateTime(opportunity.won_at) : "—"}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">创建 / 更新</dt>
            <dd className="mt-0.5 text-slate-600">
              {formatDateTime(opportunity.created_at)} · {formatDateTime(opportunity.updated_at)}
            </dd>
          </div>
          {opportunity.lost_reason_name ? (
            <div className="sm:col-span-2">
              <dt className="text-slate-500">输单原因</dt>
              <dd className="mt-0.5 break-words">
                {opportunity.lost_reason_name}
                {opportunity.lost_note ? ` · ${opportunity.lost_note}` : ""}
              </dd>
            </div>
          ) : null}
        </dl>
      </section>

      {editing ? (
        <OpportunityFormModal
          mode="edit"
          opportunity={opportunity}
          onClose={() => setEditing(false)}
        />
      ) : null}
    </div>
  );
}