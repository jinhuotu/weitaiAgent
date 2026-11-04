import { apiRequest } from './api';
import { getAccessToken } from './auth';

export type ModelKind = 'llm' | 'embedding';

export type ModelType =
  | 'text_chat'
  | 'multimodal_vision'
  | 'multimodal_audio'
  | 'text_embedding';

export const LLM_MODEL_TYPE_OPTIONS: { value: ModelType; label: string }[] = [
  { value: 'text_chat', label: '文本对话' },
  { value: 'multimodal_vision', label: '多模态视觉' },
  { value: 'multimodal_audio', label: '多模态音频' },
];

export const EMBEDDING_MODEL_TYPE_OPTIONS: { value: ModelType; label: string }[] = [
  { value: 'text_embedding', label: '文本向量' },
];

export function coerceModelType(
  raw: string | null | undefined,
  kind?: ModelKind
): ModelType {
  const all = [...LLM_MODEL_TYPE_OPTIONS, ...EMBEDDING_MODEL_TYPE_OPTIONS]
  if (raw && all.some((x) => x.value === raw)) {
    return raw as ModelType
  }
  return kind === 'embedding' ? 'text_embedding' : 'text_chat'
}

const NATIVE_AIGC_SUFFIXES = [
  '/api/v1/services/aigc/multimodal-generation/generation',
  '/api/v1/services/aigc/multimodal-generation',
  '/api/v1/services/aigc/text-generation/generation',
  '/api/v1/services/aigc/text-generation',
]
const OPENAI_SUFFIXES = ['/chat/completions', '/completions', '/embeddings', '/images/generations']

function hostIsPrivateOrLocal(host: string): boolean {
  const name = (host || '').split(':')[0].trim().toLowerCase().replace(/^\[|\]$/g, '')
  if (['localhost', '127.0.0.1', '::1', '0.0.0.0'].includes(name)) return true
  const parts = name.split('.')
  if (parts.length === 4 && parts.every((p) => /^\d+$/.test(p) && Number(p) <= 255)) {
    const a = Number(parts[0])
    const b = Number(parts[1])
    if (a === 10 || a === 127) return true
    if (a === 192 && b === 168) return true
    if (a === 172 && b >= 16 && b <= 31) return true
  }
  return false
}

export function isLoopbackApiBase(apiBase: string): boolean {
  const host = (apiBase || '').split('://').pop()?.split('/')[0] || ''
  const name = host.split(':')[0].trim().toLowerCase().replace(/^\[|\]$/g, '')
  return ['127.0.0.1', 'localhost', '::1'].includes(name)
}

