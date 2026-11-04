import { apiDownload, apiRequest, ApiError, getApiBaseUrl } from './api';
import { getAccessToken } from './auth';

export type KnowledgeBaseItem = {
  id: string;
  name: string;
  description?: string | null;
  purpose?: 'rag' | 'asset' | string;
  status: string;
  docCount: number;
  chunkCount: number;
  createdAt: number;
  updatedAt: number;
  createdAtUtc?: boolean;
  canView?: boolean;
  canUse?: boolean;
  canManage?: boolean;
};

export type KbDocItem = {
  id: string;
  baseId?: string | null;
  name: string;
  source: 'file' | 'url' | 'text' | string;
  kind?: 'doc' | 'drawing' | '3d' | 'video' | string;
  parentId?: string | null;
  parentName?: string | null;
  fileType?: string;
  size?: number;
  url?: string;
  fileKey?: string;
  previewUrl?: string;
  summary?: string;
  charCount: number;
  chunks?: number;
  tags?: string[];
  uploader?: string;
  status: 'ready' | 'failed' | 'parsing' | string;
  errorMsg?: string | null;
  pageCount?: number;
  ocrPages?: number;
  ocrCapped?: boolean;
  formulaFallback?: boolean;
  reviewStatus?: 'pending' | 'approved' | 'rejected' | string;
  reviewComment?: string | null;
  taskId?: string | null;
  duplicate?: boolean;
  createdAt: number;
  createdAtUtc?: boolean;
  canView?: boolean;
  canUse?: boolean;
  canManage?: boolean;
};

export type SearchChunk = {
  content: string;
  score: number;
  doc_id?: string;
  kb_id?: string;
  name?: string;
  chunk_index?: number;
  tags?: string[];
  file_type?: string;
  has_file?: boolean;
  /** pdf | image | file | video | "" */
  preview_kind?: string;
  startMs?: number;
  endMs?: number;
};

function requireToken(): string {
  const token = getAccessToken();
  if (!token) {
    throw new Error('请先登录后再操作知识库');
  }
  return token;
}

export type KbAccess = 'view' | 'use' | 'manage';

export type KbAclGrant = {
  id?: number;
  subjectType: 'user' | 'role';
  subjectId: number;
  subjectLabel?: string;
  canView: boolean;
  canUse: boolean;
  canManage: boolean;
};

export type KbAclDirectoryUser = {
  id: number;
  username: string;
  displayName?: string | null;
  department?: string | null;
};

export type KbAclDirectoryRole = {
  id: number;
  code: string;
  name: string;
};

export type KbAclPayload = {
  baseId: string;
  createdBy: number | null;
  grants: KbAclGrant[];
  directory: { users: KbAclDirectoryUser[]; roles: KbAclDirectoryRole[] };
  note?: string;
};

export async function listKnowledgeBaseCatalog(opts?: {
  access?: KbAccess;
}): Promise<{ items: KnowledgeBaseItem[]; canCreate: boolean }> {
  const q = opts?.access ? `?access=${encodeURIComponent(opts.access)}` : '';
  const data = await apiRequest<{ items: KnowledgeBaseItem[]; canCreate?: boolean }>(
    `/api/v1/knowledge/bases${q}`,
    { token: requireToken() },
  );
  return { items: data.items || [], canCreate: Boolean(data.canCreate) };
}

export async function listKnowledgeBases(opts?: { access?: KbAccess }): Promise<KnowledgeBaseItem[]> {
  const { items } = await listKnowledgeBaseCatalog(opts);
  return items;
}

export async function createKnowledgeBase(input: {
  name: string;
  description?: string;
}): Promise<KnowledgeBaseItem> {
  const data = await apiRequest<{ item: KnowledgeBaseItem }>('/api/v1/knowledge/bases', {
    method: 'POST',
    token: requireToken(),
    body: input,
  });
  return data.item;
}

