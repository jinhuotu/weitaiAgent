import { apiRequest } from './api';
import { getAccessToken } from './auth';

export type PromptItem = {
  id: string;
  name: string;
  content?: string;
  remark?: string | null;
  enabled: boolean;
  createdAt: number;
  updatedAt: number;
};

export type PromptOption = {
  id: string;
  name: string;
  remark?: string | null;
  enabled: boolean;
  createdAt: number;
  updatedAt: number;
};

function requireToken(): string {
  const token = getAccessToken();
  if (!token) throw new Error('请先登录');
  return token;
}

/** 对话页：启用中的提示词选项（不含全文） */
export async function listPromptOptions(): Promise<PromptOption[]> {
  const data = await apiRequest<{ items: PromptOption[] }>('/api/v1/prompts/options', {
    token: requireToken(),
  });
  return data.items || [];
}

export async function listPrompts(): Promise<PromptItem[]> {
  const data = await apiRequest<{ items: PromptItem[] }>('/api/v1/prompts', {
    token: requireToken(),
  });
  return data.items || [];
}

export async function createPrompt(body: {
  name: string;
  content: string;
  remark?: string;
  enabled?: boolean;
}): Promise<PromptItem> {
  const data = await apiRequest<{ item: PromptItem }>('/api/v1/prompts', {
    method: 'POST',
    token: requireToken(),
    body,
  });
  return data.item;
}

export async function updatePrompt(
  id: string,
  body: Record<string, unknown>
): Promise<PromptItem> {
  const data = await apiRequest<{ item: PromptItem }>(
    `/api/v1/prompts/${encodeURIComponent(id)}`,
    { method: 'PATCH', token: requireToken(), body }
  );
  return data.item;
}

export async function deletePrompt(id: string): Promise<void> {
  await apiRequest(`/api/v1/prompts/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    token: requireToken(),
  });
}
