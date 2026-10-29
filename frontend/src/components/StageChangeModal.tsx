import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { errorMessage } from "../api/client";
import { changeStage, listLostReasons } from "../api/opportunities";
import type { Opportunity } from "../api/types";
import { OPEN_STAGES, STAGE_LABELS, STAGE_ORDER, isTerminalStage } from "../constants/opportunityLabels";
import { Field, TextArea } from "./Field";
import { Modal } from "./Modal";

interface Props {
  opportunity: Opportunity;
  /** 拖拽落到的目标阶段；点卡片进入时是机会当前阶段（不可选，只用于算默认值）。 */
  initialStage: string;
  onClose: () => void;
}

/**
 * 阶段变更弹窗：桌面拖拽、手机点选、点击卡片三个入口共用。
 *
 * 后端有三条硬规则，所以有些目标阶段必须在这里被排除或补输入，否则 400 会变成
 * 用户看不懂的一句错误：
 * - 同阶段再提交是 400；终态之间互转（won ↔ lost）也是 400 —— 选项里直接不出现；
 * - 进「输单」必须选输单原因（停用的原因后端也拒），这里不给空原因提交；
 * - 从终态重开必须填重开原因，且只有主管/管理员能做 —— 原因标了必填，权限交给后端判。
 *
 * `note`（阶段历史备注 / 重开原因）与 `lost_note`（输单说明，写进机会本身）是两个
 * 不同字段，所以是两个输入框，分别提交。
 */
export function StageChangeModal({ opportunity, initialStage, onClose }: Props) {
  const queryClient = useQueryClient();

  const reopening = isTerminalStage(opportunity.stage);
  // 重开只能回到推进阶段；普通变更排除当前阶段（两者都是后端会 400 的路）
  const availableStages: readonly string[] = reopening
    ? OPEN_STAGES
    : STAGE_ORDER.filter((value) => value !== opportunity.stage);

  // 传入的目标不是合法选项时（点开已赢单/已输单的卡片、点开当前阶段），
  // 留空让用户显式挑一个——随便猜一个默认值等于把"猜错的一步"直接提交上去。
  const [stage, setStage] = useState<string>(
    availableStages.includes(initialStage) ? initialStage : "",
  );
  const [note, setNote] = useState("");
  const [lostReasonId, setLostReasonId] = useState("");
  const [lostNote, setLostNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const reasonsQuery = useQuery({
    queryKey: ["lost-reasons"],
    queryFn: listLostReasons,
    // 只有进输单才需要字典：别的目标阶段不该为这个下拉多发一次请求
    enabled: stage === "lost",
  });

  const mutation = useMutation({
    mutationFn: () =>
      changeStage(opportunity.id, {
        stage,
        // 阶段历史备注；重开时它就是必填的"重开原因"
        note: note.trim() === "" ? null : note.trim(),
        // 目标是 lost 时必填；其余阶段**显式传 null**（后端对非空值返回 400）
        lost_reason_id: stage === "lost" ? Number(lostReasonId) : null,
        // 输单说明写进机会本身，与上面的 note 是两个字段
        lost_note: stage === "lost" && lostNote.trim() !== "" ? lostNote.trim() : null,
      }),
    onMutate: () => setError(null),
    onSuccess: async () => {
      // 键与列表页一致：["board"] 让看板列头合计刷新，["opportunities"] 让列表页同步
      await queryClient.invalidateQueries({ queryKey: ["board"] });
      await queryClient.invalidateQueries({ queryKey: ["opportunities"] });
      onClose();
    },
    onError: (err) => setError(errorMessage(err, "阶段变更失败，请重试")),
  });

  const blocked =
    stage === "" ||
    // 后端对同阶段 400；选项里已经排除，这里只是最后一道保险
    stage === opportunity.stage ||
    (stage === "lost" && lostReasonId === "") ||
    (reopening && note.trim() === "");

  return (
    <Modal title="推进阶段" onClose={onClose}>
      <div className="space-y-3">
        <p className="break-words text-sm text-slate-600">
          {opportunity.name} · 当前：{STAGE_LABELS[opportunity.stage] ?? opportunity.stage}
        </p>

        <label className="block">
          <span className="mb-1 block text-sm font-medium text-slate-700">目标阶段</span>
          <select
            value={stage}
            onChange={(e) => setStage(e.target.value)}
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          >
            <option value="">
              {reopening ? "请选择要重开到的阶段" : "请选择目标阶段"}
            </option>
            {availableStages.map((value) => (
              <option key={value} value={value}>
                {STAGE_LABELS[value] ?? value}
              </option>
            ))}
          </select>
        </label>

        {reopening ? (
          // 后端对 sales 调重开返回 403。提前说清楚，比让销售填完原因再吃一个 403 好。
          <p className="text-xs text-slate-500">
            重开已赢单/已输单的机会需要主管或管理员权限，且必须填写重开原因。
          </p>
        ) : null}

        {stage === "lost" ? (
          <>
            <label className="block">
              <span className="mb-1 block text-sm font-medium text-slate-700">输单原因 *</span>
              <select
                value={lostReasonId}
                onChange={(e) => setLostReasonId(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
              >
                <option value="">{reasonsQuery.isLoading ? "加载中…" : "请选择"}</option>
                {(reasonsQuery.data ?? []).map((reason) => (
                  <option key={reason.id} value={String(reason.id)}>
                    {reason.name}
                  </option>
                ))}
              </select>
              {reasonsQuery.isError ? (
                // 字典拉不到就选不了原因，也就提交不了：必须把失败摆出来，不能让
                // 用户对着一个只有「请选择」的下拉反复点
                <span className="mt-1 block text-xs text-red-600">输单原因加载失败</span>
              ) : null}
            </label>
            <TextArea
              label="输单说明（可选，记入机会）"
              rows={2}
              value={lostNote}
              onChange={(e) => setLostNote(e.target.value)}
            />
          </>
        ) : null}

        {reopening ? (
          <TextArea
            label="重开原因 *（记入阶段历史）"
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        ) : (
          <Field
            label="备注（可选，记入阶段历史）"
            maxLength={200}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        )}

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
            disabled={blocked || mutation.isPending}
            onClick={() => mutation.mutate()}
            className="rounded-md bg-brand-500 px-3 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-60"
          >
            {mutation.isPending ? "提交中…" : "确认变更"}
          </button>
        </div>
      </div>
    </Modal>
  );
}