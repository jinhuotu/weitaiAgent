"""注册 ORM，供 Alembic ``env.py`` import。

未迁入：窑炉铸造 / 生产 SCADA / 数据治理。
"""

from db.models.agent import ScenarioAgent
from db.models.audit import LoginLog, OperationLog
from db.models.chat import ChatMessage, ChatSession
from db.models.knowledge import (
    KnowledgeBase,
    KnowledgeBaseAcl,
    KnowledgeDocument,
    KnowledgeDocumentReview,
    KnowledgeIngestTask,
)
from db.models.mcp import McpServer, McpTool
from db.models.model_config import ModelConfig
from db.models.prompt import Prompt
from db.models.role import Role, UserRole
from db.models.tender import TenderApprovalLog, TenderRecord
from db.models.user import User
from db.models.workflow import Workflow, WorkflowRun, WorkflowRunStep, WorkflowVersion

__all__ = [
    "User",
    "Role",
    "UserRole",
    "ChatSession",
    "ChatMessage",
    "ModelConfig",
    "LoginLog",
    "OperationLog",
    "Workflow",
    "WorkflowVersion",
    "WorkflowRun",
    "WorkflowRunStep",
    "KnowledgeBase",
    "KnowledgeBaseAcl",
    "KnowledgeDocument",
    "KnowledgeDocumentReview",
    "KnowledgeIngestTask",
    "Prompt",
    "McpServer",
    "McpTool",
    "ScenarioAgent",
    "TenderRecord",
    "TenderApprovalLog",
]
