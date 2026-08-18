"""init platform tables: users / chat / models / workflows / audit

Revision ID: 0001_init_platform
Revises:
Create Date: 2026-08-18

说明：
  zhongjiAgent 用了 0001～0024 多步迁移。weitaiAgent 是空库初始化，
  把本轮需要的表合并成一次升级，避免把窑炉/知识库/MCP 表一并带过来。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0001_init_platform"
down_revision: Union[str, None] = None
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
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("username", sa.String(length=64), nullable=False, comment="登录用户名"),
        sa.Column("email", sa.String(length=128), nullable=True, comment="邮箱"),
        sa.Column("hashed_password", sa.String(length=255), nullable=False, comment="密码哈希"),
        sa.Column("display_name", sa.String(length=64), nullable=True, comment="显示名称"),
        sa.Column("department", sa.String(length=64), nullable=True, comment="部门"),
        sa.Column("phone", sa.String(length=32), nullable=True, comment="手机号"),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
            comment="是否启用",
        ),
        sa.Column(
            "is_superuser",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
            comment="是否超级管理员",
        ),
        sa.Column("remark", sa.Text(), nullable=True, comment="备注"),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True, comment="最近登录时间"),
        _ts(),
        _ts_updated(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        comment="系统用户",
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "roles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("code", sa.String(length=64), nullable=False, comment="角色编码"),
        sa.Column("name", sa.String(length=64), nullable=False, comment="角色名称"),
        sa.Column("description", sa.Text(), nullable=True, comment="角色说明"),
        _ts(),
        _ts_updated(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        comment="角色",
    )

    op.create_table(
        "user_roles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("user_id", sa.BigInteger(), nullable=False, comment="用户ID"),
        sa.Column("role_id", sa.BigInteger(), nullable=False, comment="角色ID"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "role_id", name="uq_user_role"),
        comment="用户-角色关联",
    )

    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("public_id", sa.String(length=32), nullable=False, comment="对外ID"),
        sa.Column("user_id", sa.BigInteger(), nullable=False, comment="用户ID"),
        sa.Column(
            "title",
            sa.String(length=128),
            nullable=False,
            server_default="新对话",
            comment="会话标题",
        ),
        sa.Column(
            "title_auto",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
            comment="是否自动生成标题",
        ),
        sa.Column(
            "mode",
            sa.String(length=16),
            nullable=False,
            server_default="fast",
            comment="模式：fast/deep",
        ),
        sa.Column("summary", sa.String(length=512), nullable=True, comment="会话摘要"),
        sa.Column("knowledge_base_ids", mysql.JSON(), nullable=True, comment="关联知识库ID列表（预留）"),
        sa.Column(
            "message_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="消息条数",
        ),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True, comment="最近消息时间"),
        _ts(),
        _ts_updated(),
        sa.PrimaryKeyConstraint("id"),
        comment="AI 问答会话",
    )
    op.create_index("ix_chat_sessions_public_id", "chat_sessions", ["public_id"], unique=True)
    op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("public_id", sa.String(length=32), nullable=False, comment="对外ID"),
        sa.Column("session_id", sa.BigInteger(), nullable=False, comment="会话ID"),
        sa.Column("role", sa.String(length=16), nullable=False, comment="角色：user/assistant/tool等"),
        sa.Column("content", sa.Text(), nullable=False, comment="消息正文"),
        sa.Column("mode", sa.String(length=16), nullable=True, comment="生成模式"),
        sa.Column("refs", mysql.JSON(), nullable=True, comment="引用片段JSON"),
        sa.Column("stream_msg_id", sa.String(length=64), nullable=True, comment="Redis Stream消息ID"),
        sa.Column("model_name", sa.String(length=128), nullable=True, comment="模型名"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True, comment="提示词token数"),
        sa.Column("completion_tokens", sa.Integer(), nullable=True, comment="补全token数"),
        sa.Column("total_tokens", sa.Integer(), nullable=True, comment="总token数"),
        sa.Column("tool_name", sa.String(length=128), nullable=True, comment="工具名"),
        sa.Column("tool_input", mysql.JSON(), nullable=True, comment="工具入参JSON"),
        sa.Column("tool_output", mysql.JSON(), nullable=True, comment="工具出参JSON"),
        sa.Column("tool_error", sa.Text(), nullable=True, comment="工具错误信息"),
        sa.Column("tool_duration_ms", sa.Integer(), nullable=True, comment="工具耗时毫秒"),
        _ts(),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stream_msg_id"),
        comment="AI 问答消息",
    )
    op.create_index("ix_chat_messages_public_id", "chat_messages", ["public_id"], unique=True)
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])

    op.create_table(
        "model_configs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("public_id", sa.String(length=32), nullable=False, comment="对外ID"),
        sa.Column("name", sa.String(length=128), nullable=False, comment="配置名称"),
        sa.Column("kind", sa.String(length=16), nullable=False, comment="接口类别：llm/embedding"),
        sa.Column(
            "model_type",
            sa.String(length=32),
            nullable=False,
            server_default="text_chat",
            comment="模型类型：文本对话/多模态视觉/多模态音频/文本向量",
        ),
        sa.Column("api_base", sa.String(length=512), nullable=False, comment="API Base URL"),
        sa.Column("api_key", sa.String(length=512), nullable=False, comment="API Key"),
        sa.Column("model_name", sa.String(length=128), nullable=False, comment="模型名称"),
        sa.Column("temperature", sa.Float(), nullable=True, comment="温度"),
        sa.Column(
            "timeout_seconds",
            sa.Float(),
            nullable=False,
            server_default="120",
            comment="超时秒数",
        ),
        sa.Column("embedding_dim", sa.Integer(), nullable=True, comment="向量维度"),
        sa.Column("remark", sa.String(length=512), nullable=True, comment="备注"),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
            comment="是否启用",
        ),
        sa.Column(
            "scope_fast",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
            comment="绑定快速对话",
        ),
        sa.Column(
            "scope_deep",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
            comment="绑定深度对话",
        ),
        sa.Column(
            "scope_embedding",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
            comment="绑定Embedding",
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人用户ID"),
        _ts(),
        _ts_updated(),
        sa.PrimaryKeyConstraint("id"),
        comment="模型配置（对话/Embedding）",
    )
    op.create_index("ix_model_configs_public_id", "model_configs", ["public_id"], unique=True)
    op.create_index("ix_model_configs_kind", "model_configs", ["kind"])
    op.create_index("ix_model_configs_model_type", "model_configs", ["model_type"])

    op.create_table(
        "workflows",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("public_id", sa.String(length=32), nullable=False, comment="对外ID"),
        sa.Column("name", sa.String(length=128), nullable=False, comment="工作流名称"),
        sa.Column("remark", sa.Text(), nullable=True, comment="备注"),
        sa.Column(
            "domain",
            sa.String(length=32),
            nullable=False,
            server_default="ai",
            comment="业务域，默认 ai",
        ),
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
        comment="工作流定义",
    )
    op.create_index("ix_workflows_public_id", "workflows", ["public_id"], unique=True)

    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("public_id", sa.String(length=32), nullable=False, comment="对外ID"),
        sa.Column("workflow_id", sa.BigInteger(), nullable=False, comment="所属工作流ID"),
        sa.Column("version", sa.Integer(), nullable=False, comment="版本号"),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="draft",
            comment="状态：draft/published",
        ),
        sa.Column("graph_json", mysql.JSON(), nullable=True, comment="流程图 JSON（nodes/edges）"),
        sa.Column("changelog", sa.Text(), nullable=True, comment="变更说明"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人用户ID"),
        _ts(),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="工作流版本",
    )
    op.create_index("ix_workflow_versions_public_id", "workflow_versions", ["public_id"], unique=True)
    op.create_index("ix_workflow_versions_workflow_id", "workflow_versions", ["workflow_id"])

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("public_id", sa.String(length=32), nullable=False, comment="对外ID"),
        sa.Column("workflow_id", sa.BigInteger(), nullable=False, comment="所属工作流ID"),
        sa.Column("version_id", sa.BigInteger(), nullable=False, comment="使用的版本ID"),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
            comment="状态：pending/running/done/failed",
        ),
        sa.Column("trigger", sa.String(length=32), nullable=True, comment="触发方式：trial/api 等"),
        sa.Column("input_json", mysql.JSON(), nullable=True, comment="输入 JSON"),
        sa.Column("output_json", mysql.JSON(), nullable=True, comment="输出 JSON"),
        sa.Column("error_msg", sa.String(length=512), nullable=True, comment="失败原因"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人用户ID"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True, comment="开始时间"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True, comment="结束时间"),
        _ts(),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["workflow_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        comment="工作流运行记录",
    )
    op.create_index("ix_workflow_runs_public_id", "workflow_runs", ["public_id"], unique=True)
    op.create_index("ix_workflow_runs_workflow_id", "workflow_runs", ["workflow_id"])
    op.create_index("ix_workflow_runs_version_id", "workflow_runs", ["version_id"])

    op.create_table(
        "workflow_run_steps",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("run_id", sa.BigInteger(), nullable=False, comment="所属运行ID"),
        sa.Column("node_id", sa.String(length=64), nullable=False, comment="节点ID"),
        sa.Column("node_type", sa.String(length=32), nullable=False, comment="节点类型"),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
            comment="状态：pending/running/done/failed",
        ),
        sa.Column("detail_json", mysql.JSON(), nullable=True, comment="步骤详情 JSON"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True, comment="开始时间"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True, comment="结束时间"),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="工作流运行步骤",
    )
    op.create_index("ix_workflow_run_steps_run_id", "workflow_run_steps", ["run_id"])

    op.create_table(
        "login_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("username", sa.String(length=64), nullable=False, comment="尝试登录的用户名"),
        sa.Column("user_id", sa.BigInteger(), nullable=True, comment="对应用户ID（未知则为空）"),
        sa.Column(
            "success",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
            comment="是否成功",
        ),
        sa.Column(
            "reason",
            sa.String(length=64),
            nullable=False,
            server_default="",
            comment="结果原因：ok/invalid_credentials/inactive",
        ),
        sa.Column("ip", sa.String(length=64), nullable=False, server_default="", comment="客户端IP"),
        sa.Column(
            "user_agent",
            sa.String(length=512),
            nullable=False,
            server_default="",
            comment="User-Agent",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="创建时间",
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="登录日志",
    )
    op.create_index("ix_login_logs_username", "login_logs", ["username"])
    op.create_index("ix_login_logs_success", "login_logs", ["success"])
    op.create_index("ix_login_logs_created_at", "login_logs", ["created_at"])

    op.create_table(
        "operation_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("module", sa.String(length=32), nullable=False, comment="模块"),
        sa.Column("action", sa.String(length=32), nullable=False, server_default="", comment="动作"),
        sa.Column("method", sa.String(length=16), nullable=False, comment="HTTP方法"),
        sa.Column("path", sa.String(length=512), nullable=False, comment="请求路径"),
        sa.Column("resource_id", sa.String(length=128), nullable=True, comment="资源ID"),
        sa.Column("operator_id", sa.BigInteger(), nullable=True, comment="操作人用户ID"),
        sa.Column("operator_username", sa.String(length=64), nullable=True, comment="操作人用户名"),
        sa.Column(
            "success",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
            comment="是否成功",
        ),
        sa.Column("status_code", sa.Integer(), nullable=False, server_default="0", comment="HTTP状态码"),
        sa.Column("ip", sa.String(length=64), nullable=False, server_default="", comment="客户端IP"),
        sa.Column(
            "user_agent",
            sa.String(length=512),
            nullable=False,
            server_default="",
            comment="User-Agent",
        ),
        sa.Column("detail", sa.String(length=512), nullable=True, comment="摘要（已脱敏）"),
        sa.Column("error_msg", sa.String(length=512), nullable=True, comment="失败原因"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0", comment="耗时毫秒"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="创建时间",
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="操作日志",
    )
    op.create_index("ix_operation_logs_module", "operation_logs", ["module"])
    op.create_index("ix_operation_logs_operator_username", "operation_logs", ["operator_username"])
    op.create_index("ix_operation_logs_success", "operation_logs", ["success"])
    op.create_index("ix_operation_logs_created_at", "operation_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("operation_logs")
    op.drop_table("login_logs")
    op.drop_table("workflow_run_steps")
    op.drop_table("workflow_runs")
    op.drop_table("workflow_versions")
    op.drop_table("workflows")
    op.drop_table("model_configs")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_table("user_roles")
    op.drop_table("roles")
    op.drop_table("users")
