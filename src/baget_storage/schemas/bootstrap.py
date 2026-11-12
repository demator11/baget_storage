from pydantic import BaseModel, EmailStr, Field


class ResetPassword(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128, repr=False)
