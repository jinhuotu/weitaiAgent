import { apiRequest, getApiBaseUrl } from './api';
import { getAccessToken } from './auth';

export type ChatMode = 'fast' | 'deep';

export type ChatSessionItem = {
  id: string;
  title: string;
  titleAuto: boolean;
  mode: ChatMode | string;
  summary?: string | null;
  /** 会话最近一次勾选的知识库，打开历史时用于恢复勾选 */
  knowledgeBaseIds?: string[];
  messageCount: number;
  lastMessageAt: number;
  createdAt: number;
  updatedAt: number;
  messages?: ChatSessionMessage[];
};

export type ChatSessionMessage = {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'tool' | string;
  content: string;
  mode?: string | null;
  refs?: {
    content: string;
    score: number;
    doc_id?: string;
    kb_id?: string;
    kbId?: string;
    name?: string;
    chunk_index?: number;
    file_type?: string;
    has_file?: boolean;
    /** pdf | image | file | video | "" */
    preview_kind?: string;
    kind?: string;
    startMs?: number;
    endMs?: number;
  }[];
  knowledgeBaseIds?: string[];
  useKnowledge?: boolean;
  createdAt: number;
  toolName?: string | null;
  toolInput?: unknown;
  toolOutput?: unknown;
  toolError?: string | null;
  toolDurationMs?: number | null;
  images?: {
    mimeType: string;
    dataUrl?: string;
    fileKey?: string;
  }[];
  attachments?: {
    fileName: string;
    downloadName?: string;
    kind?: string;
    label?: string;
  }[];
};

function requireToken(): string {
  const token = getAccessToken();
  if (!token) throw new Error('请先登录后再使用智能问答');
  return token;
}

export async function listChatSessions(): Promise<ChatSessionItem[]> {
  const data = await apiRequest<{ items: ChatSessionItem[] }>('/api/v1/ai/sessions', {
    token: requireToken(),
  });
  return data.items || [];
}

export async function createChatSession(input?: {
  title?: string;
  mode?: ChatMode;
}): Promise<ChatSessionItem> {
  const data = await apiRequest<{ item: ChatSessionItem }>('/api/v1/ai/sessions', {
    method: 'POST',
    token: requireToken(),
    body: input || {},
  });
  return data.item;
}

export async function getChatSession(sessionId: string): Promise<ChatSessionItem> {
  const data = await apiRequest<{ item: ChatSessionItem }>(
    `/api/v1/ai/sessions/${encodeURIComponent(sessionId)}`,
    { token: requireToken() }
  );
  return data.item;
}

export async function updateChatSession(
  sessionId: string,
  input: { title?: string; mode?: ChatMode }
): Promise<ChatSessionItem> {
  const data = await apiRequest<{ item: ChatSessionItem }>(
    `/api/v1/ai/sessions/${encodeURIComponent(sessionId)}`,
    { method: 'PATCH', token: requireToken(), body: input }
  );
  return data.item;
}

