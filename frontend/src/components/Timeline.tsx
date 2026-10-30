import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";

import { deleteActivity } from "../api/activities";
import { errorMessage } from "../api/client";
import { getTimeline } from "../api/timeline";
import type { TimelineItem } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { activityTypeLabel } from "../constants/activityLabels";
import { formatDateTime } from "../constants/datetime";
import { STAGE_LABELS, formatAmount } from "../constants/opportunityLabels";
import type { ActivityDraft } from "./ActivityFormModal";
import { Modal } from "./Modal";

interface Props {
  customerId: number;
  /**
   * 客户当前是否可写（公海只读，或 admin 例外）。**必须**参与行内按钮判定：
   * `mine` 只说明"这条是我写的"，不说明"我现在还能改"——公海客户的历史跟进
   * 对非 admin 是只读的，露出按钮等于把用户领进必定 403 的操作。
   */
  writable: boolean;
  /** 编辑由父组件开 ActivityFormModal（时间线不拥有写表单） */
  onEdit: (draft: ActivityDraft) => void;
}

/**
 * 客户时间线（spec §5 `GET /customers/{id}/timeline`）。
 *
 * 两类条目（跟进 / 阶段变更）由后端合并排序，前端只按 `type` 换渲染：
 * - 已删机会、已删联系人、已停用账号**照常显示**并打灰色标签（P3 裁定 1）：
 *   历史就是历史，隐藏掉比标出来更容易让人误判"这段没发生过"。
 * - 已删对象**不给链接**：点进去必然 404，比没有链接更糟。机会虽然没删，但 P3
 *   没有机会详情路由，同样不给假链接（见报告）。联系人本就没有单条页。
 * - 金额用 `amount_snapshot`（阶段变更当刻的定点字符串），**不读**机会现值。
 * - 游标原样回传，前端不解析（裁定 14）。
 */
