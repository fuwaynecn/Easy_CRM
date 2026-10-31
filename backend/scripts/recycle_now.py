"""本地验收：立即跑一次回收，不等 02:00。

用法：cd backend && python -m scripts.recycle_now
"""

from app.db.session import SessionLocal
from app.modules.pools.service import recycle_customers


def main() -> None:
    with SessionLocal() as db:
        count = recycle_customers(db)
    print(f"本次回收 {count} 个客户")


if __name__ == "__main__":
    main()
