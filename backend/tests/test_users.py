import pytest

from app.core.errors import (
    ConflictError,
    ForbiddenError,
    LockedError,
    NotFoundError,
    UnauthorizedError,
)
from app.core.security import hash_password, verify_password
from app.modules.departments.service import create_department
from app.modules.users.service import (
    ROLE_SALES,
    authenticate,
    create_user,
    get_user_by_username,
    reset_password,
    set_user_status,
)


def test_password_hash_roundtrip():
    hashed = hash_password("Passw0rd!")
    assert hashed != "Passw0rd!"
    assert verify_password("Passw0rd!", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_create_user_stores_hash_not_plaintext(db):
    dept = create_department(db, "华东销售部")
    user = create_user(
        db,
        username="zhangsan",
        password="Passw0rd!",
        display_name="张三",
        role=ROLE_SALES,
        department_id=dept.id,
    )
    assert user.id is not None
    assert user.password_hash != "Passw0rd!"
    assert verify_password("Passw0rd!", user.password_hash)


def test_username_must_be_unique(db):
    dept = create_department(db, "华东销售部")
    create_user(
        db,
        username="zhangsan",
        password="Passw0rd!",
        display_name="张三",
        role=ROLE_SALES,
        department_id=dept.id,
    )
    with pytest.raises(ConflictError):
        create_user(
            db,
            username="zhangsan",
            password="Passw0rd!",
            display_name="张三二号",
            role=ROLE_SALES,
            department_id=dept.id,
        )


def test_get_user_by_username_returns_none_when_absent(db):
    assert get_user_by_username(db, "nobody") is None


def test_authenticate_success_updates_last_login(db):
    dept = create_department(db, "华东销售部")
    create_user(
        db,
        username="zhangsan",
        password="Passw0rd!",
        display_name="张三",
        role=ROLE_SALES,
        department_id=dept.id,
    )
    user = authenticate(db, "zhangsan", "Passw0rd!")
    assert user.last_login_at is not None
    assert user.failed_login_count == 0


def test_authenticate_wrong_password_counts_failure(db):
    dept = create_department(db, "华东销售部")
    create_user(
        db,
        username="zhangsan",
        password="Passw0rd!",
        display_name="张三",
        role=ROLE_SALES,
        department_id=dept.id,
    )
    with pytest.raises(UnauthorizedError):
        authenticate(db, "zhangsan", "bad")
    user = get_user_by_username(db, "zhangsan")
    assert user.failed_login_count == 1


def test_authenticate_locks_after_five_failures(db):
    dept = create_department(db, "华东销售部")
    create_user(
        db,
        username="zhangsan",
        password="Passw0rd!",
        display_name="张三",
        role=ROLE_SALES,
        department_id=dept.id,
    )
    for _ in range(5):
        with pytest.raises(UnauthorizedError):
            authenticate(db, "zhangsan", "bad")
    user = get_user_by_username(db, "zhangsan")
    assert user.locked_until is not None
    with pytest.raises(LockedError) as excinfo:
        authenticate(db, "zhangsan", "Passw0rd!")
    assert excinfo.value.status_code == 423
    assert "锁定" in excinfo.value.message


def test_authenticate_unknown_user_and_wrong_password_share_message(db):
    dept = create_department(db, "华东销售部")
    create_user(
        db,
        username="zhangsan",
        password="Passw0rd!",
        display_name="张三",
        role=ROLE_SALES,
        department_id=dept.id,
    )
    with pytest.raises(UnauthorizedError) as missing:
        authenticate(db, "nobody", "Passw0rd!")
    with pytest.raises(UnauthorizedError) as wrong:
        authenticate(db, "zhangsan", "bad")
    assert missing.value.message == wrong.value.message
    assert missing.value.status_code == wrong.value.status_code == 401


def test_disabled_user_cannot_authenticate(db):
    dept = create_department(db, "华东销售部")
    user = create_user(
        db,
        username="zhangsan",
        password="Passw0rd!",
        display_name="张三",
        role=ROLE_SALES,
        department_id=dept.id,
    )
    set_user_status(db, user.id, "disabled")
    with pytest.raises(ForbiddenError) as excinfo:
        authenticate(db, "zhangsan", "Passw0rd!")
    assert excinfo.value.status_code == 403
    assert "停用" in excinfo.value.message


def test_reset_password_changes_hash(db):
    dept = create_department(db, "华东销售部")
    user = create_user(
        db,
        username="zhangsan",
        password="Passw0rd!",
        display_name="张三",
        role=ROLE_SALES,
        department_id=dept.id,
    )
    updated = reset_password(db, user.id, "NewPass!23")
    assert verify_password("NewPass!23", updated.password_hash)


def test_reset_password_missing_user_raises(db):
    with pytest.raises(NotFoundError):
        reset_password(db, 9999, "NewPass!23")
