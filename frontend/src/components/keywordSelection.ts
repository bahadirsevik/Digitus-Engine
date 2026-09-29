// Keyword arama sonuçları için saf seçim/sıralama yardımcıları.
// GoogleAdsKeywordSearch ve UrlKeywordExtractor paylaşır (huni adım 7/9).

export const LOW_VOLUME_THRESHOLD = 10

export function sortByVolumeDesc<T>(items: T[], getVolume: (item: T) => number): T[] {
  return [...items].sort((a, b) => getVolume(b) - getVolume(a))
}

export function splitLowVolume<T>(
  items: T[],
  getVolume: (item: T) => number,
  threshold: number = LOW_VOLUME_THRESHOLD
): { visible: T[]; low: T[] } {
  const visible: T[] = []
  const low: T[] = []
  for (const item of items) {
    if (getVolume(item) < threshold) low.push(item)
    else visible.push(item)
  }
  return { visible, low }
}

// Hacme göre sıralı listenin ilk N öğesinin anahtarlarını seçer.
export function selectTopN<T>(
  items: T[],
  getKey: (item: T) => string,
  getVolume: (item: T) => number,
  n: number
): Set<string> {
  return new Set(sortByVolumeDesc(items, getVolume).slice(0, n).map(getKey))
}

export function selectMinVolume<T>(
  items: T[],
  getKey: (item: T) => string,
  getVolume: (item: T) => number,
  minVolume: number
): Set<string> {
  return new Set(items.filter((item) => getVolume(item) >= minVolume).map(getKey))
}
