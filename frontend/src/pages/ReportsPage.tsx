import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { QueryError } from "../components/QueryError";
import { formatAmount, STAGE_LABELS } from "../constants/opportunityLabels";
import {
  getActivities,
  getCompetitors,
  getCycle,
  getFunnel,
  getLostReasons,
  getPerformance,
  getWinRate,
} from "../api/reports";

type Preset = "this_month" | "last_month" | "this_quarter" | "this_year" | "custom";

function shanghaiToday(): string {
  // en-CA 的短日期就是 YYYY-MM-DD，不需要拼字符串
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

function shiftMonth(iso: string, delta: number): string {
  const [year, month] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1 + delta, 1));
  return date.toISOString().slice(0, 10);
}

function monthEnd(iso: string): string {
  const [year, month] = iso.split("-").map(Number);
  return new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10);
}

function presetRange(preset: Preset): { from: string; to: string } {
  const today = shanghaiToday();
  if (preset === "this_month") {
    return { from: `${today.slice(0, 8)}01`, to: today };
  }
  if (preset === "last_month") {
    const start = shiftMonth(today, -1);
    return { from: start, to: monthEnd(start) };
  }
  if (preset === "this_quarter") {
    const month = Number(today.slice(5, 7));
    const quarterStartMonth = Math.floor((month - 1) / 3) * 3 + 1;
    return { from: `${today.slice(0, 4)}-${String(quarterStartMonth).padStart(2, "0")}-01`, to: today };
  }
  if (preset === "this_year") {
    return { from: `${today.slice(0, 4)}-01-01`, to: today };
  }
  return { from: `${today.slice(0, 8)}01`, to: today };
}

export default function ReportsPage() {
  const [preset, setPreset] = useState<Preset>("this_month");
  const [granularity, setGranularity] = useState<"day" | "week">("day");
  const initial = presetRange("this_month");
  const [from, setFrom] = useState(initial.from);
  const [to, setTo] = useState(initial.to);

  const applyPreset = (next: Preset) => {
    setPreset(next);
    if (next !== "custom") {
      const range = presetRange(next);
      setFrom(range.from);
      setTo(range.to);
    }
  };

  const funnel = useQuery({ queryKey: ["reports", "funnel"], queryFn: getFunnel });
  const winRate = useQuery({
    queryKey: ["reports", "win-rate", from, to],
    queryFn: () => getWinRate(from, to),
    enabled: Boolean(from && to),
  });
  const cycle = useQuery({
    queryKey: ["reports", "cycle", from, to],
    queryFn: () => getCycle(from, to),
    enabled: Boolean(from && to),
  });
  const performance = useQuery({
    queryKey: ["reports", "performance", from, to],
    queryFn: () => getPerformance(from, to),
    enabled: Boolean(from && to),
  });
  const activities = useQuery({
    queryKey: ["reports", "activities", from, to, granularity],
    queryFn: () => getActivities(from, to, granularity),
    enabled: Boolean(from && to),
  });
  const lostReasons = useQuery({
    queryKey: ["reports", "lost-reasons", from, to],
    queryFn: () => getLostReasons(from, to),
    enabled: Boolean(from && to),
  });
  const competitors = useQuery({
    queryKey: ["reports", "competitors", from, to],
    queryFn: () => getCompetitors(from, to),
    enabled: Boolean(from && to),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-lg font-semibold text-slate-900">报表</h1>
        <div className="flex flex-wrap items-end gap-2 text-sm">
          <select
            value={preset}
            onChange={(event) => applyPreset(event.target.value as Preset)}
            className="rounded-md border border-slate-300 px-2 py-1.5"
          >
            <option value="this_month">本月</option>
            <option value="last_month">上月</option>
            <option value="this_quarter">本季</option>
            <option value="this_year">本年</option>
            <option value="custom">自定义</option>
          </select>
          <input
            type="date"
            value={from}
            onChange={(event) => { setPreset("custom"); setFrom(event.target.value); }}
            className="min-w-0 rounded-md border border-slate-300 px-2 py-1.5"
          />
          <span className="text-slate-400">至</span>
          <input
            type="date"
            value={to}
            onChange={(event) => { setPreset("custom"); setTo(event.target.value); }}
            className="min-w-0 rounded-md border border-slate-300 px-2 py-1.5"
          />
        </div>
      </div>

      <Section title="漏斗（当前存量）">
        {funnel.isLoading ? <Loading /> : null}
        {funnel.isError ? (
          <QueryError error={funnel.error} fallback="漏斗加载失败" onRetry={() => void funnel.refetch()} />
        ) : null}
        {funnel.data ? <FunnelBars data={funnel.data} /> : null}
      </Section>

      <Section title="赢单率">
        {winRate.data ? (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Kpi label="赢单" value={String(winRate.data.won)} />
              <Kpi label="输单" value={String(winRate.data.lost)} />
              <Kpi label="合计" value={String(winRate.data.total)} />
              <Kpi label="赢单率" value={formatRate(winRate.data.rate)} />
            </div>
            <ByDepartment rows={winRate.data.by_department} />
          </div>
        ) : null}
        {winRate.isError ? <QueryError error={winRate.error} fallback="赢单率加载失败" onRetry={() => void winRate.refetch()} /> : null}
      </Section>

      <Section title="平均成交周期 / 销售业绩">
        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-md border border-slate-200 p-3">
            <p className="text-xs text-slate-500">平均成交周期</p>
            <p className="text-xl font-semibold text-slate-900">{formatDays(cycle.data?.avg_days)}</p>
            <SimpleTable
              headers={["负责人", "赢单数", "平均天数"]}
              rows={(cycle.data?.by_owner ?? []).map((row) => [row.owner_name, String(row.won_count), formatDays(row.avg_days)])}
            />
          </div>
          <div className="rounded-md border border-slate-200 p-3">
            <p className="text-xs text-slate-500">销售业绩（赢单金额）</p>
            <p className="text-xl font-semibold text-slate-900">
              {performance.data ? `¥${formatAmount(performance.data.total_amount)}` : "—"}
            </p>
            <SimpleTable
              headers={["负责人", "赢单数", "金额"]}
              rows={(performance.data?.by_owner ?? []).map((row) => [row.owner_name, String(row.won_count), `¥${formatAmount(row.amount)}`])}
            />
          </div>
        </div>
      </Section>

      <Section title="跟进量">
        {activities.data ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-sm text-slate-600">合计 {activities.data.total} 条</p>
              <select
                value={granularity}
                onChange={(event) => setGranularity(event.target.value as "day" | "week")}
                className="rounded-md border border-slate-300 px-2 py-1 text-sm"
              >
                <option value="day">按天</option>
                <option value="week">按周</option>
              </select>
            </div>
            <SeriesBars series={activities.data.series} />
            <SimpleTable
              headers={["跟进人", "条数"]}
              rows={activities.data.by_owner.map((row) => [row.user_name, String(row.count)])}
            />
          </div>
        ) : null}
        {activities.isError ? <QueryError error={activities.error} fallback="跟进量加载失败" onRetry={() => void activities.refetch()} /> : null}
      </Section>

      <Section title="输单原因">
        {lostReasons.data ? (
          <SimpleTable
            headers={["原因", "条数", "金额"]}
            rows={lostReasons.data.items.map((row) => [row.lost_reason_name, String(row.count), `¥${formatAmount(row.amount)}`])}
          />
        ) : null}
        {lostReasons.isError ? <QueryError error={lostReasons.error} fallback="输单原因加载失败" onRetry={() => void lostReasons.refetch()} /> : null}
      </Section>

      <Section title="竞争对手">
        {competitors.data ? (
          <SimpleTable
            headers={["对手", "机会数", "金额", "赢", "输", "赢单率"]}
            rows={competitors.data.items.map((row) => [
              row.competitor, String(row.count), `¥${formatAmount(row.amount)}`,
              String(row.won), String(row.lost), formatRate(row.rate),
            ])}
          />
        ) : null}
        {competitors.isError ? <QueryError error={competitors.error} fallback="竞争对手加载失败" onRetry={() => void competitors.refetch()} /> : null}
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2 rounded-lg border border-slate-200 bg-white p-3">
      <h2 className="text-sm font-semibold text-slate-800">{title}</h2>
      {children}
    </section>
  );
}

