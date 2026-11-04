from pydantic import BaseModel, Field


class ApprovalReviewStepIn(BaseModel):
    key: str | None = Field(default=None, max_length=32)
    name: str = Field(min_length=1, max_length=16)
    roleCode: str | None = Field(default=None, max_length=64)


class ApprovalFlowUpdateIn(BaseModel):
    reviews: list[ApprovalReviewStepIn] = Field(min_length=1, max_length=12)
