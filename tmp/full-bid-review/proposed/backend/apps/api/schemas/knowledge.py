from pydantic import BaseModel, Field
from typing import Literal


# 对话 / 检索命中块（snake_case，与 SSE refs、Qdrant 补全字段一致）
# 视频字段约定见 docs/kb-video-contract.md
class KnowledgeRefChunk(BaseModel):
    content: str = ""
    score: float = 0.0
    doc_id: str | None = None
    kb_id: str | None = None
    name: str | None = None
    chunk_index: int | None = None
    file_type: str | None = None
    has_file: bool | None = None
    preview_kind: Literal["pdf", "image", "file", "video", ""] | str | None = None
    kind: str | None = None
    tags: list[str] | None = None
    startMs: int | None = None
    endMs: int | None = None


class CreateBaseRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)


class UpdateBaseRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)


class AclGrantIn(BaseModel):
    subjectType: Literal["user", "role"]
    subjectId: int = Field(ge=1)
    canView: bool = False
    canUse: bool = False
    canManage: bool = False


class ReplaceAclRequest(BaseModel):
    grants: list[AclGrantIn] = Field(default_factory=list, max_length=500)


class TextDocumentRequest(BaseModel):
    baseId: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=4)
    uploader: str | None = None
    tags: list[str] | None = None
    force: bool = False


class UrlDocumentRequest(BaseModel):
    baseId: str = Field(min_length=1, max_length=32)
    url: str = Field(min_length=4, max_length=1024)
    title: str | None = None
    uploader: str | None = None
    tags: list[str] | None = None
    force: bool = False


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    baseId: str | None = Field(default=None, max_length=32)
    topK: int = Field(default=5, ge=1, le=50)
    minScore: float = Field(default=0.0, ge=0.0, le=1.0)


class AttachDocumentRequest(BaseModel):
    baseId: str = Field(min_length=1, max_length=32)
    parentId: str | None = Field(default=None, max_length=32)
    asAttachment: bool | None = True


class ReviewDocumentRequest(BaseModel):
    baseId: str = Field(min_length=1, max_length=32)
    action: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=2000)


class QdrantApplyRequest(BaseModel):
    confirm: bool = False
