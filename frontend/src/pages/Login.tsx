import { useState, type FormEvent } from 'react'
import { LogIn, Loader2, AlertCircle } from 'lucide-react'
import { useAuthStore } from '../stores/authStore'
import './Login.css'

/**
 * Giriş ekranı. E-posta + parola.
 *
 * Parola sıfırlama akışı YOK (bilinçli): kullanıcılar sunucudaki
 * scripts/create_user.py betiğiyle açılır ve parolaları oradan sıfırlanır.
 * E-posta gönderimi gerektirmediği için bu iş kapsamına alınmadı.
 */
export default function Login() {
  const login = useAuthStore((s) => s.login)

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (busy) return

    setError(null)
    setBusy(true)
    try {
      await login(email.trim(), password)
      // Başarılıysa store'daki status değişir, App yeniden render eder.
    } catch (err) {
      const message = (err as { message?: string } | undefined)?.message
      setError(message || 'Giriş yapılamadı. Lütfen tekrar deneyin.')
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={onSubmit}>
        <div className="login-head">
          <h1 className="login-title">Digitus Engine</h1>
          <p className="login-subtitle">Devam etmek için giriş yapın</p>
        </div>

        <label className="login-field">
          <span className="login-label">E-posta</span>
          <input
            type="email"
            name="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoFocus
            disabled={busy}
            placeholder="ad@ornek.com"
          />
        </label>

        <label className="login-field">
          <span className="login-label">Parola</span>
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            disabled={busy}
            placeholder="••••••••••"
          />
        </label>

        {error && (
          <div className="login-error" role="alert">
            <AlertCircle size={16} aria-hidden="true" />
            <span>{error}</span>
          </div>
        )}

        <button className="login-submit" type="submit" disabled={busy || !email || !password}>
          {busy ? (
            <>
              <Loader2 size={16} className="login-spin" aria-hidden="true" />
              Giriş yapılıyor…
            </>
          ) : (
            <>
              <LogIn size={16} aria-hidden="true" />
              Giriş yap
            </>
          )}
        </button>
      </form>
    </div>
  )
}
