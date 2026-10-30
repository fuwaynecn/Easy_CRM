import { useState } from "react";

import type { FollowUp } from "../api/types";
import { ACTIVITY_TYPE_LABELS, ACTIVITY_TYPE_OPTIONS } from "../constants/activityLabels";
import { formatDateTime } from "../constants/datetime";
import { Modal } from "./Modal";

interface Props {
  followUp: FollowUp;
  pending: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (createActivity: boolean, activityType: string) => void;
}

/**
 * 完成待办（spec §4.5）：勾了「顺便写一条跟进」才会生成活动并刷新客户新鲜度，
 * 所以复选框默认勾选、并且只有在勾选时才渲染跟进方式——不勾选时后端不接受
 * activity_type，前端也不该让它看起来可填。
 */
export function FollowUpDoneModal({ followUp, pending, error, onClose, onSubmit }: Props) {
  const [createActivity, setCreateActivity] = useState(true);
  const [activityType, setActivityType] = useState<string>("visit");

  return (
    <Modal title="完成待办" onClose={onClose}>
      <div className="space-y-3">
        <div className="rounded-md bg-slate-50 p-3">
          <p className="break-words text-sm text-slate-900">{followUp.content}</p>
          <p className="mt-1 break-words text-xs text-slate-500">
            {followUp.customer_name} · 原定 {formatDateTime(followUp.due_at)}
          </p>
        </div>

        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={createActivity}
            onChange={(event) => setCreateActivity(event.target.checked)}
          />
          顺便写一条跟进记录
        </label>

        {createActivity ? (
          <label className="block space-y-1">
            <span className="text-sm text-slate-600">跟进方式</span>
            <select
              value={activityType}
              onChange={(event) => setActivityType(event.target.value)}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            >
              {ACTIVITY_TYPE_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  {ACTIVITY_TYPE_LABELS[value]}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        {error ? <p className="break-words text-sm text-red-600">{error}</p> : null}

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100"
          >
            取消
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={() => onSubmit(createActivity, activityType)}
            className="rounded-md bg-brand-600 px-3 py-1.5 text-sm text-white disabled:opacity-60"
          >
            {pending ? "提交中…" : "标记完成"}
          </button>
        </div>
      </div>
    </Modal>
  );
}