import { apiRequest } from './api';
import { getAccessToken } from './auth';

export type ChatMode = 'fast' | 'deep';

export type AgentItem = {
  id: string;
  name: string;
  remark?: string | null;
  enabled: boolean;
  promptId?: string | null;
  knowledgeBaseIds: string[];
  mode: ChatMode;
  mcpToolIds: string[];
  toolsEnabled: boolean;
  createdAt: number;
  updatedAt: number;
};

export type AgentOption = AgentItem;

export type AgentUpsertBody = {
  name: string;
  remark?: string;
  enabled?: boolean;
  promptId?: string | null;
  knowledgeBaseIds?: string[];
  mode?: ChatMode;
  mcpToolIds?: string[];
  toolsEnabled?: boolean;
};

function requireToken(): string {
  const token = getAccessToken();
  if (!token) throw new Error('请先登录');
  return token;
}

/** 对话页：启用中的场景智能体（含配置字段） */
export async function listAgentOptions(): Promise<AgentOption[]> {
  const data = await apiRequest<{ items: AgentOption[] }>('/api/v1/agents/options', {
    token: requireToken(),
  });
  return data.items || [];
}

export async function listAgents(): Promise<AgentItem[]> {
  const data = await apiRequest<{ items: AgentItem[] }>('/api/v1/agents', {
    token: requireToken(),
  });
  return data.items || [];
}

export async function getAgent(id: string): Promise<AgentItem> {
  const data = await apiRequest<{ item: AgentItem }>(
    `/api/v1/agents/${encodeURIComponent(id)}`,
    { token: requireToken() }
  );
  return data.item;
}

export async function createAgent(body: AgentUpsertBody): Promise<AgentItem> {
  const data = await apiRequest<{ item: AgentItem }>('/api/v1/agents', {
    method: 'POST',
    token: requireToken(),
    body,
  });
  return data.item;
}

export async function updateAgent(
  id: string,
  body: Record<string, unknown>
): Promise<AgentItem> {
  const data = await apiRequest<{ item: AgentItem }>(
    `/api/v1/agents/${encodeURIComponent(id)}`,
    { method: 'PATCH', token: requireToken(), body }
  );
  return data.item;
}

export async function deleteAgent(id: string): Promise<void> {
  await apiRequest(`/api/v1/agents/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    token: requireToken(),
  });
}
