import { useState, type FormEvent } from 'react'
import { Button, Modal, ModalActions, StatusDot } from '@singz/ui'
import { t } from '../i18n.js'
import { CloudSearch } from './CloudSearch.js'
import type { CloudStatus } from '@shared/types'

export function Cloud({ status, onStatus, onClose }: {
  status: CloudStatus | null
  onStatus: (status: CloudStatus) => void
  onClose: () => void
}): React.JSX.Element {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [visible, setVisible] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function login(event: FormEvent): Promise<void> {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    const request = window.patchbay.cloudLogin(email.trim(), password)
    setPassword('')
    setVisible(false)
    try {
      const result = await request
      onStatus(result)
      setError(result.error ?? '')
    } catch {
      setError(t('cloud.loginFailed'))
    } finally {
      setBusy(false)
    }
  }

  async function logout(): Promise<void> {
    setBusy(true)
    setError('')
    try {
      const result = await window.patchbay.cloudLogout()
      onStatus(result)
      setError(result.error ?? '')
    } catch {
      setError(t('cloud.logoutFailed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal onClose={() => { if (!busy) onClose() }} cardClassName={status?.authenticated ? 'cloud-card cloud-browser-card' : 'cloud-card'} aria-label={t('cloud.account')}>
      <span className="eyebrow">{t('cloud.yourAccount')}</span>
      <h2>Cortex Cloud</h2>
      <p className="fine">{t('cloud.intro')}</p>

      {!status && <p className="cloud-message" role="status">{t('cloud.checking')}</p>}

      {status?.authenticated ? (
        <>
          <div className="cloud-account">
            <StatusDot tone="ok" />
            <div><strong>{t('cloud.signedIn')}</strong><span>{status.email || t('cloud.account')}</span></div>
          </div>
          <CloudSearch disabled={busy} />
          {!busy && (error || status.error) && <p className="cloud-error" role="alert">{error || status.error}</p>}
          <ModalActions>
            <Button variant="primary" disabled={busy} onClick={onClose}>{t('cloud.done')}</Button>
            <span className="grow" />
            <Button variant="danger" disabled={busy} onClick={() => { void logout() }}>{busy ? t('cloud.signingOut') : t('cloud.signOut')}</Button>
          </ModalActions>
        </>
      ) : status && (
        <form className="cloud-form" onSubmit={(event) => { void login(event) }}>
          <label htmlFor="cloud-email">{t('cloud.email')}</label>
          <input id="cloud-email" type="email" autoComplete="username" required maxLength={320}
            value={email} onChange={(event) => setEmail(event.target.value)} disabled={busy} autoFocus />
          <label htmlFor="cloud-password">{t('cloud.password')}</label>
          <div className="cloud-password">
            <input id="cloud-password" type={visible ? 'text' : 'password'} autoComplete="current-password" required
              value={password} onChange={(event) => setPassword(event.target.value)} disabled={busy} />
            <button type="button" onClick={() => setVisible(!visible)} disabled={busy}
              aria-label={visible ? t('cloud.hidePassword') : t('cloud.showPassword')} aria-pressed={visible}>{visible ? t('cloud.hide') : t('cloud.show')}</button>
          </div>
          <div className="cloud-storage">
            <strong>{t('cloud.remember')}</strong>
            <p>{t('cloud.storage')}</p>
          </div>
          {!busy && (error || status.error) && <p className="cloud-error" role="alert">{error || status.error}</p>}
          {busy && <p className="cloud-message" role="status">{t('cloud.working')}</p>}
          <ModalActions>
            <Button type="submit" variant="primary" disabled={busy || !email.trim() || !password}>{busy ? t('cloud.signingIn') : t('cloud.signIn')}</Button>
            <Button type="button" disabled={busy} onClick={onClose}>{t('cloud.cancel')}</Button>
          </ModalActions>
        </form>
      )}
    </Modal>
  )
}