export async function getKnowledgeBase(baseId: string): Promise<KnowledgeBaseItem> {
  const data = await apiRequest<{ item: KnowledgeBaseItem }>(
    `/api/v1/knowledge/bases/${encodeURIComponent(baseId)}`,
    { token: requireToken() }
  );
  return data.item;
}

export async function updateKnowledgeBase(
  baseId: string,
  input: { name?: string; description?: string }
): Promise<KnowledgeBaseItem> {
  const data = await apiRequest<{ item: KnowledgeBaseItem }>(
    `/api/v1/knowledge/bases/${encodeURIComponent(baseId)}`,
    { method: 'PATCH', token: requireToken(), body: input }
  );
  return data.item;
}

export async function deleteKnowledgeBase(baseId: string): Promise<void> {
  await apiRequest(`/api/v1/knowledge/bases/${encodeURIComponent(baseId)}`, {
    method: 'DELETE',
    token: requireToken(),
  });
}

export async function listKnowledgeDocuments(
  baseId: string,
  opts?: { reviewStatus?: string },
): Promise<KbDocItem[]> {
  const params = new URLSearchParams({ baseId });
  if (opts?.reviewStatus) params.set('reviewStatus', opts.reviewStatus);
  const data = await apiRequest<{ items: KbDocItem[] }>(
    `/api/v1/knowledge/documents?${params.toString()}`,
    { token: requireToken() },
  );
  return data.items || [];
}

export async function checkKnowledgeDocumentDuplicate(input: {
  baseId: string;
  name: string;
  url?: string;
}): Promise<{ duplicates: KbDocItem[]; exists: boolean }> {
  const params = new URLSearchParams({
    baseId: input.baseId,
    name: input.name,
  });
  if (input.url?.trim()) params.set('url', input.url.trim());
  return apiRequest(`/api/v1/knowledge/documents/check-duplicate?${params.toString()}`, {
    token: requireToken(),
  });
}

export type UploadProgress = {
  loaded: number;
  total: number;
  percent: number;
};

function parseEnvelope<T>(raw: string, status: number): T {
  let payload: { code?: number; msg?: string; data?: T } | null = null;
  try {
    payload = JSON.parse(raw) as { code?: number; msg?: string; data?: T };
  } catch {
    throw new ApiError(status === 0 ? '服务暂时不可用，请稍后重试' : '请求失败', -1, status);
  }
  if (status >= 400 || payload.code !== 0) {
    throw new ApiError(
      (payload.msg && payload.msg.trim()) || '请求失败',
      payload.code ?? -1,
      status,
    );
  }
  return payload.data as T;
}

export async function uploadKnowledgeDocument(
  input: {
    baseId: string;
    file: File;
    name?: string;
    tags?: string[];
    parentId?: string;
    asAttachment?: boolean;
    force?: boolean;
  },
  onProgress?: (p: UploadProgress) => void
): Promise<{ item: KbDocItem; items: KbDocItem[]; replaced?: KbDocItem[] }> {
  const form = new FormData();
  form.append('file', input.file);
  form.append('baseId', input.baseId);
  const name = (input.name || input.file.name || '').trim();
  if (name) form.append('name', name);
  if (input.tags?.length) form.append('tags', input.tags.join(','));
  if (input.parentId) form.append('parentId', input.parentId);
  if (input.asAttachment != null) form.append('asAttachment', input.asAttachment ? '1' : '0');
  if (input.force) form.append('force', 'true');

  const send = (token: string) =>
    new Promise<{ item: KbDocItem; items: KbDocItem[]; replaced?: KbDocItem[] }>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', `${getApiBaseUrl()}/api/v1/knowledge/documents/upload`);
      xhr.setRequestHeader('Accept', 'application/json');
      xhr.setRequestHeader('Authorization', `Bearer ${token}`);
      xhr.setRequestHeader('X-Access-Token', token);
      xhr.upload.onprogress = (ev) => {
        if (!onProgress) return;
        const total = ev.lengthComputable ? ev.total : input.file.size;
        const loaded = ev.loaded;
        const percent = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0;
        onProgress({ loaded, total, percent });
      };
      xhr.onload = () => {
        try {
          resolve(parseEnvelope(xhr.responseText, xhr.status));
        } catch (e) {
          reject(e);
        }
      };
      xhr.onerror = () => {
        reject(new ApiError('无法连接后端服务，请确认 API 已启动', -1, 0));
      };
      xhr.onabort = () => {
        reject(new ApiError('上传已取消', -1, 0));
      };
      xhr.send(form);
    });

  try {
    return await send(requireToken());
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      const { refreshTokens, getAccessToken, clearTokens } = await import('./auth');
      const refreshed = await refreshTokens();
      if (refreshed) {
        const token = getAccessToken();
        if (token) return send(token);
      }
      clearTokens();
    }
    throw e;
  }
}

