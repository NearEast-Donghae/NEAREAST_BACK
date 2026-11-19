from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class LoginDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: SecretStr = Field(min_length=8, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalizeEmail(cls, value: str) -> str:
        return value.strip().casefold() if isinstance(value, str) else value


class SignupDto(LoginDto):
    nickname: str = Field(min_length=1, max_length=40)

    @field_validator("nickname")
    @classmethod
    def normalizeNickname(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Nickname must not be empty")
        return value.strip()


class UserDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    nickname: str
    createdAt: datetime


class AuthDto(BaseModel):
    user: UserDto
    csrfToken: str


class ProfileUpdateDto(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    nickname: str = Field(min_length=2, max_length=12)


class PasswordUpdateDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currentPassword: SecretStr = Field(min_length=8, max_length=128)
    newPassword: SecretStr = Field(min_length=8, max_length=128)
