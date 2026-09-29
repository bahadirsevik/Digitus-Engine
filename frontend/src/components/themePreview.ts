export interface ThemeExclusionPreview {
  keyword: string
  matched: string
}

const MIN_STEM_LEN = 3
const PREFIX_OVERLAP = 4

const TURKISH_CHAR_MAP: Record<string, string> = {
  ç: 'c',
  ğ: 'g',
  ı: 'i',
  ö: 'o',
  ş: 's',
  ü: 'u',
}

export function normalizeThemeText(text: string): string {
  return (text || '')
    .toLocaleLowerCase('tr-TR')
    .replace(/[çğıöşü]/g, (ch) => TURKISH_CHAR_MAP[ch] || ch)
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

function stems(text: string): string[] {
  return normalizeThemeText(text)
    .split(/\s+/)
    .filter((token) => token.length >= MIN_STEM_LEN)
}

function stemMatches(themeStem: string, itemStem: string): boolean {
  if (themeStem === itemStem || themeStem.startsWith(itemStem) || itemStem.startsWith(themeStem)) {
    return true
  }

  let overlap = 0
  for (let i = 0; i < Math.min(themeStem.length, itemStem.length); i += 1) {
    if (themeStem[i] !== itemStem[i]) break
    overlap += 1
  }
  return overlap >= PREFIX_OVERLAP
}

export function matchExcludeTheme(keyword: string, excludeThemes: string[]): string | null {
  const itemStems = stems(keyword)
  if (itemStems.length === 0) return null

  for (const theme of excludeThemes || []) {
    if (typeof theme !== 'string') continue
    const themeStems = stems(theme)
    if (themeStems.length === 0) continue
    const allThemeStemsMatch = themeStems.every((themeStem) =>
      itemStems.some((itemStem) => stemMatches(themeStem, itemStem))
    )
    if (allThemeStemsMatch) return theme
  }

  return null
}

export function getExcludeThemesFromProfile(profile: Record<string, unknown> | null | undefined) {
  const raw = profile?.exclude_themes
  if (!Array.isArray(raw)) return []
  return raw.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
}

export function previewThemeExclusions<T>(
  items: T[],
  getKeyword: (item: T) => string,
  excludeThemes: string[]
): ThemeExclusionPreview[] {
  const previews: ThemeExclusionPreview[] = []
  for (const item of items) {
    const keyword = getKeyword(item)
    const matched = matchExcludeTheme(keyword, excludeThemes)
    if (matched) previews.push({ keyword, matched })
  }
  return previews
}
