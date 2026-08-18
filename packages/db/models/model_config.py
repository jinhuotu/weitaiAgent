"""OpenAI 兼容模型配置（LLM / Embedding）。

运行时对话只读本表，不再回落到 .env 的 LLM_*。
Embedding 配置本轮一并建表，供后续知识库复用。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class ModelConfig(Base):
    """模型配置。``kind`` 区分对话/向量；``scope_*`` 绑定默认用途（互斥）。"""

    __tablename__ = "model_configs"
    __table_args__ = {"comment": "模型配置（对话/Embedding）"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    public_id: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, nullable=False, comment="对外ID"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="配置名称")
    # llm | embedding
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, index=True, comment="接口类别：llm/embedding"
    )
    # text_chat | multimodal_vision | multimodal_audio | text_embedding
    model_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="text_chat",
        index=True,
        comment="模型类型：文本对话/多模态视觉/多模态音频/文本向量",
    )
    api_base: Mapped[str] = mapped_column(String(512), nullable=False, comment="API Base URL")
    api_key: Mapped[str] = mapped_column(String(512), nullable=False, comment="API Key")
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="模型名称")
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True, comment="温度")
    timeout_seconds: Mapped[float] = mapped_column(
        Float, nullable=False, default=120.0, comment="超时秒数"
    )
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="向量维度")
    remark: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="备注")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否启用")
    # 同一用途建议仅一条为 True（创建/更新时会清掉其它行的同 scope）
    scope_fast: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="绑定快速对话"
    )
    scope_deep: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="绑定深度对话"
    )
    scope_embedding: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="绑定Embedding"
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="创建人用户ID")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="更新时间",
    )
