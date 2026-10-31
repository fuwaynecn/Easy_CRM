"""回收与首页共用的豁免条件。

两处必须用同一份 EXISTS，否则“首页提示即将回收”和“实际回收”会在边界上打架。
"""

from sqlalchemy import select

from app.modules.customers.models import Customer
from app.modules.followups.models import FollowUp
from app.modules.opportunities.models import OPEN_STAGES, Opportunity


def open_deal_exists():
    return (
        select(Opportunity.id)
        .where(
            Opportunity.customer_id == Customer.id,
            Opportunity.deleted_at.is_(None),
            Opportunity.stage.in_(OPEN_STAGES),
        )
        .exists()
    )


def pending_followup_exists():
    return (
        select(FollowUp.id)
        .where(
            FollowUp.customer_id == Customer.id,
            FollowUp.deleted_at.is_(None),
            FollowUp.status == "pending",
        )
        .exists()
    )
