"""输单原因字典（design §3.10）。

字典表而不是自由文本：输单原因要作为报表维度（§7 按 `lost_reason_id` 分组），
自由文本在报表里会变成"价格高 / 价格太高 / 太贵"三行。

停用（`active=false`）与删除是两件事：历史机会还指着某条原因，硬删会让报表的
输单原因分组整块消失，所以 P2 只提供"停用"，不提供删除。
"""

from sqlalchemy import BigInteger, Boolean, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class LostReason(Base, TimestampMixin):
    __tablename__ = "lost_reasons"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # unique 走命名约定，约束名是 uq_lost_reasons_name（迁移里显式写出同名约束）
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # server_default 是给 ORM 之外的写入兜底（导入脚本、运维手插）：
    # 只有 Python 侧 default 时，裸 INSERT 会直接撞 NOT NULL。
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
