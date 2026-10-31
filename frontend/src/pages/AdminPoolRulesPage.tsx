import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { errorMessage } from "../api/client";
import { getPoolRules, updatePoolRules } from "../api/pools";
import { QueryError } from "../components/QueryError";
import { useAuth } from "../auth/AuthProvider";

/**
 * 公海规则（spec §3.11 / §4.1）。
 *
 * 保存后必须让首页与公海列表重算：阈值改了，预警区间与“即将进公海”都会变。
 * 后端 PATCH 返回完整规则，前端直接用返回值回写，不自己拼。
 */
export default function AdminPoolRulesPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const rules = useQuery({
    queryKey: ["pool-rules"],
    queryFn: getPoolRules,
    // 非 admin 打这个接口必然 403；无权限文案已在下面分支给出，不必先打一次注定失败的 GET。
    // `RequireAuth` 保证进入本页时 user 已就绪，故 enabled 不会因 user 未加载而抖动。
    enabled: user?.role === "admin",
  });
  const [inactiveDays, setInactiveDays] = useState("30");
  const [noticeDays, setNoticeDays] = useState("3");
  const [maxOwned, setMaxOwned] = useState("50");
  const [autoRecycle, setAutoRecycle] = useState(true);
  const [protectDeal, setProtectDeal] = useState(true);
  const [protectFollowUp, setProtectFollowUp] = useState(true);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!rules.data) return;
    setInactiveDays(String(rules.data.inactive_days));
    setNoticeDays(String(rules.data.recycle_notice_days));
    setMaxOwned(String(rules.data.max_owned));
    setAutoRecycle(rules.data.auto_recycle_enabled);
    setProtectDeal(rules.data.protect_with_open_deal);
    setProtectFollowUp(rules.data.protect_with_pending_followup);
  }, [rules.data]);

  const save = useMutation({
    mutationFn: () =>
      updatePoolRules({
        inactive_days: Number(inactiveDays),
        recycle_notice_days: Number(noticeDays),
        max_owned: Number(maxOwned),
        auto_recycle_enabled: autoRecycle,
        protect_with_open_deal: protectDeal,
        protect_with_pending_followup: protectFollowUp,
      }),
    onMutate: () => {
      setError(null);
      setSaved(false);
    },
    onSuccess: async (updated) => {
      queryClient.setQueryData(["pool-rules"], updated);
      setSaved(true);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["home"] }),
        queryClient.invalidateQueries({ queryKey: ["pool"] }),
        queryClient.invalidateQueries({ queryKey: ["assignable-users"] }),
      ]);
    },
    onError: (err) => setError(errorMessage(err, "保存失败")),
  });

  // 三个数字框必须都有值：`Number("") === 0`，清空后提交会被后端当成合法值吞掉
  // （如 `recycle_notice_days: 0` 等于静默关掉首页预警），所以在提交与按钮上双重拦住。
  const numericReady =
    inactiveDays.trim() !== "" && noticeDays.trim() !== "" && maxOwned.trim() !== "";

  if (user?.role !== "admin") {
    return <p className="text-sm text-red-600">只有管理员可以修改公海规则。</p>;
  }

  if (rules.isLoading) return <p className="text-sm text-slate-500">加载中…</p>;

  if (rules.isError) {
    return (
      <QueryError
        error={rules.error}
        fallback="公海规则加载失败"
        onRetry={() => void rules.refetch()}
      />
    );
  }

  return (
    <div className="max-w-2xl space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-slate-900">公海规则</h1>
        <p className="mt-1 text-sm text-slate-500">
          改完保存后，首页预警、公海判定与分配上限立即按新值计算。
        </p>
      </div>

      <form
        className="space-y-4 rounded-lg border border-slate-200 bg-white p-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (!numericReady) return;
          save.mutate();
        }}
      >
        <label className="block">
          <span className="mb-1 block text-sm font-medium text-slate-700">
            无跟进回收天数 *
          </span>
          <input
            type="number"
            min={1}
            max={3650}
            required
            value={inactiveDays}
            onChange={(event) => setInactiveDays(event.target.value)}
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
          <span className="mt-1 block text-xs text-slate-400">
            客户连续 N 天无任何跟进（活动或完成待办）后进入回收候选。
          </span>
        </label>

        <label className="block">
          <span className="mb-1 block text-sm font-medium text-slate-700">
            提前预警天数 *
          </span>
          <input
            type="number"
            min={0}
            max={3650}
            required
            value={noticeDays}
            onChange={(event) => setNoticeDays(event.target.value)}
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
          <span className="mt-1 block text-xs text-slate-400">
            首页「即将进公海」提前 M 天出现；必须小于回收天数。
          </span>
        </label>

        <label className="block">
          <span className="mb-1 block text-sm font-medium text-slate-700">
            每人客户上限 *
          </span>
          <input
            type="number"
            min={1}
            max={10000}
            required
            value={maxOwned}
            onChange={(event) => setMaxOwned(event.target.value)}
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
          <span className="mt-1 block text-xs text-slate-400">
            分配/领取时超过上限返回 409；不影响已有客户。
          </span>
        </label>

        <label className="flex items-start gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={autoRecycle}
            onChange={(event) => setAutoRecycle(event.target.checked)}
            className="mt-1"
          />
          <span>启用每日自动回收（关闭后仅可手工触发回收）</span>
        </label>

        <label className="flex items-start gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={protectDeal}
            onChange={(event) => setProtectDeal(event.target.checked)}
            className="mt-1"
          />
          <span>有未关闭机会的客户不回收、不预警</span>
        </label>

        <label className="flex items-start gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={protectFollowUp}
            onChange={(event) => setProtectFollowUp(event.target.checked)}
            className="mt-1"
          />
          <span>有未完成待办的客户不回收、不预警</span>
        </label>

        {error ? (
          <p className="break-words rounded-md bg-red-50 p-2 text-sm text-red-600">{error}</p>
        ) : null}
        {saved ? <p className="text-sm text-green-600">已保存。</p> : null}

        <button
          type="submit"
          disabled={save.isPending || !numericReady}
          className="rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
        >
          {save.isPending ? "保存中…" : "保存"}
        </button>
      </form>
    </div>
  );
}