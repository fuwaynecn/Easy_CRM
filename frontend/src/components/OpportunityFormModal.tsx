import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { errorMessage } from "../api/client";
import { listCustomers } from "../api/customers";
import { createOpportunity, updateOpportunity } from "../api/opportunities";
import type { OpportunityCreateBody, OpportunityUpdateBody } from "../api/opportunities";
import type { OpenStage, Opportunity } from "../api/types";
import { isOpenStage, OPEN_STAGES, STAGE_LABELS } from "../constants/opportunityLabels";
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
  /**
   * 只有新建时才提交阶段。收窄成 `OpenStage` 而不是 `string`：终态（won/lost）
   * 只能由 `/opportunities/{id}/stage` 产生，创建体根本不该有它们的类型位置。
   */
  stage: OpenStage;
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
    // 编辑态不提交 stage（下拉禁用，显示直接用 opportunity.stage），这里只需给
    // OpenStage 表单一个合法初值；终态落回 contact 不会被提交，见 parseForm 的调用点。
    stage: isOpenStage(opportunity.stage) ? opportunity.stage : "contact",
  };
}

/** 赢单率是 0–100 的整数百分比：`50%`、`五成`、`50.5`、`10 0` 都不合法。 */
const WIN_RATE_PATTERN = /^\d{1,3}$/;

/** 金额：非负、整数或最多两位小数（匹配前先去掉千分位逗号与空白，见 parseForm）。 */
const AMOUNT_PATTERN = /^\d+(\.\d{1,2})?$/;

/** 字段级校验结果：哪个字段红了，就在对应输入框下面显示哪一句。 */
interface FieldErrors {
  amount?: string;
  win_rate?: string;
}

type ParsedForm =
  | {
      ok: true;
      name: string;
      amount: string;
      expectedCloseDate: string | null;
      competitor: string | null;
      winRate: number | null;
    }
  | { ok: false; errors: FieldErrors };

/**
 * 表单 → 请求体字段的统一出口，**校验与解析在同一个函数里**。
 *
 * 这样写是为了堵死一条静默写坏数据的路径：上一版在 `mutationFn` 里直接
 * `Number(form.win_rate)`，用户填 `50%`（标签就写着「赢单率 %」，填百分号完全
 * 合理）得到 `NaN`，`JSON.stringify` 把它序列化成 `null`，后端再把 `win_rate:
 * null` 读成「回到跟随阶段」并返回 200——手填的赢单率被清空且毫无提示。
 *
 * 现在 `win_rate` 的数值只有一个构造点，就在正则通过之后；不合法时返回的载荷里
 * 根本没有这个字段，调用方拿不到可提交的 `NaN`，也不需要记得先校验。**注意这是
 * 结构上的保证，不是类型系统给的**（`number` 本身就包含 `NaN`），所以要靠
 * 「校验与解析同一个函数、mutationFn 只接收成品载荷」这条结构钉住。
 *
 * 空值仍然合法：赢单率空 = 跟随阶段（null），金额空 = 0。
 */
function parseForm(form: FormState): ParsedForm {
  const errors: FieldErrors = {};

  // 列表里金额显示成 1,800,000.00，用户会照抄着填回来；后端 Decimal 解析不了逗号
  // （直接 422），所以先去掉千分位逗号与所有空白，再按定点小数校验。
  const amount = form.amount.replace(/[,\s]/g, "");
  if (amount !== "" && !AMOUNT_PATTERN.test(amount)) {
    errors.amount = "金额请填写数字，最多两位小数";
  }

  const rawWinRate = form.win_rate.trim();
  if (rawWinRate !== "" && (!WIN_RATE_PATTERN.test(rawWinRate) || Number(rawWinRate) > 100)) {
    errors.win_rate = "赢单率请填 0-100 的整数";
  }

  if (errors.amount || errors.win_rate) return { ok: false, errors };

  return {
    ok: true,
    name: form.name.trim(),
    amount: amount === "" ? "0" : amount,
    // 清空日期必须传 null（后端只认 null 表示清空）；空串会被 422 拦下
    expectedCloseDate: form.expected_close_date || null,
    competitor: form.competitor.trim() || null,
    winRate: rawWinRate === "" ? null : Number(rawWinRate),
  };
}