export async function deleteChatSession(sessionId: string): Promise<void> {
  await apiRequest(`/api/v1/ai/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
    token: requireToken(),
  });
}

/** 停止生成：强制释放后端会话锁，避免点停止后重问 Failed to fetch */
export async function cancelChatSession(sessionId: string): Promise<void> {
  await apiRequest(`/api/v1/ai/sessions/${encodeURIComponent(sessionId)}/cancel`, {
    method: 'POST',
    token: requireToken(),
  });
}

export async function summarizeChatSessionTitle(sessionId: string): Promise<ChatSessionItem> {
  const data = await apiRequest<{ item: ChatSessionItem }>(
    `/api/v1/ai/sessions/${encodeURIComponent(sessionId)}/summarize-title`,
    { method: 'POST', token: requireToken() }
  );
  return data.item;
}

export async function fetchRelatedQuestions(input: {
  question: string;
  answer: string;
}): Promise<string[]> {
  const data = await apiRequest<{ questions: string[] }>('/api/v1/ai/chat/related', {
    method: 'POST',
    token: requireToken(),
    body: input,
  });
  return data.questions || [];
}

export type ToolEventPayload = {
  phase: 'call' | 'result';
  toolCallId?: string;
  name?: string;
  toolName?: string;
  serverId?: string | null;
  arguments?: Record<string, unknown>;
  content?: string;
  error?: string | null;
  durationMs?: number;
};

export type StreamHandlers = {
  onRefs?: (chunks: unknown[], meta?: { agentId?: string; agentName?: string; useKnowledge?: boolean }) => void;
  onDelta?: (text: string) => void;
  onTool?: (payload: ToolEventPayload) => void;
  onDone?: (payload: {
    ok?: boolean;
    title?: string;
    sessionId?: string;
    agentId?: string;
  }) => void;
  onError?: (msg: string) => void;
};

/** 直连后端 SSE：/api/v1/ai/chat（服务端 Redis 窗口为上下文权威源，只传本轮 content） */
export async function streamChat(
  input: {
    content: string;
    mode: ChatMode;
    sessionId: string;
    /** @deprecated 以 knowledgeBaseIds 为准；未选 = 不检索 */
    useKnowledge?: boolean;
    /** 选中的知识库 publicId；空数组 = 不检索 */
    knowledgeBaseIds?: string[];
    /** 选中的提示词 publicId；未传/空 = 不注入系统提示词基座 */
    promptId?: string | null;
    /** 场景智能体：服务端以智能体配置覆盖 mode/prompt/KB/工具 */
    agentId?: string | null;
    /** 指定模型配置 id；未传则用快速/深度默认绑定 */
    modelId?: string | null;
    /** 本轮附图（jpeg/png/webp/gif/pdf/dxf/dwg，Base64，不含 data: 前缀） */
    images?: { mimeType: string; data: string; fileName?: string }[];
  },
  handlers: StreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  const token = requireToken();
  const knowledgeBaseIds = (input.knowledgeBaseIds || []).filter(Boolean);
  const promptId = (input.promptId || '').trim() || undefined;
  const agentId = (input.agentId || '').trim() || undefined;
  const modelId = (input.modelId || '').trim() || undefined;
  const images = (input.images || [])
    .filter((img) => img?.data)
    .map((img) => ({
      mimeType: img.mimeType || 'image/jpeg',
      data: img.data,
      ...(img.fileName ? { fileName: img.fileName } : {}),
    }));
  const res = await fetch(`${getApiBaseUrl()}/api/v1/ai/chat`, {
    method: 'POST',
    headers: {
      Accept: 'text/event-stream',
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      content: input.content,
      mode: input.mode,
      sessionId: input.sessionId,
      knowledgeBaseIds,
      useKnowledge: knowledgeBaseIds.length > 0,
      ...(promptId ? { promptId } : {}),
      ...(agentId ? { agentId } : {}),
      ...(modelId ? { modelId } : {}),
      ...(images.length ? { images } : {}),
    }),
    signal,
  });

  if (!res.ok || !res.body) {
    let msg = `服务异常 ${res.status}`;
    try {
      const j = await res.json();
      msg = j.msg || msg;
    } catch {
      // ignore
    }
    throw new Error(msg);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // 兼容 \n\n 与 \r\n\r\n 分帧
      buffer = buffer.replace(/\r\n/g, '\n');

      let idx: number;
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const lines = block.split('\n');
        let event = 'message';
        const dataParts: string[] = [];
        for (const raw of lines) {
          const ln = raw.replace(/\r$/, '');
          if (ln.startsWith('event:')) {
            event = ln.slice(6).trim();
          } else if (ln.startsWith('data:')) {
            // 兼容 "data: {...}" 与 "data:{...}"
            dataParts.push(ln.slice(5).replace(/^\s/, ''));
          }
        }
        const data = dataParts.join('\n');
        if (!data || data.startsWith(':')) continue;
        try {
          const payload = JSON.parse(data);
          if (event === 'refs') {
            handlers.onRefs?.(payload.chunks || [], {
              agentId: payload.agentId,
              agentName: payload.agentName,
              useKnowledge: payload.useKnowledge,
            });
          } else if (event === 'delta') handlers.onDelta?.(String(payload.text ?? ''));
          else if (event === 'tool') handlers.onTool?.(payload as ToolEventPayload);
          else if (event === 'done') handlers.onDone?.(payload);
          else if (event === 'error') handlers.onError?.(String(payload.msg || 'error'));
        } catch {
          // ignore malformed SSE JSON chunks
        }
      }
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // ignore
    }
  }
}
