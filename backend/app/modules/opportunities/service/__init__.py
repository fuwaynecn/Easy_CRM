"""机会服务的公共入口。

P2 时期这里是单文件 515 行；P3 的时间线要读 `stage_histories`，再往里堆会难以导航，
因此按职责拆成三个模块，**只搬代码、不改语义**：

- ``reads.py``：可见性、列表、看板、单条读取
- ``writes.py``：创建、编辑、软删、负责人同步 seam
- ``stage.py``：阶段变更

重导出的公共名字列在 ``__all__`` 里（显式声明"这些都是对外契约"），调用方
（router / seed / tests）无需改 import。私有 helper 留在各自子模块，不在这里重导出。
"""

from app.modules.opportunities.service.reads import (
    BOARD_COLUMN_LIMIT,
    BoardColumn,
    board,
    get_opportunity,
    list_opportunities,
)
from app.modules.opportunities.service.stage import change_stage
from app.modules.opportunities.service.writes import (
    UPDATABLE_FIELDS,
    create_opportunity,
    delete_opportunity,
    sync_opportunity_owner,
    update_opportunity,
)

__all__ = [
    "BOARD_COLUMN_LIMIT",
    "UPDATABLE_FIELDS",
    "BoardColumn",
    "board",
    "change_stage",
    "create_opportunity",
    "delete_opportunity",
    "get_opportunity",
    "list_opportunities",
    "sync_opportunity_owner",
    "update_opportunity",
]
