import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { errorMessage } from "../api/client";
import { listCustomers } from "../api/customers";
import { createOpportunity, updateOpportunity } from "../api/opportunities";
import type { Opportunity } from "../api/types";
import { OPEN_STAGES, STAGE_LABELS } from "../constants/opportunityLabels";
import { Field } from "./Field";
import { Modal } from "./Modal";

interface Props {
  mode: "create" | "edit";
  /** 编辑时的原值；创建时不传。 */
  opportunity?: Opportunity;
  /** 固定客户（从客户详情页进来时用）：不再显示客户选择器。 */
  fixedCustomer?: { id: number; name: string };
  onClose: () => void;
}

interface FormState {
  name: string;
  amount: string;
  expected_close_date: string;
  competitor: string;
  win_rate: string;
  stage: string;
}

const EMPTY_FORM: FormState = {
  name: "",
  // 金额在表单里也全程是字符串（后端 wire 就是 "1234.50"）。
  // 一旦用 number 存，0.1 + 0.2 那类误差就会在「打开-改名-保存」时被悄悄写回库里。
  amount: "0",
  expected_close_date: "",
  competitor: "",
  win_rate: "",
  stage: "contact",
};

function toForm(opportunity: Opportunity): FormState {
  return {
    name: opportunity.name,
    // 原样回填后端给的定点字符串，不经过 Number() 再 toFixed()
    amount: opportunity.amount,
    expected_close_date: opportunity.expected_close_date ?? "",
    competitor: opportunity.competitor ?? "",
    // 手工值才回填；跟随阶段的（win_rate_manual=false）留空，保存后仍是自动值
    win_rate: opportunity.win_rate_manual ? String(opportunity.win_rate) : "",
    stage: opportunity.stage,
  };
}

/**
 * 机会的新建/编辑弹窗。
 *
 * 两处类型层面的约束值得留意：
 * - 客户从这里选，但「负责人」不在这里选：后端从客户负责人继承（`extra="forbid"`，
 *   多传 `owner_id` 就是 422）。
 * - 编辑态不提交 `stage`，阶段下拉禁用：`PATCH` 不接受 `stage`，改阶段必须走
 *   `/opportunities/{id}/stage`，那条路径才会写 `stage_histories`。
 */