export async function downloadKnowledgeDocument(
  baseId: string,
  docId: string,
  fallbackName?: string
): Promise<void> {
  await apiDownload(
    `/api/v1/knowledge/documents/${encodeURIComponent(docId)}/download?baseId=${encodeURIComponent(baseId)}`,
    { token: requireToken(), fallbackName: fallbackName || 'document' }
  );
}

/** 知识库原件流式地址（Range）；供 <video src>，默认带 access_token */
export function knowledgeDocumentFilePath(
  baseId: string,
  docId: string,
  opts?: { withToken?: boolean },
): string {
  const q = new URLSearchParams()
  q.set('baseId', baseId)
  if (opts?.withToken !== false) {
    const t = getAccessToken()
    if (t) q.set('access_token', t)
  }
  return `/api/v1/knowledge/documents/${encodeURIComponent(docId)}/file?${q.toString()}`
}

export async function createTextDocument(input: {
  baseId: string;
  title: string;
  content: string;
  uploader?: string;
  tags?: string[];
  force?: boolean;
}): Promise<{ item: KbDocItem; items: KbDocItem[]; replaced?: KbDocItem[] }> {
  return apiRequest('/api/v1/knowledge/documents/from-text', {
    method: 'POST',
    token: requireToken(),
    body: input,
  });
}

export async function createUrlDocument(input: {
  baseId: string;
  url: string;
  title?: string;
  uploader?: string;
  tags?: string[];
  force?: boolean;
}): Promise<{ item: KbDocItem; items: KbDocItem[]; replaced?: KbDocItem[] }> {
  return apiRequest('/api/v1/knowledge/documents/from-url', {
    method: 'POST',
    token: requireToken(),
    body: input,
  });
}

export async function searchKnowledge(input: {
  query: string;
  baseId?: string;
  topK?: number;
}): Promise<SearchChunk[]> {
  const data = await apiRequest<{ chunks: SearchChunk[] }>('/api/v1/knowledge/search', {
    method: 'POST',
    token: requireToken(),
    body: input,
  });
  return data.chunks || [];
}

export async function getKnowledgeDocumentPreview(
  baseId: string,
  docId: string
): Promise<{
  item: KbDocItem;
  chunks: Array<{ chunkIndex: number; content: string }>;
  content: string;
  truncated: boolean;
}> {
  return apiRequest(
    `/api/v1/knowledge/documents/${encodeURIComponent(docId)}/preview?baseId=${encodeURIComponent(baseId)}`,
    { token: requireToken() }
  );
}

export async function reparseKnowledgeDocument(
  baseId: string,
  docId: string
): Promise<{ item: KbDocItem; items: KbDocItem[] }> {
  return apiRequest(
    `/api/v1/knowledge/documents/${encodeURIComponent(docId)}/reparse?baseId=${encodeURIComponent(baseId)}`,
    { method: 'POST', token: requireToken() }
  );
}

