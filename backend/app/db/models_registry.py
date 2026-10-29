"""集中导入全部 ORM 模型，确保 Base.metadata 完整。

新增模型时，必须在这里追加一行导入，否则测试夹具建表会缺表。
"""

from app.modules.customers.models import Customer  # noqa: F401
from app.modules.departments.models import Department  # noqa: F401
from app.modules.users.models import User  # noqa: F401
