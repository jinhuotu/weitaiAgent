/**
 * createdAt from API is UTC epoch milliseconds once the backend sends
 * `createdAtUtc: true`. Older API processes treated MySQL UTC DATETIME as
 * local time, so the number is 8 hours early on CST hosts — compensate then.
 */

export function toEpochMs(ts: number | string | Date | null | undefined): number {
  if (ts == null || ts === '') return 0
  if (ts instanceof Date) return ts.getTime()
  if (typeof ts === 'number') {
    if (!Number.isFinite(ts) || ts <= 0) return 0
    return ts < 1e12 ? Math.round(ts * 1000) : Math.round(ts)
  }
  const raw = String(ts).trim()
  if (!raw) return 0
  if (/^\d+$/.test(raw)) return toEpochMs(Number(raw))
  const iso = raw.includes('T') ? raw : raw.replace(' ', 'T')
  const hasZone = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso)
  const ms = Date.parse(hasZone ? iso : `${iso}Z`)
  return Number.isFinite(ms) ? ms : 0
}

export function apiTimeMs(
  ts: number | string | Date | null | undefined,
  utc?: boolean | null,
): number {
  const raw = toEpochMs(ts)
  if (!raw || utc) return raw
  return raw - new Date().getTimezoneOffset() * 60_000
}

export function fmtAgo(
  ts: number | string | Date | null | undefined,
  utc?: boolean | null,
): string {
  const diff = Date.now() - apiTimeMs(ts, utc)
  const m = Math.floor(diff / 60_000)
  if (m < 1) return '刚刚'
  if (m < 60) return `${m} 分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小时前`
  const d = Math.floor(h / 24)
  return `${d} 天前`
}