export async function attachKnowledgeDocument(input: {
  baseId: string;
  docId: string;
  parentId?: string | null;
  asAttachment?: boolean;
}): Promise<{ item: KbDocItem; items: KbDocItem[] }> {
  return apiRequest(
    `/api/v1/knowledge/documents/${encodeURIComponent(input.docId)}/attach`,
    {
      method: 'PATCH',
      token: requireToken(),
      body: {
        baseId: input.baseId,
        parentId: input.parentId || null,
        asAttachment: input.asAttachment ?? true,
      },
    },
  );
}

export async function deleteKnowledgeDocument(baseId: string, docId: string): Promise<KbDocItem[]> {
  const data = await apiRequest<{ items: KbDocItem[] }>(
    `/api/v1/knowledge/documents/${encodeURIComponent(docId)}?baseId=${encodeURIComponent(baseId)}`,
    { method: 'DELETE', token: requireToken() }
  );
  return data.items || [];
}

export async function getKnowledgeBaseAcl(baseId: string): Promise<KbAclPayload> {
  return apiRequest<KbAclPayload>(
    `/api/v1/knowledge/bases/${encodeURIComponent(baseId)}/acl`,
    { token: requireToken() },
  );
}

export async function saveKnowledgeBaseAcl(
  baseId: string,
  grants: KbAclGrant[],
): Promise<KbAclPayload> {
  return apiRequest<KbAclPayload>(
    `/api/v1/knowledge/bases/${encodeURIComponent(baseId)}/acl`,
    {
      method: 'PUT',
      token: requireToken(),
      body: {
        grants: grants.map((g) => ({
          subjectType: g.subjectType,
          subjectId: g.subjectId,
          canView: g.canView,
          canUse: g.canUse,
          canManage: g.canManage,
        })),
      },
    },
  );
}

export type KbIngestTask = {
  id: string;
  baseId?: string | null;
  docId?: string | null;
  docName?: string | null;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | string;
  progress: number;
  errorMsg?: string | null;
  createdAt: number;
  startedAt?: number;
  finishedAt?: number;
};

export async function listKnowledgeIngestTasks(baseId: string): Promise<KbIngestTask[]> {
  const data = await apiRequest<{ items: KbIngestTask[] }>(
    `/api/v1/knowledge/ingest-tasks?baseId=${encodeURIComponent(baseId)}`,
    { token: requireToken() },
  );
  return data.items || [];
}

export async function cancelKnowledgeIngestTask(baseId: string, taskId: string): Promise<void> {
  await apiRequest(
    `/api/v1/knowledge/ingest-tasks/${encodeURIComponent(taskId)}/cancel?baseId=${encodeURIComponent(baseId)}`,
    { method: 'POST', token: requireToken() },
  );
}

export async function reviewKnowledgeDocument(input: {
  baseId: string;
  docId: string;
  action: 'approve' | 'reject';
  comment?: string;
}): Promise<{ item: KbDocItem; items: KbDocItem[] }> {
  return apiRequest(
    `/api/v1/knowledge/documents/${encodeURIComponent(input.docId)}/review`,
    {
      method: 'POST',
      token: requireToken(),
      body: {
        baseId: input.baseId,
        action: input.action,
        comment: input.comment || null,
      },
    },
  );
}

export type QdrantSettings = {
  name: string;
  exists: boolean;
  points: number;
  vectorSize?: number | null;
  quantization?: string;
  envQuantization?: string;
};

export async function getQdrantSettings(): Promise<QdrantSettings> {
  const data = await apiRequest<{ item: QdrantSettings }>('/api/v1/knowledge/qdrant/settings', {
    token: requireToken(),
  });
  return data.item;
}

export async function applyQdrantSettings(): Promise<{
  item: QdrantSettings;
  reindex: { reindexed: number; failed: number; total: number };
}> {
  return apiRequest('/api/v1/knowledge/qdrant/settings/apply', {
    method: 'POST',
    token: requireToken(),
    body: { confirm: true },
  });
}
