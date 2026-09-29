/**
 * ds-entry.ts — design system dis yuzeyi (claude.ai/design senkronizasyonu).
 *
 * Uygulama bu dosyayi HIC import etmez; yalnizca `.design-sync` donusturucusu
 * icin giris noktasidir (tsconfig.ds.json ile dist-ds/ altina derlenir).
 * Amaci: paylasilan token/stil katmani + tekrar kullanilabilir bilesenleri
 * adlandirilmis export olarak tek bir yuzeyde toplamak.
 *
 * Yeni bir paylasilan bilesen eklenince buraya da export satiri eklenmelidir;
 * aksi halde design system senkronunda gorunmez.
 */

// Token + global sinif katmani (sirasi onemli: fontlar -> token'lar -> siniflar)
import './styles/ds-fonts.css'
import './styles/variables.css'
import './styles/globals.css'

// Router baglami: Layout / ChannelNav / AnalysisBanner gibi bilesenler
// react-router hook'lari kullanir. Onizleme kartlarinin saglayicisi olarak
// kullanilmak uzere disari acilir (bkz. .design-sync/config.json -> provider).
export { MemoryRouter } from 'react-router-dom'

// ── Geri bildirim & durum ────────────────────────────────────────────────
export { default as ErrorBanner } from './components/ErrorBanner'
export { default as TaskProgress } from './components/TaskProgress'
export { ChannelNav, AnalysisBanner, AnalysisToast } from './components/AnalysisProgress'

// ── Form & secim ─────────────────────────────────────────────────────────
export { default as SectionSelect } from './components/SectionSelect'
export { default as StartAnalysisModal } from './components/StartAnalysisModal'
export { default as PolicyPanel } from './components/PolicyPanel'

// ── Veri gosterimi ───────────────────────────────────────────────────────
export { default as KeywordImportResult } from './components/KeywordImportResult'
export { default as ScoreResults } from './components/ScoreResults'

// ── Kabuk & aksiyonlar ───────────────────────────────────────────────────
export { default as Layout } from './components/Layout'
export { default as ExportMenu } from './components/ExportMenu'

// ── Is akisi panelleri (store/API bagimli — onizlemede floor card) ───────
export { default as ChannelWorkspaceView } from './components/ChannelWorkspaceView'
export { default as SocialStepper } from './components/SocialStepper'
export { default as GoogleAdsKeywordSearch } from './components/GoogleAdsKeywordSearch'
export { default as UrlKeywordExtractor } from './components/UrlKeywordExtractor'
export { default as AdsPanel } from './components/generation/AdsPanel'
export { default as SeoGeoPanel } from './components/generation/SeoGeoPanel'
export { default as SocialPanel } from './components/generation/SocialPanel'
