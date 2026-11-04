/** 知识库卡片配色（沿用主题色，按序号轮换） */
export type LibraryCardAccent = {
  /** 左边条 / 图标底 */
  bar: string
  iconWrap: string
  icon: string
  hoverBorder: string
  hoverTitle: string
  cardBg: string
  pill: string
  glow: string
}

export const LIBRARY_CARD_ACCENTS: LibraryCardAccent[] = [
  {
    bar: 'bg-molybdenum',
    iconWrap: 'bg-molybdenum/15 ring-1 ring-molybdenum/25',
    icon: 'text-molybdenum',
    hoverBorder: 'hover:border-molybdenum/55',
    hoverTitle: 'group-hover:text-molybdenum',
    cardBg: 'bg-gradient-to-br from-molybdenum/[0.07] via-bg-elevated/50 to-bg-elevated/30',
    pill: 'bg-molybdenum/10 text-molybdenum border-molybdenum/20',
    glow: 'group-hover:shadow-[0_8px_28px_-12px_rgba(96,165,250,0.35)]',
  },
  {
    bar: 'bg-patina',
    iconWrap: 'bg-patina/15 ring-1 ring-patina/25',
    icon: 'text-patina',
    hoverBorder: 'hover:border-patina/55',
    hoverTitle: 'group-hover:text-patina',
    cardBg: 'bg-gradient-to-br from-patina/[0.08] via-bg-elevated/50 to-bg-elevated/30',
    pill: 'bg-patina/10 text-patina border-patina/20',
    glow: 'group-hover:shadow-[0_8px_28px_-12px_rgba(52,211,153,0.32)]',
  },
  {
    bar: 'bg-sulfur',
    iconWrap: 'bg-sulfur/15 ring-1 ring-sulfur/25',
    icon: 'text-sulfur',
    hoverBorder: 'hover:border-sulfur/50',
    hoverTitle: 'group-hover:text-sulfur',
    cardBg: 'bg-gradient-to-br from-sulfur/[0.07] via-bg-elevated/50 to-bg-elevated/30',
    pill: 'bg-sulfur/10 text-sulfur border-sulfur/20',
    glow: 'group-hover:shadow-[0_8px_28px_-12px_rgba(250,204,21,0.28)]',
  },
  {
    bar: 'bg-iron',
    iconWrap: 'bg-iron/15 ring-1 ring-iron/30',
    icon: 'text-iron',
    hoverBorder: 'hover:border-iron/45',
    hoverTitle: 'group-hover:text-iron',
    cardBg: 'bg-gradient-to-br from-iron/[0.08] via-bg-elevated/50 to-bg-elevated/30',
    pill: 'bg-iron/10 text-iron border-iron/25',
    glow: 'group-hover:shadow-[0_8px_28px_-12px_rgba(248,113,113,0.28)]',
  },
]

export function libraryCardAccent(index: number): LibraryCardAccent {
  return LIBRARY_CARD_ACCENTS[index % LIBRARY_CARD_ACCENTS.length]!
}

/** 按名称关键词微调配色（有命中则优先） */
export function libraryCardAccentByName(name: string, fallbackIndex: number): LibraryCardAccent {
  const n = (name || '').toLowerCase()
  if (/能耗|能源|energy/.test(n)) return LIBRARY_CARD_ACCENTS[0]!
  if (/碳|排放|carbon/.test(n)) return LIBRARY_CARD_ACCENTS[1]!
  if (/缺陷|质量|defect|quality/.test(n)) return LIBRARY_CARD_ACCENTS[2]!
  if (/运维|检修|维护|ops|maintain/.test(n)) return LIBRARY_CARD_ACCENTS[3]!
  return libraryCardAccent(fallbackIndex)
}
