"""knowledge / prompts / mcp / scenario_agents

Revision ID: 0002_kb_prompts_mcp_agents
Revises: 0001_init_platform
Create Date: 2026-08-18

version_num 默认 VARCHAR(32)。旧 ID 0002_knowledge_prompts_mcp_agents 为 33 字符会写不进去。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0002_kb_prompts_mcp_agents"
down_revision: Union[str, None] = "0001_init_platform"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
        comment="创建时间",
    )


def _ts_updated() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
        comment="更新时间",
    )


def upgrade() -> None:
    # 上次已建表、但写入 alembic_version 失败时，跳过 DDL，只更新版本号
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("knowledge_bases"):
        return

    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("public_id", sa.String(length=32), nullable=False, comment="对外ID"),
        sa.Column("name", sa.String(length=128), nullable=False, comment="知识库名称"),
        sa.Column("description", sa.String(length=512), nullable=True, comment="描述"),
        sa.Column(
            "purpose",
            sa.String(length=16),
            nullable=False,
            server_default="rag",
            comment="用途：rag=AI知识库",
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="active",
            comment="状态",
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人用户ID"),
        _ts(),
        _ts_updated(),
        sa.PrimaryKeyConstraint("id"),
        comment="知识库",
    )
    op.create_index("ix_knowledge_bases_public_id", "knowledge_bases", ["public_id"], unique=True)
    op.create_index("ix_knowledge_bases_purpose", "knowledge_bases", ["purpose"])

    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("public_id", sa.String(length=32), nullable=False, comment="对外ID"),
        sa.Column("base_id", sa.BigInteger(), nullable=False, comment="所属知识库ID"),
        sa.Column("name", sa.String(length=255), nullable=False, comment="资料名称"),
        sa.Column(
            "source",
            sa.String(length=16),
            nullable=False,
            server_default="text",
            comment="来源：file/text/url",
        ),
        sa.Column(
            "kind",
            sa.String(length=16),
            nullable=False,
            server_default="doc",
            comment="类型：doc/3d等",
        ),
        sa.Column("file_type", sa.String(length=32), nullable=True, comment="文件扩展名"),
        sa.Column("size", sa.BigInteger(), nullable=True, comment="文件大小(字节)"),
        sa.Column("url", sa.String(length=1024), nullable=True, comment="来源URL"),
        sa.Column("storage_path", sa.String(length=512), nullable=True, comment="存储相对路径"),
        sa.Column("file_key", sa.String(length=512), nullable=True, comment="文件键"),
        sa.Column("preview_url", sa.String(length=2048), nullable=True, comment="预览地址"),
        sa.Column("summary", sa.Text(), nullable=True, comment="摘要"),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0", comment="字符数"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0", comment="向量切块数"),
        sa.Column("tags", mysql.JSON(), nullable=True, comment="标签JSON"),
        sa.Column("uploader", sa.String(length=128), nullable=True, comment="上传人"),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="parsing",
            comment="状态：parsing/ready/failed",
        ),
        sa.Column("error_msg", sa.Text(), nullable=True, comment="失败原因"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人用户ID"),
        _ts(),
        _ts_updated(),
        sa.ForeignKeyConstraint(["base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="知识库文档元数据",
    )
    op.create_index(
        "ix_knowledge_documents_public_id", "knowledge_documents", ["public_id"], unique=True
    )
    op.create_index("ix_knowledge_documents_base_id", "knowledge_documents", ["base_id"])

    op.create_table(
        "prompts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("public_id", sa.String(length=32), nullable=False, comment="对外ID"),
        sa.Column("name", sa.String(length=128), nullable=False, comment="提示词名称"),
        sa.Column("content", sa.Text(), nullable=False, comment="提示词正文"),
        sa.Column("remark", sa.String(length=512), nullable=True, comment="备注"),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
            comment="是否启用",
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人用户ID"),
        _ts(),
        _ts_updated(),
        sa.PrimaryKeyConstraint("id"),
        comment="系统提示词",
    )
    op.create_index("ix_prompts_public_id", "prompts", ["public_id"], unique=True)

    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("public_id", sa.String(length=32), nullable=False, comment="对外ID"),
        sa.Column("name", sa.String(length=128), nullable=False, comment="服务名称"),
        sa.Column("transport", sa.String(length=32), nullable=False, comment="传输"),
        sa.Column("url", sa.String(length=1024), nullable=True, comment="服务URL"),
        sa.Column("command", sa.String(length=512), nullable=True, comment="stdio命令"),
        sa.Column("args", mysql.JSON(), nullable=True, comment="命令参数JSON"),
        sa.Column("env", mysql.JSON(), nullable=True, comment="环境变量JSON"),
        sa.Column("headers", mysql.JSON(), nullable=True, comment="请求头JSON"),
        sa.Column(
            "timeout_seconds",
            sa.Float(),
            nullable=False,
            server_default="60",
            comment="超时秒数",
        ),
        sa.Column("remark", sa.String(length=512), nullable=True, comment="备注"),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
            comment="是否启用",
        ),
        sa.Column("last_error", sa.Text(), nullable=True, comment="最近错误"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True, comment="最近探测时间"),
        sa.Column("tools_cached_at", sa.DateTime(timezone=True), nullable=True, comment="工具缓存时间"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人用户ID"),
        _ts(),
        _ts_updated(),
        sa.PrimaryKeyConstraint("id"),
        comment="MCP 服务配置",
    )
    op.create_index("ix_mcp_servers_public_id", "mcp_servers", ["public_id"], unique=True)
    op.create_index("ix_mcp_servers_transport", "mcp_servers", ["transport"])

    op.create_table(
        "mcp_tools",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("public_id", sa.String(length=32), nullable=False, comment="对外ID"),
        sa.Column("server_id", sa.BigInteger(), nullable=False, comment="所属MCP服务ID"),
        sa.Column("name", sa.String(length=128), nullable=False, comment="工具名"),
        sa.Column("description", sa.Text(), nullable=True, comment="工具描述"),
        sa.Column("input_schema", mysql.JSON(), nullable=True, comment="入参Schema JSON"),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
            comment="是否启用",
        ),
        _ts(),
        _ts_updated(),
        sa.ForeignKeyConstraint(["server_id"], ["mcp_servers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="MCP 工具缓存",
    )
    op.create_index("ix_mcp_tools_public_id", "mcp_tools", ["public_id"], unique=True)
    op.create_index("ix_mcp_tools_server_id", "mcp_tools", ["server_id"])
    op.create_index("ix_mcp_tools_name", "mcp_tools", ["name"])

    op.create_table(
        "scenario_agents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("public_id", sa.String(length=32), nullable=False, comment="对外ID"),
        sa.Column("name", sa.String(length=128), nullable=False, comment="智能体名称"),
        sa.Column("remark", sa.String(length=512), nullable=True, comment="备注"),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
            comment="是否启用",
        ),
        sa.Column("prompt_public_id", sa.String(length=32), nullable=True, comment="绑定提示词ID"),
        sa.Column("knowledge_base_ids", mysql.JSON(), nullable=True, comment="知识库ID列表"),
        sa.Column(
            "mode",
            sa.String(length=16),
            nullable=False,
            server_default="fast",
            comment="模式：fast/deep",
        ),
        sa.Column("mcp_tool_ids", mysql.JSON(), nullable=True, comment="MCP工具白名单"),
        sa.Column(
            "tools_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
            comment="是否启用工具调用",
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人用户ID"),
        _ts(),
        _ts_updated(),
        sa.PrimaryKeyConstraint("id"),
        comment="场景智能体配置",
    )
    op.create_index("ix_scenario_agents_public_id", "scenario_agents", ["public_id"], unique=True)
    op.create_index("ix_scenario_agents_prompt_public_id", "scenario_agents", ["prompt_public_id"])


def downgrade() -> None:
    op.drop_table("scenario_agents")
    op.drop_table("mcp_tools")
    op.drop_table("mcp_servers")
    op.drop_table("prompts")
    op.drop_table("knowledge_documents")
    op.drop_table("knowledge_bases")
