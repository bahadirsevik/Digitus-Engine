import { create } from 'zustand'
import { authApi, UNAUTHENTICATED_EVENT, type AuthUser } from '../services/api'

/**
 * Oturum durumu.
 *
 * DIKKAT — kalıcılaştırma (persist) BİLEREK yok:
 * Oturumun tek kaynağı sunucudaki HttpOnly cookie'dir. Kullanıcıyı
 * localStorage'a yazmak "giriş yapmış" görünen ama sunucuda oturumu olmayan
 * bir arayüz üretir (cookie süresi dolduğunda veya oturum iptal edildiğinde).
 * Bunun yerine uygulama açılışında /auth/me ile sunucuya sorulur.
 *
 * status:
 *   'unknown'       -> henüz sorulmadı, ekran gösterilmemeli
 *   'authenticated' -> girişli
 *   'anonymous'     -> girişsiz, login ekranı gösterilir
 */
export type AuthStatus = 'unknown' | 'authenticated' | 'anonymous'

interface AuthState {
  status: AuthStatus
  user: AuthUser | null
  /** Giriş gerekli mi? Backend LOGIN_ENABLED=false ise false kalır. */
  loginRequired: boolean

  /** Açılışta çağrılır: sunucuya "ben kimim" diye sorar. */
  bootstrap: () => Promise<void>
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

export const useAuthStore = create<AuthState>((set) => ({
  status: 'unknown',
  user: null,
  loginRequired: true,

  bootstrap: async () => {
    // 1) Backend giriş istiyor mu? LOGIN_ENABLED=false ise giriş ekranı hiç
    //    gösterilmez — feature flag'in uçtan uca çalışması buna bağlı.
    try {
      const { data: status } = await authApi.status()
      if (!status.login_required) {
        set({ status: 'authenticated', user: null, loginRequired: false })
        return
      }
      set({ loginRequired: true })
    } catch {
      // Durum ucu okunamadı (eski backend veya ağ hatası). Giriş gerekli
      // varsayılır — fail-closed.
      set({ loginRequired: true })
    }

    // 2) Oturum var mı?
    try {
      const { data } = await authApi.me()
      set({ status: 'authenticated', user: data })
    } catch {
      // 401 girişsiz demek; 503/ağ hatasında da giriş ekranı gösterilir ki
      // kullanıcı tekrar deneyebilsin. Sessizce girişli SAYMAYIZ.
      set({ status: 'anonymous', user: null })
    }
  },

  login: async (email, password) => {
    const { data } = await authApi.login(email, password)
    set({ status: 'authenticated', user: data })
  },

  logout: async () => {
    try {
      await authApi.logout()
    } finally {
      // Sunucu hata verse bile istemci tarafında çıkış yapılmış sayılır.
      set({ status: 'anonymous', user: null })
    }
  },
}))

/**
 * Oturum düşerse (herhangi bir istek 401 dönerse) giriş ekranına al.
 *
 * api.ts bu olayı yayar; buradan dinlenir. Ters yönde import dairesel
 * bağımlılık yaratırdı.
 *
 * loginRequired false ise (backend giriş istemiyor) olay yok sayılır —
 * o durumdaki 401 başka bir sebepten gelir ve ekranı sıfırlamamalı.
 */
if (typeof window !== 'undefined') {
  window.addEventListener(UNAUTHENTICATED_EVENT, () => {
    const { status, loginRequired } = useAuthStore.getState()
    if (!loginRequired) return
    if (status === 'anonymous') return
    useAuthStore.setState({ status: 'anonymous', user: null })
  })
}