export function OpportunityFormModal({
  mode,
  opportunity,
  fixedCustomer,
  onClose,
}: Props) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<FormState>(
    opportunity ? toForm(opportunity) : EMPTY_FORM,
  );
  const [error, setError] = useState<string | null>(null);
  // 创建时必须选定客户：先按关键字搜，再从结果里点一条
  const [customerKeyword, setCustomerKeyword] = useState("");
  const [selectedCustomer, setSelectedCustomer] = useState<{ id: number; name: string } | null>(
    fixedCustomer ?? null,
  );

  const customerQuery = useQuery({
    queryKey: ["customer-options", customerKeyword],
    queryFn: () => listCustomers({ keyword: customerKeyword, page_size: 10 }),
    // 已固定客户或编辑时不查客户名录：编辑不改客户（后端也不接受 customer_id）
    enabled: mode === "create" && fixedCustomer === undefined,
  });

  const mutation = useMutation({
    mutationFn: () => {
      // 赢单率是 0–100 的整数百分比，不是金额：这里的 Number() 是合法的，
      // 别把它当成「金额也能转数字」的先例。
      const winRate = form.win_rate.trim() === "" ? null : Number(form.win_rate);
      if (mode === "edit" && opportunity) {
        return updateOpportunity(opportunity.id, {
          name: form.name.trim(),
          amount: form.amount.trim(),
          // 清空日期必须传 null（后端只认 null 表示清空）；空串会被 422 拦下
          expected_close_date: form.expected_close_date || null,
          competitor: form.competitor.trim() || null,
          win_rate: winRate,
        });
      }
      if (selectedCustomer === null) throw new Error("请先选择客户");
      return createOpportunity({
        customer_id: selectedCustomer.id,
        name: form.name.trim(),
        stage: form.stage,
        amount: form.amount.trim(),
        expected_close_date: form.expected_close_date || null,
        competitor: form.competitor.trim() || null,
        win_rate: winRate,
      });
    },
    onMutate: () => setError(null),
    onSuccess: async () => {
      // 列表与看板是两个查询键，都要失效：改金额/负责人会影响看板列头合计
      await queryClient.invalidateQueries({ queryKey: ["opportunities"] });
      await queryClient.invalidateQueries({ queryKey: ["board"] });
      onClose();
    },
    // 错误提示一律用后端返回的 message（越权/公海客户/参数不合规各有各的说法），
    // 前端不自己编一套，避免同一件事两处口径不一致。
    onError: (err) => setError(errorMessage(err, "保存失败，请重试")),
  });

  const customerLabel = fixedCustomer?.name ?? opportunity?.customer_name ?? "";

  return (
    <Modal title={mode === "create" ? "新建机会" : "编辑机会"} onClose={onClose}>
      <div className="space-y-3">
        {mode === "create" && fixedCustomer === undefined ? (
          <div className="space-y-2">
            <Field
              label="客户 *"
              value={customerKeyword}
              placeholder="输入客户名称搜索"
              onChange={(e) => {
                setCustomerKeyword(e.target.value);
                setSelectedCustomer(null);
              }}
            />
            {selectedCustomer ? (
              <p className="text-sm text-slate-700">
                已选择：<span className="font-medium">{selectedCustomer.name}</span>
              </p>
            ) : (
              <ul className="max-h-40 overflow-y-auto rounded-md border border-slate-200">
                {(customerQuery.data?.items ?? []).map((customer) => (
                  <li key={customer.id}>
                    <button
                      type="button"
                      onClick={() => setSelectedCustomer({ id: customer.id, name: customer.name })}
                      className="block w-full px-3 py-2 text-left text-sm hover:bg-slate-50"
                    >
                      {customer.name}
                      {/* 公海客户没有负责人，而后端裁定公海不能建机会（400）：
                          提前标出来，别让用户选完才吃一个报错。 */}
                      {customer.owner_id === null ? (
                        <span className="ml-2 text-xs text-amber-700">公海（不能建机会）</span>
                      ) : null}
                    </button>
                  </li>
                ))}
                {customerQuery.isSuccess && (customerQuery.data?.items ?? []).length === 0 ? (
                  <li className="px-3 py-2 text-sm text-slate-500">没有匹配的客户</li>
                ) : null}
              </ul>
            )}
          </div>
        ) : (
          <p className="text-sm text-slate-700">
            客户：<span className="font-medium">{customerLabel}</span>
          </p>
        )}

        <Field
          label="机会名称 *"
          maxLength={200}
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
        />
        <div className="grid grid-cols-2 gap-3">
          <Field
            label="预计金额"
            inputMode="decimal"
            value={form.amount}
            onChange={(e) => setForm({ ...form, amount: e.target.value })}
          />
          <Field
            label="预计成交日"
            type="date"
            value={form.expected_close_date}
            onChange={(e) => setForm({ ...form, expected_close_date: e.target.value })}
          />
        </div>
        <Field
          label="竞争对手"
          maxLength={200}
          value={form.competitor}
          onChange={(e) => setForm({ ...form, competitor: e.target.value })}
        />
        <div className="grid grid-cols-2 gap-3">
          <Field
            label="赢单率 %（留空跟随阶段）"
            inputMode="numeric"
            value={form.win_rate}
            onChange={(e) => setForm({ ...form, win_rate: e.target.value })}
          />
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-700">阶段</span>
            <select
              value={form.stage}
              // 编辑时不允许在这里改阶段：改阶段必须走 /stage，后端 PATCH 也不接受 stage
              disabled={mode === "edit"}
              onChange={(e) => setForm({ ...form, stage: e.target.value })}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm disabled:bg-slate-100"
            >
              {OPEN_STAGES.map((stage) => (
                <option key={stage} value={stage}>
                  {STAGE_LABELS[stage]}
                </option>
              ))}
              {/* 编辑终态机会时，当前阶段不在 OPEN_STAGES 里，补一个只读选项，
                  否则下拉会显示成空白，看着像「阶段丢了」。 */}
              {mode === "edit" && opportunity ? (
                <option value={opportunity.stage}>{STAGE_LABELS[opportunity.stage]}</option>
              ) : null}
            </select>
          </label>
        </div>
        {mode === "edit" ? (
          <p className="text-xs text-slate-500">
            改阶段请在看板或列表的「推进」里操作：每次阶段变更都会记一条历史。
          </p>
        ) : null}
        {error ? <p className="break-words text-sm text-red-600">{error}</p> : null}
        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-slate-300 px-3 py-2 text-sm"
          >
            取消
          </button>
          <button
            type="button"
            disabled={
              form.name.trim() === "" ||
              form.amount.trim() === "" ||
              mutation.isPending ||
              (mode === "create" && selectedCustomer === null)
            }
            onClick={() => mutation.mutate()}
            className="rounded-md bg-brand-500 px-3 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-60"
          >
            {mutation.isPending ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </Modal>
  );
}