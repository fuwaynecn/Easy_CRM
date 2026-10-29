from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=64)
    role: str
    department_id: int | None = None


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role: str
    department_id: int | None
    status: str
    created_at: datetime


class UserStatusUpdate(BaseModel):
    status: str


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)
