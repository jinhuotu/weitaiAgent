import { apiRequest, getApiBaseUrl } from './api'
import { getAccessToken } from './auth'

function requireToken(): string {
  const token = getAccessToken()
  if (!token) throw new Error('请先登录')
  return token
}

export type WorkflowNodeType =
  | 'start'
  | 'end'
  | 'knowledge'
  | 'llm'
  | 'agent'
  | 'mcp'
  | 'condition'
  | 'vision'
  | 'image_out'
  | 'layout_out'


export type WorkflowGraphNode = {
  id: string
  type: WorkflowNodeType | string
  position?: { x: number; y: number }
  data?: Record<string, unknown>
}

export type WorkflowGraphEdge = {
  id: string
  source: string
  target: string
  sourceHandle?: string | null
  targetHandle?: string | null
}

export type WorkflowGraph = {
  nodes: WorkflowGraphNode[]
  edges: WorkflowGraphEdge[]
}

export type WorkflowVersion = {
  id: string
  version: number
  status: 'draft' | 'published' | string
  graph: WorkflowGraph
  changelog?: string | null
  createdAt: number
}

export type WorkflowItem = {
  id: string
  name: string
  remark?: string | null
  domain: string
  enabled: boolean
  draftVersion?: WorkflowVersion | null
  publishedVersion?: WorkflowVersion | null
  createdAt: number
  updatedAt: number
}

export type WorkflowRunStep = {
  nodeId: string
  nodeType: string
  status: string
  detail?: Record<string, unknown> | null
  startedAt?: number
  finishedAt?: number
}

export type WorkflowRunItem = {
  id: string
  status: string
  trigger?: string
  input?: unknown
  output?: unknown
  errorMsg?: string | null
  steps?: WorkflowRunStep[]
  startedAt?: number
  finishedAt?: number
}

export type WorkflowOption = {
  id: string
  name: string
  remark?: string | null
  domain?: string
  publishedVersion?: number
}

export async function listWorkflowOptions(): Promise<WorkflowOption[]> {
  const data = await apiRequest<{ items: WorkflowOption[] }>('/api/v1/workflows/options', {
    token: requireToken(),
  })
  return data.items || []
}

export async function listWorkflows(): Promise<WorkflowItem[]> {
  const data = await apiRequest<{ items: WorkflowItem[] }>('/api/v1/workflows', {
    token: requireToken(),
  })
  return data.items || []
}

export async function createWorkflow(body: {
  name: string
  remark?: string
  domain?: string
  enabled?: boolean
}): Promise<WorkflowItem> {
  const data = await apiRequest<{ item: WorkflowItem }>('/api/v1/workflows', {
    method: 'POST',
    token: requireToken(),
    body,
  })
  return data.item
}

export async function getWorkflow(id: string): Promise<WorkflowItem> {
  const data = await apiRequest<{ item: WorkflowItem }>(
    `/api/v1/workflows/${encodeURIComponent(id)}`,
    { token: requireToken() },
  )
  return data.item
}

export async function updateWorkflow(
  id: string,
  body: Partial<{ name: string; remark: string | null; enabled: boolean; domain: string }>,
): Promise<WorkflowItem> {
  const data = await apiRequest<{ item: WorkflowItem }>(
    `/api/v1/workflows/${encodeURIComponent(id)}`,
    { method: 'PATCH', token: requireToken(), body },
  )
  return data.item
}

export async function deleteWorkflow(id: string): Promise<void> {
  await apiRequest(`/api/v1/workflows/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    token: requireToken(),
  })
}

export async function saveWorkflowGraph(id: string, graph: WorkflowGraph): Promise<WorkflowItem> {
  const data = await apiRequest<{ item: WorkflowItem }>(
    `/api/v1/workflows/${encodeURIComponent(id)}/graph`,
    { method: 'PUT', token: requireToken(), body: graph },
  )
  return data.item
}

export async function publishWorkflow(id: string, changelog?: string): Promise<WorkflowItem> {
  const data = await apiRequest<{ item: WorkflowItem }>(
    `/api/v1/workflows/${encodeURIComponent(id)}/publish`,
    { method: 'POST', token: requireToken(), body: { changelog } },
  )
  return data.item
}

export type RunHandlers = {
  onStepStart?: (p: Record<string, unknown>) => void
  onStepEnd?: (p: Record<string, unknown>) => void
  onDelta?: (text: string) => void
  onTool?: (p: Record<string, unknown>) => void
  onDone?: (p: Record<string, unknown>) => void
  onError?: (msg: string) => void
}

export async function runWorkflowTrial(
  id: string,
  input: { input: unknown; useDraft?: boolean; sessionId?: string; trigger?: string },
  handlers: RunHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const token = requireToken()
  const res = await fetch(`${getApiBaseUrl()}/api/v1/workflows/${encodeURIComponent(id)}/runs`, {
    method: 'POST',
    headers: {
      Accept: 'text/event-stream',
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      input: input.input,
      useDraft: input.sessionId ? false : input.useDraft !== false,
      sessionId: input.sessionId || undefined,
      trigger: input.trigger || (input.sessionId ? 'chat' : 'trial'),
    }),
    signal,
  })

  if (!res.ok || !res.body) {
    let msg = `服务异常 ${res.status}`
    try {
      const j = (await res.json()) as { msg?: string }
      msg = j.msg || msg
    } catch {
      /* ignore */
    }
    throw new Error(msg)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      buffer = buffer.replace(/\r\n/g, '\n')
      let idx: number
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)
        let event = 'message'
        const dataParts: string[] = []
        for (const raw of block.split('\n')) {
          const ln = raw.replace(/\r$/, '')
          if (ln.startsWith('event:')) event = ln.slice(6).trim()
          else if (ln.startsWith('data:')) dataParts.push(ln.slice(5).replace(/^\s/, ''))
        }
        const data = dataParts.join('\n')
        if (!data) continue
        try {
          const payload = JSON.parse(data) as Record<string, unknown>
          if (event === 'step_start') handlers.onStepStart?.(payload)
          else if (event === 'step_end') handlers.onStepEnd?.(payload)
          else if (event === 'delta') handlers.onDelta?.(String(payload.text || ''))
          else if (event === 'tool') handlers.onTool?.(payload)
          else if (event === 'done') handlers.onDone?.(payload)
          else if (event === 'error') handlers.onError?.(String(payload.msg || 'error'))
        } catch {
          /* ignore */
        }
      }
    }
  } finally {
    try {
      await reader.cancel()
    } catch {
      /* ignore */
    }
  }
}
