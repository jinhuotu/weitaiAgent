import { apiRequest } from './api';
import { getAccessToken } from './auth';

export type McpTransport = 'streamable_http' | 'sse' | 'stdio';

export type McpToolItem = {
  id: string;
  name: string;
  description: string;
  inputSchema?: Record<string, unknown>;
  enabled: boolean;
  updatedAt: number;
};

export type McpServerItem = {
  id: string;
  name: string;
  transport: McpTransport;
  url?: string | null;
  command?: string | null;
  args: string[];
  envMasked: Record<string, string>;
  headersMasked: Record<string, string>;
  envSecretKeys?: string[];
  headersSecretKeys?: string[];
  timeoutSeconds: number;
  remark?: string | null;
  enabled: boolean;
  lastError?: string | null;
  lastCheckedAt?: number | null;
  toolsCachedAt?: number | null;
  toolCount: number;
  tools: McpToolItem[];
  createdAt: number;
  updatedAt: number;
};

function requireToken(): string {
  const token = getAccessToken();
  if (!token) throw new Error('请先登录');
  return token;
}

export async function listMcpServers(): Promise<McpServerItem[]> {
  const data = await apiRequest<{ items: McpServerItem[] }>('/api/v1/mcp-servers', {
    token: requireToken(),
  });
  return data.items || [];
}

export async function createMcpServer(body: {
  name: string;
  transport: McpTransport;
  url?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  headers?: Record<string, string>;
  timeoutSeconds?: number;
  remark?: string;
  enabled?: boolean;
}): Promise<McpServerItem> {
  const data = await apiRequest<{ item: McpServerItem }>('/api/v1/mcp-servers', {
    method: 'POST',
    token: requireToken(),
    body,
  });
  return data.item;
}

export async function updateMcpServer(
  id: string,
  body: Record<string, unknown>
): Promise<McpServerItem> {
  const data = await apiRequest<{ item: McpServerItem }>(
    `/api/v1/mcp-servers/${encodeURIComponent(id)}`,
    { method: 'PATCH', token: requireToken(), body }
  );
  return data.item;
}

export async function deleteMcpServer(id: string): Promise<void> {
  await apiRequest(`/api/v1/mcp-servers/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    token: requireToken(),
  });
}

export async function healthMcpServer(id: string): Promise<{
  ok: boolean;
  server: McpServerItem;
  probe: { toolCount: number; tools: { name: string; description: string }[] };
}> {
  return apiRequest(`/api/v1/mcp-servers/${encodeURIComponent(id)}/health`, {
    method: 'POST',
    token: requireToken(),
  });
}

export async function refreshMcpTools(id: string): Promise<McpServerItem> {
  const data = await apiRequest<{ item: McpServerItem }>(
    `/api/v1/mcp-servers/${encodeURIComponent(id)}/refresh-tools`,
    { method: 'POST', token: requireToken() }
  );
  return data.item;
}

export async function updateMcpTool(
  serverId: string,
  toolId: string,
  body: { enabled?: boolean }
): Promise<McpServerItem> {
  const data = await apiRequest<{ item: McpServerItem }>(
    `/api/v1/mcp-servers/${encodeURIComponent(serverId)}/tools/${encodeURIComponent(toolId)}`,
    { method: 'PATCH', token: requireToken(), body }
  );
  return data.item;
}
