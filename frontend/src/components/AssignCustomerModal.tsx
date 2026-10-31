import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { errorMessage } from "../api/client";
import { assignCustomer, listAssignableUsers } from "../api/pools";
import { Modal } from "./Modal";
import { TextArea } from "./Field";

interface Props {
  customerId: number;
  currentOwnerId: number | null;
  onClose: () => void;
  onDone: () => void;
}

/** 分配客户（spec §3.12）：主管只能选本部门，admin 可选全公司。 */
export function AssignCustomerModal({
  customerId,
  currentOwnerId,
  onClose,
  onDone,
}: Props) {
  const queryClient = useQueryClient();
  const users = useQuery({
    queryKey: ["assignable-users"],
    queryFn: listAssignableUsers,
  });
  const [ownerId, setOwnerId] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const selected = users.data?.find((user) => String(user.id) === ownerId) ?? null;
  const atLimit = selected !== null && selected.owned_count >= selected.max_owned;

  const assign = useMutation({
    mutationFn: () =>
      assignCustomer(customerId, {
        owner_id: Number(ownerId),
        ...(reason.trim() ? { reason: reason.trim() } : {}),
      }),
    onMutate: () => setError(null),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["customer", customerId] }),
        queryClient.invalidateQueries({ queryKey: ["customers"] }),
        queryClient.invalidateQueries({ queryKey: ["pool"] }),
        queryClient.invalidateQueries({ queryKey: ["home"] }),
        queryClient.invalidateQueries({ queryKey: ["timeline", customerId] }),
        queryClient.invalidateQueries({ queryKey: ["assignable-users"] }),
      ]);
      onDone();
    },
    onError: (err) => setError(errorMessage(err, "分配失败")),
  });

  const canSubmit =
    selected !== null &&
    selected.id !== currentOwnerId &&
    !atLimit &&
    !assign.isPending;

  return (
    <Modal title="分配客户" onClose={onClose}>
      <div className="space-y-3">
        <label className="block">
          <span className="mb-1 block text-sm font-medium text-slate-700">新负责人 *</span>
          <select
            value={ownerId}
            onChange={(event) => setOwnerId(event.target.value)}
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
          >
            <option value="">请选择</option>
            {(users.data ?? []).map((user) => (
              <option key={user.id} value={user.id}>
                {user.display_name}（{user.owned_count}/{user.max_owned}）
              </option>
            ))}
          </select>
        </label>

        {users.isError ? (
          <p className="break-words text-sm text-red-600">
            {errorMessage(users.error, "可分配用户加载失败")}
          </p>
        ) : null}

        {selected && selected.id === currentOwnerId ? (
          <p className="text-sm text-amber-700">该客户已经属于此负责人。</p>
        ) : null}
        {atLimit ? (
          <p className="text-sm text-amber-700">
            该负责人名下客户已达上限（{selected?.max_owned}），请先释放或换人。
          </p>
        ) : null}

        <TextArea
          label="分配原因（选填）"
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
            onClick={() => assign.mutate()}
            className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
          >
            {assign.isPending ? "分配中…" : "确认分配"}
          </button>
        </div>
      </div>
    </Modal>
  );
}