export function normalizeApiBase(raw: string): string {
  let text = (raw || '').trim().replace(/\s+/g, '').replace(/^['"]|['"]$/g, '')
  if (!text) return ''
  const schemeMatch = text.match(/^(https?:\/\/)+/i)
  let scheme = 'https'
  if (schemeMatch) {
    const block = schemeMatch[0].toLowerCase()
    scheme = block.startsWith('http://') && !block.includes('https://') ? 'http' : 'https'
    text = text.slice(schemeMatch[0].length)
  }
  text = text.replace(/^(https?:\/\/)+/i, '').replace(/^\/+/, '').replace(/\/+$/, '')
  if (!schemeMatch && hostIsPrivateOrLocal(text.split('/')[0] || '')) {
    scheme = 'http'
  }
  text = text.replace(/^([^/]+)\/+:(\d+)/, '$1:$2').replace(/^([^/:]+):\/+(\d+)/, '$1:$2')
  const lower = `/${text.toLowerCase()}`
  for (const suffix of [...OPENAI_SUFFIXES, ...NATIVE_AIGC_SUFFIXES]) {
    if (lower.endsWith(suffix)) {
      text = text.slice(0, text.length - suffix.length).replace(/\/+$/, '')
      break
    }
  }
  const host = text.split('/')[0] || ''
  const path = text.slice(host.length)
  if (host.toLowerCase().endsWith('.maas.aliyuncs.com') && !path) {
    return `${scheme}://${host}/compatible-mode/v1`
  }
  return text ? `${scheme}://${text}` : ''
}

export function modelTypeLabel(modelType?: string | null, kind?: ModelKind): string {
  const all = [...LLM_MODEL_TYPE_OPTIONS, ...EMBEDDING_MODEL_TYPE_OPTIONS]
  const found = all.find((x) => x.value === modelType)
  if (found) return found.label
  return kind === 'embedding' ? '文本向量' : '文本对话'
}

function normalizeConfigItem(item: ModelConfigItem & { model_type?: string }): ModelConfigItem {
  return {
    ...item,
    modelType: coerceModelType(item.modelType || item.model_type, item.kind),
  }
}

export type ModelConfigItem = {
  id: string;
  name: string;
  kind: ModelKind;
  modelType: ModelType;
  apiBase: string;
  apiKeyMasked: string;
  modelName: string;
  temperature?: number | null;
  timeoutSeconds: number;
  embeddingDim?: number | null;
  remark?: string | null;
  enabled: boolean;
  scopeFast: boolean;
  scopeDeep: boolean;
  scopeEmbedding: boolean;
  createdAt: number;
  updatedAt: number;
};

export type ModelRuntime = {
  llm_configured: boolean;
  embedding_configured: boolean;
  llm_fast: ModelConfigItem | null;
  llm_deep: ModelConfigItem | null;
  embedding: ModelConfigItem | null;
};

function requireToken(): string {
  const token = getAccessToken();
  if (!token) throw new Error('请先登录');
  return token;
}

export async function fetchModelRuntime(): Promise<ModelRuntime> {
  return apiRequest<ModelRuntime>('/api/v1/models/runtime', { token: requireToken() });
}

export type ModelOptionItem = {
  id: string
  name: string
  kind: ModelKind
  modelType: ModelType
  modelName: string
  scopeFast?: boolean
  scopeDeep?: boolean
  scopeEmbedding?: boolean
}

function toOptionItem(item: {
  id: string
  name: string
  kind: ModelKind
  modelType?: string | null
  model_type?: string
  modelName: string
  scopeFast?: boolean
  scopeDeep?: boolean
  scopeEmbedding?: boolean
}): ModelOptionItem {
  return {
    id: item.id,
    name: item.name,
    kind: item.kind,
    modelType: coerceModelType(item.modelType || item.model_type, item.kind),
    modelName: item.modelName,
    scopeFast: Boolean(item.scopeFast),
    scopeDeep: Boolean(item.scopeDeep),
    scopeEmbedding: Boolean(item.scopeEmbedding),
  }
}

function matchOptionParams(
  item: ModelOptionItem,
  params?: { kind?: ModelKind; modelType?: ModelType }
): boolean {
  if (params?.kind && item.kind !== params.kind) return false
  if (params?.modelType && item.modelType !== params.modelType) return false
  return true
}

/** 业务页下拉：启用中的模型。options 失败时回退到管理列表 / runtime。 */
export async function listModelOptions(params?: {
  kind?: ModelKind
  modelType?: ModelType
}): Promise<ModelOptionItem[]> {
  const qs = new URLSearchParams()
  if (params?.kind) qs.set('kind', params.kind)
  if (params?.modelType) qs.set('modelType', params.modelType)
  const q = qs.toString() ? `?${qs}` : ''

  try {
    const data = await apiRequest<{ items: ModelOptionItem[] }>(`/api/v1/models/options${q}`, {
      token: requireToken(),
    })
    const items = (data.items || []).map(toOptionItem).filter((x) => matchOptionParams(x, params))
    if (items.length > 0) return items
  } catch {
    // 旧后端可能没有 /options
  }

  try {
    const configs = await listModelConfigs(params?.kind)
    const items = configs
      .filter((x) => x.enabled)
      .map(toOptionItem)
      .filter((x) => matchOptionParams(x, params))
    if (items.length > 0) return items
  } catch {
    // 非管理员无法读完整列表
  }

  try {
    const runtime = await fetchModelRuntime()
    const seen = new Set<string>()
    const items: ModelOptionItem[] = []
    for (const row of [runtime.llm_fast, runtime.llm_deep, runtime.embedding]) {
      if (!row?.id || seen.has(row.id)) continue
      seen.add(row.id)
      const opt = toOptionItem(row)
      if (matchOptionParams(opt, params)) items.push(opt)
    }
    return items
  } catch {
    return []
  }
}

export async function listModelConfigs(kind?: ModelKind): Promise<ModelConfigItem[]> {
  const q = kind ? `?kind=${kind}` : '';
  const data = await apiRequest<{ items: ModelConfigItem[] }>(`/api/v1/models${q}`, {
    token: requireToken(),
  });
  return (data.items || []).map(normalizeConfigItem);
}

export async function createModelConfig(body: {
  name: string;
  kind: ModelKind;
  modelType: ModelType;
  apiBase: string;
  apiKey?: string;
  modelName: string;
  temperature?: number | null;
  timeoutSeconds?: number;
  embeddingDim?: number | null;
  remark?: string;
  enabled?: boolean;
  scopeFast?: boolean;
  scopeDeep?: boolean;
  scopeEmbedding?: boolean;
}): Promise<ModelConfigItem> {
  const data = await apiRequest<{ item: ModelConfigItem }>('/api/v1/models', {
    method: 'POST',
    token: requireToken(),
    body,
  });
  return normalizeConfigItem(data.item);
}

export async function updateModelConfig(
  id: string,
  body: Record<string, unknown>
): Promise<ModelConfigItem> {
  const data = await apiRequest<{ item: ModelConfigItem }>(
    `/api/v1/models/${encodeURIComponent(id)}`,
    { method: 'PATCH', token: requireToken(), body }
  );
  return normalizeConfigItem(data.item);
}

export async function deleteModelConfig(id: string): Promise<void> {
  await apiRequest(`/api/v1/models/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    token: requireToken(),
  });
}
