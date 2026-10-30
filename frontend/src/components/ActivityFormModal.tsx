import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { createActivity, updateActivity } from "../api/activities";
import { errorMessage } from "../api/client";
import { listContacts } from "../api/contacts";
import { listOpportunities } from "../api/opportunities";
import type { ActivityType } from "../api/types";
import {
  ACTIVITY_TYPE_LABELS,
  ACTIVITY_TYPE_OPTIONS,
} from "../constants/activityLabels";
import {
  defaultDueInput,
  fromDateTimeInput,
  toDateTimeInput,
} from "../constants/datetime";
import { Modal } from "./Modal";

/**
 * 时间线路由"编辑"时只需要这几个字段：时间线行里没有完整 Activity（不返回
 * created_at/updated_at），硬凑一个 Activity 会骗过类型系统。
 */
export interface ActivityDraft {
  id: number;
  type: ActivityType;
  content: string;
  activity_at: string;
  contact_id: number | null;
  opportunity_id: number | null;
}

interface Props {
  customerId: number;
  /** 传入即为编辑模式：此时不渲染"下次跟进"（排待办只在新建时，裁定 11） */
  draft?: ActivityDraft;
  onClose: () => void;
  onSaved: () => void;
}

export function ActivityFormModal({ customerId, draft, onClose, onSaved }: Props) {
  const queryClient = useQueryClient();
  const editing = draft !== undefined;

  const [type, setType] = useState<ActivityType>(draft?.type ?? "call");
  const [content, setContent] = useState(draft?.content ?? "");
  const [activityAt, setActivityAt] = useState(
    toDateTimeInput(draft?.activity_at ?? new Date().toISOString()),
  );
  const [contactId, setContactId] = useState<string>(
    draft?.contact_id === null || draft?.contact_id === undefined
      ? ""
      : String(draft.contact_id),
  );
  const [opportunityId, setOpportunityId] = useState<string>(
    draft?.opportunity_id === null || draft?.opportunity_id === undefined
      ? ""
      : String(draft.opportunity_id),
  );
  const [nextDue, setNextDue] = useState(defaultDueInput());
  const [nextContent, setNextContent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  // 关联对象下拉：客户可见才有数据，客户不可见时这两个查询会 403，直接当作空列表。
  // 机会的 key 比详情页多一段 "options"：详情页只取 50 条，这里要 200 条，共用
  // 同一个 key 会"先跑的那个决定缓存内容"（React Query 只按 key 缓存，不认参数）
  const contacts = useQuery({
    queryKey: ["contacts", customerId],
    queryFn: () => listContacts(customerId),
  });
  const opportunities = useQuery({
    queryKey: ["opportunities", "customer", customerId, "options"],
    queryFn: () => listOpportunities({ customer_id: customerId, page_size: 200 }),
  });

  const save = useMutation({
    mutationFn: async () => {
      const activityAtIso = fromDateTimeInput(activityAt);
      if (editing) {
        // 关联对象不可改：编辑只提交三个可改字段（裁定 11）
        return updateActivity(draft.id, {
          type,
          content: content.trim(),
          activity_at: activityAtIso,
        });
      }
      const wantsTodo = nextContent.trim() !== "";
      return createActivity({
        customer_id: customerId,
        type,
        content: content.trim(),
        activity_at: activityAtIso,
        contact_id: contactId === "" ? null : Number(contactId),
        opportunity_id: opportunityId === "" ? null : Number(opportunityId),
        next_follow_up: wantsTodo
          ? { due_at: fromDateTimeInput(nextDue), content: nextContent.trim() }
          : null,
      });
    },
    onSuccess: async () => {
      // 写跟进会推进 last_activity_at、可能新增待办、必然新增时间线条目：
      // 这几个缓存一起失效，首页/客户列表/详情/时间线不会各自留旧数据。
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["timeline", customerId] }),
        queryClient.invalidateQueries({ queryKey: ["follow_ups"] }),
        queryClient.invalidateQueries({ queryKey: ["home"] }),
        queryClient.invalidateQueries({ queryKey: ["customer", customerId] }),
        // 客户列表的「最后跟进」读的是 customers.last_activity_at：不失效列表，
        // 用户得切页或等 staleTime 过期才看得到新日期。
        queryClient.invalidateQueries({ queryKey: ["customers"] }),
      ]);
      onSaved();
    },
    onError: (err) => setError(errorMessage(err, "保存跟进失败")),
  });

  function submit() {
    const errors: Record<string, string> = {};
    if (content.trim() === "") errors.content = "请填写跟进内容";
    if (activityAt === "") errors.activityAt = "请选择跟进时间";
    if (!editing && nextContent.trim() !== "" && nextDue === "") {
      errors.nextDue = "请选择下次跟进时间";
    }
    setFieldErrors(errors);
    setError(null);
    if (Object.keys(errors).length > 0) return;
    save.mutate();
  }

  const contactOptions = contacts.data ?? [];
  const opportunityOptions = opportunities.data?.items ?? [];

  return (
    <Modal title={editing ? "编辑跟进" : "写跟进"} onClose={onClose}>
      <div className="space-y-3">
        <label className="block space-y-1">
          <span className="text-sm text-slate-600">跟进方式</span>
          <select
            value={type}
            onChange={(event) => setType(event.target.value as ActivityType)}
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          >
            {ACTIVITY_TYPE_OPTIONS.map((value) => (
              <option key={value} value={value}>
                {ACTIVITY_TYPE_LABELS[value]}
              </option>
            ))}
          </select>
        </label>

        <label className="block space-y-1">
          <span className="text-sm text-slate-600">跟进时间</span>
          <input
            type="datetime-local"
            value={activityAt}
            onChange={(event) => setActivityAt(event.target.value)}
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
          {fieldErrors.activityAt ? (
            <span className="text-xs text-red-600">{fieldErrors.activityAt}</span>
          ) : null}
        </label>

        <label className="block space-y-1">
          <span className="text-sm text-slate-600">跟进内容</span>
          <textarea
            rows={4}
            value={content}
            onChange={(event) => setContent(event.target.value)}
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            placeholder="例如：电话确认交期，客户要求 11 月 15 日前到货"
          />
          {fieldErrors.content ? (
            <span className="text-xs text-red-600">{fieldErrors.content}</span>
          ) : null}
        </label>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="block space-y-1">
            <span className="text-sm text-slate-600">关联联系人（可选）</span>
            <select
              value={contactId}
              disabled={editing}
              onChange={(event) => setContactId(event.target.value)}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm disabled:bg-slate-100"
            >
              <option value="">不关联</option>
              {contactOptions.map((contact) => (
                <option key={contact.id} value={contact.id}>
                  {contact.name}
                </option>
              ))}
            </select>
          </label>

          <label className="block space-y-1">
            <span className="text-sm text-slate-600">关联机会（可选）</span>
            <select
              value={opportunityId}
              disabled={editing}
              onChange={(event) => setOpportunityId(event.target.value)}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm disabled:bg-slate-100"
            >
              <option value="">不关联</option>
              {opportunityOptions.map((opportunity) => (
                <option key={opportunity.id} value={opportunity.id}>
                  {opportunity.name}
                </option>
              ))}
            </select>
          </label>
        </div>

        {editing ? (
          <p className="text-xs text-slate-500">
            关联联系人与机会在创建后不可修改。
          </p>
        ) : (
          <div className="space-y-3 rounded-md border border-slate-200 p-3">
            <p className="text-sm font-medium text-slate-700">下次跟进（可选）</p>
            <label className="block space-y-1">
              <span className="text-xs text-slate-500">计划时间</span>
              <input
                type="datetime-local"
                value={nextDue}
                onChange={(event) => setNextDue(event.target.value)}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
              />
              {fieldErrors.nextDue ? (
                <span className="text-xs text-red-600">{fieldErrors.nextDue}</span>
              ) : null}
            </label>
            <label className="block space-y-1">
              <span className="text-xs text-slate-500">要做什么</span>
              <input
                type="text"
                value={nextContent}
                onChange={(event) => setNextContent(event.target.value)}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                placeholder="留空则不生成待办"
              />
            </label>
          </div>
        )}

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
            disabled={save.isPending}
            onClick={submit}
            className="rounded-md bg-brand-600 px-3 py-1.5 text-sm text-white disabled:opacity-60"
          >
            {save.isPending ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </Modal>
  );
}