function Loading() {
  return <p className="text-sm text-slate-500">加载中…</p>;
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-slate-50 p-2">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="text-lg font-semibold text-slate-900">{value}</p>
    </div>
  );
}

function formatRate(rate: number | null): string {
  return rate === null ? "—" : `${rate}%`;
}

function formatDays(days: number | null | undefined): string {
  return days === null || days === undefined ? "—" : `${days} 天`;
}

function ByDepartment({ rows }: { rows: { department_id: number | null; department_name: string; won: number; lost: number; total: number; rate: number | null }[] }) {
  if (rows.length === 0) return <p className="text-sm text-slate-500">本期没有关闭的机会。</p>;
  return (
    <SimpleTable
      headers={["部门", "赢单", "输单", "赢单率"]}
      rows={rows.map((row) => [row.department_name, String(row.won), String(row.lost), formatRate(row.rate)])}
    />
  );
}

function SimpleTable({ headers, rows }: { headers: string[]; rows: string[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-left text-slate-500">
            {headers.map((header) => <th key={header} className="whitespace-nowrap px-2 py-1.5 font-medium">{header}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr><td className="px-2 py-2 text-slate-500" colSpan={headers.length}>暂无数据</td></tr>
          ) : rows.map((row, index) => (
            <tr key={index} className="border-b border-slate-100 last:border-0">
              {row.map((cell, cellIndex) => <td key={cellIndex} className="whitespace-nowrap px-2 py-1.5 text-slate-700">{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FunnelBars({ data }: { data: { stages: { stage: string; count: number; amount: string }[] } }) {
  const max = Math.max(...data.stages.map((row) => Number(row.amount)), 1);
  return (
    <div className="space-y-2">
      {data.stages.map((row) => (
        <div key={row.stage} className="space-y-1">
          <div className="flex justify-between text-sm">
            <span className="text-slate-700">{STAGE_LABELS[row.stage] ?? row.stage}</span>
            <span className="text-slate-500">{row.count} 条 · ¥{formatAmount(row.amount)}</span>
          </div>
          <div className="h-2 rounded bg-slate-100">
            <div className="h-2 rounded bg-brand-500" style={{ width: `${Math.min(100, (Number(row.amount) / max) * 100)}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function SeriesBars({ series }: { series: { bucket: string; count: number }[] }) {
  const max = Math.max(...series.map((row) => row.count), 1);
  return (
    <div className="flex h-24 items-end gap-0.5 overflow-hidden">
      {series.map((row) => (
        <div key={row.bucket} className="min-w-0 flex-1" title={`${row.bucket}：${row.count} 条`}>
          {/* brand-500：tailwind.config 只定义了 50/100/500/600/700，没有 400 */}
          <div className="rounded-t bg-brand-500" style={{ height: `${Math.max(2, (row.count / max) * 88)}px` }} />
        </div>
      ))}
    </div>
  );
}