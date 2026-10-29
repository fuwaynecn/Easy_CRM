from pydantic import BaseModel, ConfigDict, Field


class LostReasonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    sort_order: int = 0
    active: bool = True


class LostReasonRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    sort_order: int
    active: bool
