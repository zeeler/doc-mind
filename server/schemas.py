"""Pydantic 请求体模型 — 统一校验所有 API 输入。"""

from pydantic import BaseModel, Field, field_validator
from typing import Literal


# ======== chat ========

class ChatAskRequest(BaseModel):
    conversation_id: str
    question: str

    @field_validator("question")
    @classmethod
    def question_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("问题不能为空")
        return v.strip()


# ======== conversations ========

class CreateConversationRequest(BaseModel):
    title: str = ""


class UpdateConversationRequest(BaseModel):
    title: str


class BatchDeleteConvsRequest(BaseModel):
    ids: list[str]

    @field_validator("ids")
    @classmethod
    def ids_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("ids 不能为空")
        return v


# ======== config ========

class UpdateConfigRequest(BaseModel):
    """接受任意配置键值对。key=配置名, value=配置值（字符串）。"""

    model_config = {"extra": "allow"}


# ======== documents ========

class ImportUrlRequest(BaseModel):
    url: str
    folder_path: str = ""

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("URL 不能为空")
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL 必须以 http:// 或 https:// 开头")
        return v

    @field_validator("folder_path")
    @classmethod
    def sanitize_folder_path(cls, v: str) -> str:
        v = (v or "").strip()
        if ".." in v or v.startswith("/"):
            raise ValueError("folder_path 包含非法字符")
        return v


class BatchOperationRequest(BaseModel):
    ids: list[str]
    action: Literal["delete", "retry", "tag", "untag", "categorize"]
    params: dict = {}

    @field_validator("ids")
    @classmethod
    def ids_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("ids 不能为空")
        return v


# ======== memories ========

class RememberRequest(BaseModel):
    conversation_id: str
    message_id: str
    note: str = ""
    scope: str = "global"


class ObserveRequest(BaseModel):
    conversation_id: str


class ConsolidateRequest(BaseModel):
    dry_run: bool = False


class ExportMemoriesRequest(BaseModel):
    scope: str | None = None


# ======== tags ========

class CreateTagRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def name_valid(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("标签名不能为空")
        if len(v) > 100:
            raise ValueError("标签名不能超过100个字符")
        return v
