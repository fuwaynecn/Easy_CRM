import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { errorMessage } from "../api/client";
import { releaseCustomer } from "../api/pools";
import { Modal } from "./Modal";
import { TextArea } from "./Field";

interface Props {
  customerId: number;
  customerName: string;
  onClose: () => void;
  onDone: () => void;
}

/** 释放回公海（spec §3.13）：原因必填 1–200 字，后端 422/400 都会挡住空原因。 */
export function ReleaseCustomerModal({
  customerId,
  customerName,
  onClose,
  onDone,
}: Props) {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const release = useMutation({
    mutationFn: () => releaseCustomer(customerId, { reason: reason.trim() }),
    onMutate: () => setError(null),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["customer", customerId] }),
        queryClient.invalidateQueries({ queryKey: ["customers"] }),
        queryClient.invalidateQueries({ queryKey: ["pool"] }),
        queryClient.invalidateQueries({ queryKey: ["home"] }),
        queryClient.invalidateQueries({ queryKey: ["timeline", customerId] }),
      ]);
      onDone();
    },
    onError: (err) => setError(errorMessage(err, "释放失败")),
  });

  const canSubmit = reason.trim().length > 0 && !release.isPending;

  return (
    <Modal title="释放回公海" onClose={onClose}>
      <div className="space-y-3">
        <p className="break-words text-sm text-slate-600">
          将「{customerName}」释放回公海后，负责人与部门会清空；未关闭的机会仍保留原负责人。
        </p>

        <TextArea
          label="释放原因 *"
          rows={3}
          maxLength={200}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />

        {error ? (
          <p className="break-words rounded-md bg-red-50 p-2 text-sm text-red-600">{error}</p>
        ) : null}

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
            disabled={!canSubmit}
            onClick={() => release.mutate()}
            className="rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
          >
            {release.isPending ? "释放中…" : "确认释放"}
          </button>
        </div>
      </div>
    </Modal>
  );
}