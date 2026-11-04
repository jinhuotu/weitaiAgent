export type JsonSchema = {
  type?: string; title?: string; description?: string; properties?: Record<string, JsonSchema>
  required?: string[]; items?: JsonSchema; enum?: unknown[]; const?: unknown; default?: unknown
  minLength?: number; maxLength?: number; pattern?: string; minimum?: number; maximum?: number
  min?: number; max?: number; minItems?: number; maxItems?: number; uniqueItems?: boolean
  allOf?: JsonSchema[]; if?: JsonSchema; then?: JsonSchema; else?: JsonSchema
  format?: string; 'x-kind'?: string; 'x-options'?: { value: string; label: string }[]
}
export type Issue = {
  id: string; type: string; severity: 'blocking' | 'warning' | 'info'
  title: string; description: string; status: 'open' | 'resolved' | 'deferred'
  evidence: { label: string; locator: string; quote: string }[]
  action: { kind: string; schema: JsonSchema; options: { value: string; label: string }[]; default: unknown }
  target_action: { operation: string; arguments: unknown; upload?: { field: string; assign: string } } | null
  unavailable_reason?: string | null
}
export type Wizard = {
  id: string; revision: number; stage: string; stage_label: string; progress: { done: number; total: number }
  issues: Issue[]; next_action: string | null; can_auto_continue: boolean; next_hint: string
  retry_required: boolean; retry_action?: string | null; result: any; test_only: boolean; regression_status: string
}

export function validate(schema: JsonSchema, value: unknown, path = schema.title || '表单'): string[] {
  const errors: string[] = []
  const absent = value === undefined || value === null || value === ''
  if (absent) return errors // Presence is checked by the parent required list.
  if (schema.enum && !schema.enum.some(v => Object.is(v, value))) errors.push(`${path}：请选择有效选项`)
  if ('const' in schema && !Object.is(value, schema.const)) errors.push(`${path}：请明确确认后继续`)
  if (schema.type === 'object') {
    if (typeof value !== 'object' || Array.isArray(value)) return [`${path}：格式不正确`]
    const obj = value as Record<string, unknown>
    for (const key of schema.required || []) {
      if (obj[key] === undefined || obj[key] === null || obj[key] === '' || (typeof obj[key] === 'string' && !obj[key].trim()))
        errors.push(`${schema.properties?.[key]?.title || path}：必填`)
    }
    for (const [key, child] of Object.entries(schema.properties || {})) errors.push(...validate(child, obj[key], `${path} / ${child.title || '内容'}`))
  } else if (schema.type === 'array') {
    if (!Array.isArray(value)) return [`${path}：请选择或添加条目`]
    if (value.length < (schema.minItems ?? 0)) errors.push(`${path}：至少选择或添加 ${schema.minItems} 项`)
    if (schema.maxItems !== undefined && value.length > schema.maxItems) errors.push(`${path}：最多 ${schema.maxItems} 项`)
    if (schema.uniqueItems && new Set(value.map(v => JSON.stringify(v))).size !== value.length) errors.push(`${path}：不能重复`)
    value.forEach((v, i) => errors.push(...validate(schema.items || {}, v, `${path} 第 ${i + 1} 项`)))
  } else if (schema.type === 'number' || schema.type === 'integer') {
    if (typeof value !== 'number' || !Number.isFinite(value) || (schema.type === 'integer' && !Number.isInteger(value))) errors.push(`${path}：须为有效数值`)
    else {
      if (value < (schema.minimum ?? schema.min ?? -Infinity)) errors.push(`${path}：数值过小`)
      if (value > (schema.maximum ?? schema.max ?? Infinity)) errors.push(`${path}：数值过大`)
    }
  } else if (schema.type === 'boolean') {
    if (typeof value !== 'boolean') errors.push(`${path}：须明确选择`)
  } else if (schema.type === 'string' && schema.format !== 'binary') {
    if (typeof value !== 'string') return [`${path}：须为文本`]
    if (value.length < (schema.minLength ?? 0)) errors.push(`${path}：内容太短`)
    if (schema.maxLength !== undefined && value.length > schema.maxLength) errors.push(`${path}：内容太长`)
    if (schema.pattern) {
      try { if (!new RegExp(schema.pattern, 'u').test(value)) errors.push(`${path}：格式不符合要求`) }
      catch { errors.push(`${path}：校验规则不可用，请联系管理员`) }
    }
  } else if (schema.format === 'binary' && !(value instanceof File)) errors.push(`${path}：请选择文件`)
  for (const child of schema.allOf || []) errors.push(...validate(child, value, path))
  if (schema.if) errors.push(...validate(validate(schema.if, value, path).length ? schema.else || {} : schema.then || {}, value, path))
  return errors
}

export function initialValue(schema: JsonSchema): any {
  if (schema.default !== undefined) return structuredClone(schema.default)
  if (schema.type === 'object') return {}
  if (schema.type === 'array') return []
  return undefined // Never infer a decision, number, or the first option.
}

export function mapArguments(template: unknown, data: Record<string, any>): any {
  if (Array.isArray(template)) return template.map(v => mapArguments(v, data))
  if (template && typeof template === 'object') {
    const item = template as Record<string, unknown>
    if (Object.keys(item).length === 1 && typeof item.$answer === 'string') {
      if (!Object.prototype.hasOwnProperty.call(data, item.$answer)) throw Error('请完成所有必填信息')
      return data[item.$answer]
    }
    return Object.fromEntries(Object.entries(item).map(([k, v]) => [k, mapArguments(v, data)]))
  }
  return template
}
