"""子记录归属校验：联系人 / 机会是否属于给定客户。

跟进与待办都带可空的 `contact_id` / `opportunity_id`。只校验"记录存在"不够：
把 A 客户的联系人挂到 B 客户的跟进上，时间线就会在 A 客户页面显示 B 客户联系人
的名字（可见性没破，但信息串了）。这类错误必须在写入时 400 掉。

放 customers 模块而不是任一业务模块：两条写入路径都要用，谁 import 谁都会造成
activities ↔ followups 的循环依赖；这里只碰模型，不碰 service。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import InvalidValueError, NotFoundError
from app.modules.contacts.models import Contact
from app.modules.opportunities.models import Opportunity


def validate_child_links(
    db: Session,
    *,
    customer_id: int,
    contact_id: int | None,
    opportunity_id: int | None,
) -> None:
    """校验两个可空外键：不存在 404，存在但不属于该客户 400。"""
    if contact_id is not None:
        contact = db.execute(
            select(Contact).where(
                Contact.id == contact_id, Contact.deleted_at.is_(None)
            )
        ).scalar_one_or_none()
        if contact is None:
            raise NotFoundError("联系人不存在")
        if contact.customer_id != customer_id:
            raise InvalidValueError("联系人不属于该客户")

    if opportunity_id is not None:
        opportunity = db.execute(
            select(Opportunity).where(
                Opportunity.id == opportunity_id, Opportunity.deleted_at.is_(None)
            )
        ).scalar_one_or_none()
        if opportunity is None:
            raise NotFoundError("机会不存在")
        if opportunity.customer_id != customer_id:
            raise InvalidValueError("机会不属于该客户")
