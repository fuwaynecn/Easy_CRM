"""公海池规则的唯一读取点。

默认值是 spec §3.11 的初始值；数据库没有行时返回默认值，GET 不写库。PATCH 才
upsert id=1。这样“读规则”是纯函数式、无副作用，测试和首页都不会因为一次 GET
改变数据库状态。
"""

from dataclasses import asdict, dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import InvalidValueError
from app.modules.pools.models import PoolRule
from app.modules.pools.schemas import PoolRuleRead, PoolRulesUpdate

DEFAULT_INACTIVE_DAYS = 30
DEFAULT_MAX_OWNED = 50
DEFAULT_AUTO_RECYCLE_ENABLED = True
DEFAULT_PROTECT_WITH_OPEN_DEAL = True
DEFAULT_PROTECT_WITH_PENDING_FOLLOWUP = True
DEFAULT_RECYCLE_NOTICE_DAYS = 3

# P3 的常量名保留：已有首页测试与文档引用它们；值必须与默认值一致。
INACTIVE_DAYS = DEFAULT_INACTIVE_DAYS
RECYCLE_NOTICE_DAYS = DEFAULT_RECYCLE_NOTICE_DAYS
NOTICE_THRESHOLD_DAYS = INACTIVE_DAYS - RECYCLE_NOTICE_DAYS


@dataclass(frozen=True)
class PoolRules:
    inactive_days: int = DEFAULT_INACTIVE_DAYS
    max_owned: int = DEFAULT_MAX_OWNED
    auto_recycle_enabled: bool = DEFAULT_AUTO_RECYCLE_ENABLED
    protect_with_open_deal: bool = DEFAULT_PROTECT_WITH_OPEN_DEAL
    protect_with_pending_followup: bool = DEFAULT_PROTECT_WITH_PENDING_FOLLOWUP
    recycle_notice_days: int = DEFAULT_RECYCLE_NOTICE_DAYS

    @property
    def notice_threshold_days(self) -> int:
        """首页预警阈值：距上次跟进达到这个天数就提示“即将回收”。"""
        return self.inactive_days - self.recycle_notice_days


def _from_row(row: PoolRule) -> PoolRules:
    return PoolRules(
        inactive_days=row.inactive_days,
        max_owned=row.max_owned,
        auto_recycle_enabled=row.auto_recycle_enabled,
        protect_with_open_deal=row.protect_with_open_deal,
        protect_with_pending_followup=row.protect_with_pending_followup,
        recycle_notice_days=row.recycle_notice_days,
    )


def load_pool_rules(db: Session) -> PoolRules:
    row = db.get(PoolRule, 1)
    return _from_row(row) if row is not None else PoolRules()


def _merge_rules(current: PoolRules, changes: dict[str, object]) -> PoolRules:
    rules = PoolRules(**{**asdict(current), **changes})
    if rules.recycle_notice_days >= rules.inactive_days:
        raise InvalidValueError("预警天数必须小于无跟进天数")
    return rules


def _write_row(db: Session, rules: PoolRules) -> None:
    row = db.get(PoolRule, 1)
    if row is None:
        row = PoolRule(id=1)
        db.add(row)
    for field, value in asdict(rules).items():
        setattr(row, field, value)


def update_pool_rules(db: Session, payload: PoolRulesUpdate) -> PoolRules:
    changes = payload.model_dump(exclude_unset=True)
    # `int | None` 是“可省略”的类型表达；显式 null 不是合法值。放过去会在
    # 下面的 `>=` 比较里变成 TypeError -> 500。
    if any(value is None for value in changes.values()):
        raise InvalidValueError("规则字段不能为 null")
    rules = _merge_rules(load_pool_rules(db), changes)
    try:
        _write_row(db, rules)
        db.commit()
    except IntegrityError:
        # 两个并发 PATCH 都观察到空表时，后提交者的 INSERT 会撞主键/单行 CHECK。
        # 先到的事务已经建好行：回滚后按库里的当前值重新合并，只重试一次。
        # 不重试第二次，也不吞其它异常——真的还有冲突就该暴露出来。
        db.rollback()
        rules = _merge_rules(load_pool_rules(db), changes)
        _write_row(db, rules)
        db.commit()
    return rules


def as_read(rules: PoolRules) -> PoolRuleRead:
    return PoolRuleRead(**asdict(rules))
