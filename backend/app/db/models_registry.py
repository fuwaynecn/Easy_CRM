"""集中导入全部 ORM 模型，确保 Base.metadata 完整。

新增模型时，必须在这里追加一行导入，否则测试夹具建表会缺表。
"""

from app.modules.activities.models import Activity  # noqa: F401
from app.modules.audit.models import AuditLog  # noqa: F401
from app.modules.contacts.models import Contact  # noqa: F401
from app.modules.customers.models import Customer  # noqa: F401
from app.modules.departments.models import Department  # noqa: F401
from app.modules.followups.models import FollowUp  # noqa: F401
from app.modules.imports.models import ImportJob  # noqa: F401
from app.modules.lost_reasons.models import LostReason  # noqa: F401
from app.modules.opportunities.models import Opportunity, StageHistory  # noqa: F401
from app.modules.pools.models import OwnerChangeLog, PoolRule  # noqa: F401
from app.modules.users.models import User  # noqa: F401
