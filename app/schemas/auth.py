from typing import Optional, List
from pydantic import BaseModel, Field, field_validator
from datetime import datetime

class UserBase(BaseModel):
    username: str = Field(min_length=3, max_length=128)
    team_code: str = Field(min_length=3, max_length=128)

    @field_validator("username", "team_code")
    @classmethod
    def strip_whitespace(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field cannot be empty.")
        return cleaned

class UserCreate(UserBase):
    # Basic length validation blocks extremely weak credentials at the API boundary.
    password: str = Field(min_length=8, max_length=256)

class UserResponse(UserBase):
    id: str
    tenant_id: str
    created_at: datetime

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str
    username: str
    team_code: str
    tenant_id: str

class TokenData(BaseModel):
    username: Optional[str] = None
