import { useEffect, useMemo, useState } from 'react'
import { Badge, Button, Modal, ModalActions } from '@singz/ui'
import type { BenchSlot, PresetFolder, PresetRef } from '@shared/types'
import { slotId } from '../derive.js'
import { t, tn } from '../i18n.js'

/**
 * Pick presets to park on the bench.
 *
 * The listing comes from the device's own DIRECTORY. Reading it live costs
 * about twelve seconds of streamed `File` messages, so the bench prefers the
 * on-disk snapshot and only pulls a fresh one when asked — the first run on a
 * machine with no snapshot is the slow one.
 */
export function PresetPicker({
  onClose, onAdd
}: {
  onClose: () => void
  onAdd: (slots: BenchSlot[]) => void
}): React.JSX.Element {
  const [folders, setFolders] = useState<PresetFolder[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<BenchSlot[]>([])

  const load = (refresh: boolean): void => {
    setBusy(true)
    setError(null)
    window.patchbay.leveling
      .folders(refresh)
      .then(setFolders)
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false))
  }

  useEffect(() => { load(false) }, [])

  const hits = useMemo(() => {
    if (!folders) return []
    const q = query.trim().toLowerCase()
    return folders
      .map((f) => ({
        ...f,
        presets: q
          ? f.presets.filter((p) => p.name.toLowerCase().includes(q))
          : f.presets
      }))
      .filter((f) => f.presets.length > 0 && (!q || true))
      .slice(0, q ? 40 : 400)
  }, [folders, query])

  const slotFor = (folder: PresetFolder, p: PresetRef): BenchSlot => ({
    folderKey: folder.key, position: p.position, name: p.name,
    cloudId: p.cloudId, scene: null
  })

  const has = (id: string): boolean => picked.some((p) => slotId(p) === id)

  const toggle = (folder: PresetFolder, p: PresetRef): void => {
    const slot = slotFor(folder, p)
    const id = slotId(slot)
    setPicked((cur) => (has(id) ? cur.filter((x) => slotId(x) !== id) : [...cur, slot]))
  }

  return (
    <Modal onClose={onClose} cardClassName="picker-card" aria-label={t('pick.title')}>
      <h2>{t('pick.title')}</h2>
      <p className="fine">{t('pick.intro')}</p>

      <div className="pick-tools">
        <input
          className="pick-search"
          placeholder={t('pick.search')}
          value={query}
          autoFocus
          onChange={(e) => setQuery(e.target.value)}
        />
        <Button size="sm" onClick={() => load(true)} disabled={busy}>
          {busy ? t('pick.reading') : t('pick.reread')}
        </Button>
      </div>

      {error && <div className="strip bad"><span className="grow">{error}</span></div>}

      <div className="pick-list">
        {!folders && !error && <div className="fine">{t('pick.readingDir')}</div>}
        {folders && hits.length === 0 && (
          <div className="fine">{t('pick.noMatch', { q: query })}</div>
        )}
        {hits.map((f) => (
          <section key={f.key}>
            <h3>
              {f.name}
              {f.isFactory && <Badge className="off">{t('pick.factory')}</Badge>}
            </h3>
            <div className="pick-grid">
              {f.presets.map((p) => (
                <button
                  key={slotId(slotFor(f, p))}
                  className={has(slotId(slotFor(f, p))) ? 'pick on' : 'pick'}
                  onClick={() => toggle(f, p)}
                >
                  {/* Downloads have no meaningful slot number — only a cloud id */}
                  {!f.isDownloads && <span className="mono">{p.position + 1}</span>}
                  {p.name}
                </button>
              ))}
            </div>
          </section>
        ))}
      </div>

      <ModalActions>
        <Button onClick={onClose}>{t('pick.cancel')}</Button>
        <Button
          variant="primary"
          disabled={picked.length === 0}
          onClick={() => onAdd(picked)}
        >
          {picked.length ? tn('pick.add', picked.length) : t('pick.addNone')}
        </Button>
      </ModalActions>
    </Modal>
  )
}