export function Timeline({ customerId, writable, onEdit }: Props) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const timeline = useInfiniteQuery({
    queryKey: ["timeline", customerId],
    queryFn: ({ pageParam }) => getTimeline(customerId, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });

  const remove = useMutation({
    mutationFn: (id: number) => deleteActivity(id),
    onSuccess: async () => {
      setDeleteTarget(null);
      setDeleteError(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["timeline", customerId] }),
        queryClient.invalidateQueries({ queryKey: ["home"] }),
      ]);
    },
    onError: (err) => setDeleteError(errorMessage(err, "删除跟进失败")),
  });

  if (timeline.isLoading) return <p className="text-sm text-slate-500">加载中…</p>;

  if (timeline.isError) {
    return (
      <div className="space-y-2">
        <p className="break-words text-sm text-red-600">
          {errorMessage(timeline.error, "时间线加载失败")}
        </p>
        <button
          type="button"
          onClick={() => void timeline.refetch()}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        >
          重试
        </button>
      </div>
    );
  }

  const items = timeline.data?.pages.flatMap((page) => page.items) ?? [];

  if (items.length === 0) {
    return (
      <p className="break-words rounded-md bg-slate-50 p-3 text-sm text-slate-500">
        还没有跟进记录。写下第一通电话或第一次拜访，客户的时间线就从这里开始。
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <ol className="space-y-3">
        {items.map((item) => {
          // 行内按钮要同时满足三条：客户此刻可写（公海客户对非 admin 只读）、这条是
          // 跟进（阶段变更不可编辑）、且是我写的或是管理员。
          // 主管删本部门记录的规则需要部门归属，前端不猜；后端仍按裁定 5 放行/拒绝。
          const mine = item.operator_id !== null && item.operator_id === user?.id;
          const canTouch =
            writable && item.type === "activity" && (mine || user?.role === "admin");
          return (
            <li key={`${item.type}-${item.id}`} className="flex gap-3">
              <span className="mt-1 h-fit shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">
                {item.type === "activity" ? activityTypeLabel(item.activity_type) : labelOfType(item)}
              </span>
              <div className="min-w-0 flex-1 space-y-1">
                <p className="break-words text-sm text-slate-900">{summarize(item)}</p>
                <div className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 break-words text-xs text-slate-500">
                  <span className="break-words">{item.operator_name}</span>
                  {item.operator_disabled ? <MutedTag>账号已停用</MutedTag> : null}
                  <span>· {formatDateTime(item.occurred_at)}</span>
                  {item.contact_name ? (
                    <span className="break-words">· 联系人 {item.contact_name}</span>
                  ) : null}
                  {/* 联系人没有单条页、已删的更不能点：一律纯文本 + 灰色标签 */}
                  {item.contact_deleted ? <MutedTag>联系人已删除</MutedTag> : null}
                  {item.opportunity_name ? (
                    <span className="break-words">· {item.opportunity_name}</span>
                  ) : null}
                  {item.opportunity_deleted ? <MutedTag>机会已删除</MutedTag> : null}
                  {item.type === "stage_change" && item.amount_snapshot ? (
                    <span className="break-words">
                      · ¥{formatAmount(item.amount_snapshot)}
                    </span>
                  ) : null}
                </div>
                {canTouch ? (
                  <div className="flex items-center gap-3 text-xs">
                    <button
                      type="button"
                      onClick={() =>
                        onEdit({
                          id: item.id,
                          // `activity_type` 对 activity 条目恒非空；兜底 "other" 只为类型收窄
                          type: item.activity_type ?? "other",
                          content: item.content ?? "",
                          activity_at: item.occurred_at,
                          contact_id: item.contact_id,
                          opportunity_id: item.opportunity_id,
                        })
                      }
                      className="text-slate-500 hover:text-brand-600"
                    >
                      编辑
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setDeleteError(null);
                        setDeleteTarget(item.id);
                      }}
                      className="text-slate-400 hover:text-red-600"
                    >
                      删除
                    </button>
                  </div>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>

      {timeline.hasNextPage ? (
        <button
          type="button"
          disabled={timeline.isFetchingNextPage}
          onClick={() => void timeline.fetchNextPage()}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100 disabled:opacity-60"
        >
          {timeline.isFetchingNextPage ? "加载中…" : "加载更多"}
        </button>
      ) : (
        <p className="text-xs text-slate-400">没有更多了</p>
      )}

      {deleteTarget !== null ? (
        <Modal title="删除跟进记录" onClose={() => setDeleteTarget(null)}>
          <div className="space-y-3">
            <p className="text-sm text-slate-700">
              删除后这条记录不再出现在时间线里，且无法恢复。确定删除吗？
            </p>
            {deleteError ? (
              <p className="break-words text-sm text-red-600">{deleteError}</p>
            ) : null}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100"
              >
                取消
              </button>
              <button
                type="button"
                disabled={remove.isPending}
                onClick={() => remove.mutate(deleteTarget)}
                className="rounded-md bg-red-600 px-3 py-1.5 text-sm text-white disabled:opacity-60"
              >
                {remove.isPending ? "删除中…" : "删除"}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}

/** 灰色标签：只表示"这条记录的关联对象已不在"，不是可点元素。 */
function MutedTag({ children }: { children: ReactNode }) {
  return (
    <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">
      {children}
    </span>
  );
}

function labelOfType(item: TimelineItem): string {
  return item.type === "owner_change" ? "负责人变更" : "阶段";
}

function summarize(item: TimelineItem): string {
  if (item.type === "activity") return item.content ?? "";
  if (item.type === "owner_change") {
    return `${item.operator_name} 变更了负责人`;
  }
  const from = item.from_stage ? (STAGE_LABELS[item.from_stage] ?? item.from_stage) : "创建";
  const to = item.to_stage ? (STAGE_LABELS[item.to_stage] ?? item.to_stage) : "—";
  return `${from} → ${to}`;
}