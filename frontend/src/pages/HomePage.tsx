import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { errorMessage } from "../api/client";
import { completeFollowUp } from "../api/followUps";
import { getHome } from "../api/home";
import type { FollowUp, HomeSummary, PoolNoticeItem } from "../api/types";
import { FollowUpDoneModal } from "../components/FollowUpDoneModal";
import { QueryError } from "../components/QueryError";
import { formatClock } from "../constants/datetime";

/**
 * 首页（design §6.1）：今日待办、已逾期、即将进公海三块。
 *
 * 三块的口径全部在后端（`app/modules/home/service.py`）：阈值、豁免、"我的"范围
 * 都不在这里复算。前端只负责展示与触发完成动作——把口径搬到前端，等于让首页与
 * 后续的回收任务各算一套。
 */
export default function HomePage() {
  const queryClient = useQueryClient();
  const home = useQuery({ queryKey: ["home"], queryFn: getHome });
  const [doneTarget, setDoneTarget] = useState<FollowUp | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const done = useMutation({
    mutationFn: (input: { id: number; createActivity: boolean; activityType: string }) =>
      completeFollowUp(input.id, {
        create_activity: input.createActivity,
        // 不勾选就不传 activity_type（后端对"不勾选却指定方式"返回 400）
        ...(input.createActivity ? { activity_type: input.activityType } : {}),
      }),
    onSuccess: async () => {
      setDoneTarget(null);
      setActionError(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["home"] }),
        queryClient.invalidateQueries({ queryKey: ["follow_ups"] }),
        queryClient.invalidateQueries({ queryKey: ["timeline"] }),
        queryClient.invalidateQueries({ queryKey: ["customer"] }),
        // 勾选"顺便写一条跟进"会推进 last_activity_at，客户列表的「最后跟进」也要新
        queryClient.invalidateQueries({ queryKey: ["customers"] }),
      ]);
    },
    onError: (err) => setActionError(errorMessage(err, "标记完成失败")),
  });

  if (home.isLoading) {
    return <p className="text-sm text-slate-500">加载中…</p>;
  }

  if (home.isError) {
    return (
      <QueryError
        error={home.error}
        fallback="首页加载失败"
        onRetry={() => void home.refetch()}
      />
    );
  }

  const data: HomeSummary | undefined = home.data;
  if (!data) return <p className="text-sm text-slate-500">加载中…</p>;

  const openDone = (item: FollowUp) => {
    setActionError(null);
    setDoneTarget(item);
  };

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-slate-900">今日工作</h1>

      <Section
        title="今日待办"
        total={data.today.total}
        shown={data.today.items.length}
        empty="今天没有待办。"
      >
        <FollowUpRows items={data.today.items} overdue={false} onDone={openDone} />
      </Section>

      <Section
        title="已逾期"
        total={data.overdue.total}
        shown={data.overdue.items.length}
        empty="没有逾期的待办，保持住。"
      >
        <FollowUpRows items={data.overdue.items} overdue onDone={openDone} />
      </Section>

      <Section
        title="即将进公海"
        total={data.pool_notice.total}
        shown={data.pool_notice.items.length}
        empty="暂无客户进入预警区间。"
        hint={
          <p className="text-xs text-slate-400">
            {data.pool_notice.rule.inactive_days} 天无跟进进入回收区间，提前{" "}
            {data.pool_notice.rule.recycle_notice_days} 天预警；有在谈机会或未完成待办的客户不预警。
            规则将于 P4 可配置。
          </p>
        }
      >
        <PoolNoticeRows items={data.pool_notice.items} />
      </Section>

      {doneTarget ? (
        <FollowUpDoneModal
          followUp={doneTarget}
          pending={done.isPending}
          error={actionError}
          onClose={() => {
            setDoneTarget(null);
            setActionError(null);
          }}
          onSubmit={(createActivity, activityType) =>
            done.mutate({ id: doneTarget.id, createActivity, activityType })
          }
        />
      ) : null}
    </div>
  );
}

interface SectionProps {
  title: string;
  total: number;
  shown: number;
  empty: string;
  hint?: ReactNode;
  children: ReactNode;
}

function Section({ title, total, shown, empty, hint, children }: SectionProps) {
  return (
    <section className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
        <span className="text-xs text-slate-500">
          共 {total} 条
          {total > shown ? `（显示前 ${shown} 条）` : ""}
        </span>
      </div>
      {total === 0 ? (
        <p className="break-words rounded-md bg-slate-50 p-3 text-sm text-slate-500">{empty}</p>
      ) : (
        children
      )}
      {hint}
    </section>
  );
}

function FollowUpRows({
  items,
  overdue,
  onDone,
}: {
  items: FollowUp[];
  overdue: boolean;
  onDone: (item: FollowUp) => void;
}) {
  return (
    <ul className="divide-y divide-slate-100">
      {items.map((item) => (
        <li key={item.id} className="flex items-start justify-between gap-2 py-2">
          <div className="min-w-0 space-y-0.5">
            <p className="break-words text-sm text-slate-900">{item.content}</p>
            <p className="break-words text-xs text-slate-500">
              <Link to={`/customers/${item.customer_id}`} className="text-brand-600 hover:underline">
                {item.customer_name}
              </Link>
              {" · "}
              {overdue ? "已逾期" : `${formatClock(item.due_at)} 前`}
            </p>
          </div>
          <button
            type="button"
            onClick={() => onDone(item)}
            className="shrink-0 rounded-md border border-slate-300 px-2.5 py-1 text-sm hover:bg-slate-100"
          >
            完成
          </button>
        </li>
      ))}
    </ul>
  );
}

function PoolNoticeRows({ items }: { items: PoolNoticeItem[] }) {
  return (
    <ul className="divide-y divide-slate-100">
      {items.map((item) => (
        <li key={item.id} className="flex items-start justify-between gap-2 py-2">
          <div className="min-w-0 space-y-0.5">
            <Link
              to={`/customers/${item.id}`}
              className="break-words text-sm font-medium text-brand-600 hover:underline"
            >
              {item.name}
            </Link>
            <p className="break-words text-xs text-slate-500">
              负责人 {item.owner_name ?? "未分配"} · 已 {item.days_inactive} 天无跟进
            </p>
          </div>
          <span className="shrink-0 rounded bg-amber-50 px-2 py-1 text-xs text-amber-700">
            即将进公海
          </span>
        </li>
      ))}
    </ul>
  );
}