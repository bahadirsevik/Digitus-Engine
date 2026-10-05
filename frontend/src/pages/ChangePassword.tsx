import { useState, type FormEvent } from 'react'
import { KeyRound, Loader2, AlertCircle } from 'lucide-react'
import { useAuthStore } from '../stores/authStore'
import { authApi } from '../services/api'
import './Login.css'

/**
 * Zorunlu parola değiştirme ekranı.
 *
 * Geçici parolayla açılan kullanıcı (must_change_password) parolasını
 * değiştirmeden uygulamayı göremez. Atlanamaz — çıkış dışında bir yol yok.
 *
 * Backend tarafı da bağımsız olarak kapalıdır: require_login, zorunluluk
 * duran kullanıcıya /api/v1/* uçlarında 403 döner. Yani bu ekranı atlatmak
 * veriye erişim sağlamaz.
 */
export default function ChangePassword() {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const markPasswordChanged = useAuthStore((s) => s.markPasswordChanged)

  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (busy) return

    if (newPassword !== repeat) {
      setError('Yeni parolalar eşleşmiyor.')
      return
    }
    if (newPassword === currentPassword) {
      setError('Yeni parola geçici parolayla aynı olamaz.')
      return
    }

    setError(null)
    setBusy(true)
    try {
      await authApi.changePassword(currentPassword, newPassword)
      // Backend parola değişiminde TÜM oturumları kapatır — bu oturum da
      // düştü. Kullanıcıyı yeni parolasıyla tekrar giriş yapmaya alıyoruz;
      // "değişti ama hiçbir şey çalışmıyor" durumu oluşmasın.
      markPasswordChanged()
      await logout()
    } catch (err) {
      const message = (err as { message?: string } | undefined)?.message
      setError(message || 'Parola değiştirilemedi.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={onSubmit}>
        <div className="login-head">
          <h1 className="login-title">Parolanızı belirleyin</h1>
          <p className="login-subtitle">
            {user?.email
              ? `${user.email} için geçici parola kullanılıyor. Devam etmek için yeni bir parola belirleyin.`
              : 'Geçici parola kullanılıyor. Devam etmek için yeni bir parola belirleyin.'}
          </p>
        </div>

        <label className="login-field">
          <span className="login-label">Geçici parola</span>
          <input
            type="password"
            autoComplete="current-password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            required
            autoFocus
            disabled={busy}
          />
        </label>

        <label className="login-field">
          <span className="login-label">Yeni parola</span>
          <input
            type="password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            required
            minLength={10}
            disabled={busy}
            placeholder="En az 10 karakter"
          />
        </label>

        <label className="login-field">
          <span className="login-label">Yeni parola (tekrar)</span>
          <input
            type="password"
            autoComplete="new-password"
            value={repeat}
            onChange={(e) => setRepeat(e.target.value)}
            required
            minLength={10}
            disabled={busy}
          />
        </label>

        {error && (
          <div className="login-error" role="alert">
            <AlertCircle size={16} aria-hidden="true" />
            <span>{error}</span>
          </div>
        )}

        <button
          className="login-submit"
          type="submit"
          disabled={busy || !currentPassword || !newPassword || !repeat}
        >
          {busy ? (
            <>
              <Loader2 size={16} className="login-spin" aria-hidden="true" />
              Kaydediliyor…
            </>
          ) : (
            <>
              <KeyRound size={16} aria-hidden="true" />
              Parolayı değiştir
            </>
          )}
        </button>

        <button
          type="button"
          className="login-secondary"
          onClick={() => void logout()}
          disabled={busy}
        >
          Çıkış yap
        </button>
      </form>
    </div>
  )
}
