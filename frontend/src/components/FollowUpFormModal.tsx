import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { errorMessage } from "../api/client";
import { createFollowUp } from "../api/followUps";
import type { Contact, Opportunity } from "../api/types";
import { defaultDueInput, fromDateTimeInput } from "../constants/datetime";
import { Modal } from "./Modal";

interface Props {
  customerId: number;
  contactOptions: Contact[];
  opportunityOptions: Opportunity[];
  onClose: () => void;
  onSaved: () => void;
}

/**
 * 新建待办（spec §4.5）。到期时间含时分：首页按上海自然日切分，只到日期会让
 * "明天"这条到底算不算今天产生歧义。
 *
 * 关联对象只能是**这个客户**的联系人/机会：选项由详情页传入（已按客户过滤），
 * 不在这里再拉一次全量列表。
 */
export function FollowUpFormModal({
  customerId,
  contactOptions,
  opportunityOptions,
  onClose,
  onSaved,
}: Props) {
  const queryClient = useQueryClient();
  const [due, setDue] = useState(defaultDueInput());
  const [content, setContent] = useState("");
  const [contactId, setContactId] = useState("");
  const [opportunityId, setOpportunityId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () =>
      createFollowUp({
        customer_id: customerId,
        due_at: fromDateTimeInput(due),
        content: content.trim(),
        contact_id: contactId === "" ? null : Number(contactId),
        opportunity_id: opportunityId === "" ? null : Number(opportunityId),
      }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["follow_ups"] }),
        queryClient.invalidateQueries({ queryKey: ["home"] }),
      ]);
      onSaved();
    },
    onError: (err) => setError(errorMessage(err, "保存待办失败")),
  });

  function submit() {
    if (content.trim() === "") {
      setFieldError("请填写待办内容");
      return;
    }
    if (due === "") {
      setFieldError("请选择计划时间");
      return;
    }
    setFieldError(null);
    setError(null);
    save.mutate();
  }

  return (
    <Modal title="新建待办" onClose={onClose}>
      <div className="space-y-3">
        <label className="block space-y-1">
          <span className="text-sm text-slate-600">计划时间</span>
          <input
            type="datetime-local"
            value={due}
            onChange={(event) => setDue(event.target.value)}
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
        </label>

        <label className="block space-y-1">
          <span className="text-sm text-slate-600">要做什么</span>
          <textarea
            rows={3}
            value={content}
            onChange={(event) => setContent(event.target.value)}
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            placeholder="例如：把样册送到王工手上"
          />
          {fieldError ? <span className="text-xs text-red-600">{fieldError}</span> : null}
        </label>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="block space-y-1">
            <span className="text-sm text-slate-600">关联联系人（可选）</span>
            <select
              value={contactId}
              onChange={(event) => setContactId(event.target.value)}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
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
              onChange={(event) => setOpportunityId(event.target.value)}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
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