/** mutation 的入参：载荷在 `submit()` 里一次构造完，mutationFn 只负责发请求。 */
type SubmitPayload =
  | { kind: "create"; body: OpportunityCreateBody }
  | { kind: "edit"; id: number; body: OpportunityUpdateBody };

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
  // 本地字段校验：与后端错误分开存，本地红字在对应输入框下面，后端错误在底部
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
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
    // 载荷是已校验过的成品（见 parseForm）：这里既不转数字、也不拼字段，
    // 所以「非法输入被静默写坏」没有第二次发生的入口。
    mutationFn: (payload: SubmitPayload) =>
      payload.kind === "create"
        ? createOpportunity(payload.body)
        : updateOpportunity(payload.id, payload.body),
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

  /**
   * 保存前先做字段级本地校验：不合法就在弹窗内给红字并**阻止提交**。
   * 不依赖后端 422——那条路上用户只看到笼统的「提交内容不合规」，而赢单率填
   * `50%` 更糟：后端 200，值被静默清空。
   */
  function submit() {
    const parsed = parseForm(form);
    if (!parsed.ok) {
      setFieldErrors(parsed.errors);
      return;
    }
    setFieldErrors({});
    const body = {
      name: parsed.name,
      amount: parsed.amount,
      expected_close_date: parsed.expectedCloseDate,
      competitor: parsed.competitor,
      win_rate: parsed.winRate,
    };
    if (mode === "edit" && opportunity) {
      mutation.mutate({ kind: "edit", id: opportunity.id, body });
      return;
    }
    if (selectedCustomer === null) {
      setError("请先选择客户");
      return;
    }
    mutation.mutate({
      kind: "create",
      body: { ...body, customer_id: selectedCustomer.id, stage: form.stage },
    });
  }

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
            error={fieldErrors.amount}
            onChange={(e) => {
              setForm({ ...form, amount: e.target.value });
              // 改动即撤掉旧红字：提示停在已经改过的输入框下面会让人以为还没改好
              setFieldErrors((prev) => ({ ...prev, amount: undefined }));
            }}
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
            error={fieldErrors.win_rate}
            onChange={(e) => {
              setForm({ ...form, win_rate: e.target.value });
              setFieldErrors((prev) => ({ ...prev, win_rate: undefined }));
            }}
          />
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-slate-700">阶段</span>
            <select
              // 编辑态显示机会自己的阶段（可能是终态），新建态显示表单值：
              // 编辑不提交 stage，两个来源不会打架。
              value={mode === "edit" && opportunity ? opportunity.stage : form.stage}
              // 编辑时不允许在这里改阶段：改阶段必须走 /stage，后端 PATCH 也不接受 stage
              disabled={mode === "edit"}
              onChange={(e) => {
                const value = e.target.value;
                if (isOpenStage(value)) setForm({ ...form, stage: value });
              }}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm disabled:bg-slate-100"
            >
              {OPEN_STAGES.map((stage) => (
                <option key={stage} value={stage}>
                  {STAGE_LABELS[stage]}
                </option>
              ))}
              {/* 编辑终态机会时当前阶段不在 OPEN_STAGES 里，补一个选项，否则下拉会
                  显示成空白，看着像「阶段丢了」。只在**不在**推进阶段里时补：否则
                  OPEN_STAGES.map 已经渲染过同值 option，下拉里会出现两行一样的。 */}
              {mode === "edit" && opportunity && !isOpenStage(opportunity.stage) ? (
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
            onClick={submit}
            className="rounded-md bg-brand-500 px-3 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-60"
          >
            {mutation.isPending ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
