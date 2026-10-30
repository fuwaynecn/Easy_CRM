"""机会阶段变更：唯一入口，同事务写 stage_histories。

规则全部来自 design §3.6（终态互转、重开权限与原因、输单原因、won_at、手工赢单率、
不碰 customers.last_activity_at），逐条落在 change_stage 里。
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, InvalidValueError
from app.modules.lost_reasons.service import get_lost_reason
from app.modules.opportunities.models import (
    OPPORTUNITY_STAGES,
    TERMINAL_STAGES,
    Opportunity,
    StageHistory,
)
from app.modules.opportunities.schemas import StageChange
from app.modules.opportunities.service.reads import _load_full, get_opportunity
from app.modules.opportunities.service.writes import _clean_optional, _writable_customer
from app.modules.users.models import ROLE_ADMIN, ROLE_MANAGER, User

# 重开已关闭的机会限这两个角色（design §3.6 + P2 裁定第 10 条）。
# 销售不能自己把输掉的单改回在谈：那等于让漏斗里的"输单数"由参单的人自己决定。
_REOPEN_ROLES = (ROLE_ADMIN, ROLE_MANAGER)


def _validate_target_stage(stage: str) -> None:
    """目标阶段必须是六个阶段之一。

    先于权限检查，是因为"未知阶段"是参数错误（400），与"你有没有权限改这单"无关；
    放在后面会让拼错阶段的销售拿到 403，前端会去提示"无权操作"。
    """
    if stage not in OPPORTUNITY_STAGES:
        raise InvalidValueError(f"未知阶段：{stage}")


def change_stage(
    db: Session, actor: User, opportunity_id: int, payload: StageChange
) -> Opportunity:
    """阶段变更：允许前进、后退、跨阶段跳转，每次都写一条 history。

    规则全部来自 design §3.6，逐条落在这里（路由层无法绕过：`PATCH` 不接受 stage，
    这是唯一入口）：

    - `won` / `lost` 是终态；**终态之间不能互转**——赢单后想改成输单，必须先重开到
      推进阶段，否则一条 history 会同时表达"赢过"和"输掉"，报表口径就乱了；
    - 重开终态限 `manager` / `admin`，且必须填原因（写进 history.note）；
    - 进 `lost` 必须选 `lost_reason_id`（不存在是 404，不是外键 500；已停用是 400，
      否则 `lost_reasons.active` 在 P2 没有任何执行点，停用等于没做）；
    - 进 `won` 记 `won_at`；离开终态时把三个终态字段清干净，否则报表仍按旧
      `won_at` 把这单算成赢单；
    - 手工赢单率（`win_rate` 非空）**不覆盖**，自动值（NULL）继续跟随新阶段；
    - 不碰 `customers.last_activity_at`（§3.3 的三条同步点之外一律不动）。

    机会更新与 history 在**同一个事务**里提交（一次 commit）：只落一半会让"当前阶段"
    与"阶段历史"互相矛盾，而 history 是漏斗报表的唯一数据来源。
    """
    _validate_target_stage(payload.stage)
    # get_opportunity 已做作用域校验（403/404 分开）；但"看得见"不等于"能改"，
    # 公海客户对本角色只读，所以写路径还要过 assert_customer_writable。
    opportunity = get_opportunity(db, actor, opportunity_id)
    _writable_customer(db, actor, opportunity.customer_id)

    if opportunity.stage == payload.stage:
        raise InvalidValueError("机会已处于该阶段")
    note = (payload.note or "").strip() or None

    was_closed = opportunity.stage in TERMINAL_STAGES
    if was_closed:
        # 终态→终态先判：它压根不是"重开"，用重开权限去解释它只会给出误导的 403。
        if payload.stage in TERMINAL_STAGES:
            raise InvalidValueError(
                "已关闭的机会不能直接改为另一个终态，请先重开到推进阶段"
            )
        if actor.role not in _REOPEN_ROLES:
            raise ForbiddenError("仅主管及以上可重开已关闭的机会")
        if note is None:
            raise InvalidValueError("重开机会必须填写重开原因")

    if payload.stage == "lost":
        if payload.lost_reason_id is None:
            raise InvalidValueError("输单必须选择输单原因")
        # 不存在的原因要给 404，而不是拖到 commit 时变成外键 500
        reason = get_lost_reason(db, payload.lost_reason_id)
        if not reason.active:
            raise InvalidValueError("该输单原因已停用，请选择其他原因")
    elif payload.lost_reason_id is not None or payload.lost_note is not None:
        # 输单备注静默丢弃比报错更糟：调用方以为记下了，报表里却永远查不到。
        raise InvalidValueError("只有输单阶段可以填写输单原因与输单备注")

    if payload.stage == "won":
        opportunity.won_at = datetime.now(UTC)
        opportunity.lost_reason_id = None
        opportunity.lost_note = None
    elif payload.stage == "lost":
        opportunity.won_at = None
        opportunity.lost_reason_id = payload.lost_reason_id
        opportunity.lost_note = _clean_optional(payload.lost_note)
    else:
        opportunity.won_at = None
        opportunity.lost_reason_id = None
        opportunity.lost_note = None

    history = StageHistory(
        opportunity_id=opportunity.id,
        from_stage=opportunity.stage,   # 必须在改 stage 之前取
        to_stage=payload.stage,
        amount_snapshot=opportunity.amount,
        note=note,
        operator_id=actor.id,
    )
    opportunity.stage = payload.stage
    db.add(history)
    db.commit()
    # 必须经 _load_full 回读：三个关系是 lazy="raise"，不重新预加载就无法序列化成
    # OpportunityRead（会直接抛 InvalidRequestError）。
    return _load_full(db, opportunity.id)
