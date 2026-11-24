import { useState } from 'react'
import { Button, SegmentedControl } from '@singz/ui'
import type { CloudCategory, CloudPresetDetails, CloudProduct } from '@shared/types'
import { t } from '../i18n.js'

const words = (label: string | undefined): string | undefined => label?.replaceAll('_', ' ')

function ProductDetails({ product, category }: { product: CloudProduct; category: CloudCategory }): React.JSX.Element {
  const version = product.version?.replace(/^v/i, '')
  const isPreset = category === 'presets'
  const [details, setDetails] = useState<CloudPresetDetails | null>(null)
  const [loadingDetails, setLoadingDetails] = useState(false)
  async function loadPreset(): Promise<void> {
    if (!isPreset || loadingDetails) return
    setLoadingDetails(true)
    try {
      setDetails(await window.patchbay.cloudPresetDetails(product.productId))
    } catch {
      setDetails({ error: t('cloud.detailFailed') })
    } finally {
      setLoadingDetails(false)
    }
  }
  const presetDetails = details && !('error' in details) ? details : null
  const compatibility = presetDetails?.compatibility.map((value) =>
    value === 'quad' ? 'Quad Cortex' : value === 'native' ? 'Cortex Native' : value)
  const summary = [product.authorUsername, ...(isPreset
    ? [product.creatorType === 'quad' ? 'Quad Cortex' : words(product.creatorType)]
    : [version && 'V' + version, words(product.deviceType)])]
  const facts = [
    [t('cloud.captureVersion'), version ? 'V' + version : undefined],
    [t('cloud.captureInstrument'), words(product.instrumentType)],
    [t('cloud.captureGear'), words(product.deviceType)],
    [t('cloud.captureGain'), product.gainType],
    [t('cloud.captureDate'), product.createdAt?.slice(0, 10)],
    [t('cloud.captureDownloads'), product.downloads],
    [t('cloud.captureTags'), product.tags?.join(', ')],
    [t('cloud.captureId'), product.productId],
    [t('cloud.captureHash'), product.hash],
    [t('cloud.creatorVersion'), isPreset ? product.creatorVersion : undefined],
    [t('cloud.compatibility'), compatibility?.join(', ')],
    [t('cloud.presetBlocks'), presetDetails?.blocks.join(' · ')],
    [t('cloud.presetScenes'), presetDetails?.scenes.join(' · ')]
  ]
  return (
    <details className="cloud-capture" onToggle={(event) => {
      if (event.currentTarget.open && !details) void loadPreset()
    }}>
      <summary>
        <span className="cloud-capture-name">{product.name}</span>
        <span className="cloud-capture-summary">{summary.filter(Boolean).join(' · ')}</span>
      </summary>
      {product.description && <p className="cloud-capture-description">{product.description}</p>}
      <dl className="cloud-capture-facts">
        {facts.filter(([, value]) => value !== undefined && value !== '').map(([label, value]) => (
          <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
        ))}
      </dl>
      {loadingDetails && <p className="cloud-message" role="status">{t('cloud.detailLoading')}</p>}
      {details && 'error' in details && !loadingDetails && (
        <>
          <p className="cloud-error" role="alert">{details.error}</p>
          <Button size="sm" onClick={() => { void loadPreset() }}>{t('cloud.retryDetails')}</Button>
        </>
      )}
    </details>
  )
}

export function CloudSearch({ disabled }: { disabled: boolean }): React.JSX.Element {
  const [category, setCategory] = useState<CloudCategory>('captures')
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [results, setResults] = useState<{
    query: string
    page: number
    products: CloudProduct[]
    hasNext: boolean
  } | null>(null)

  async function search(text: string, page: number): Promise<void> {
    if (!text.trim() || loading || disabled) return
    setLoading(true)
    setError('')
    try {
      const result = await window.patchbay.cloudSearch(category, text.trim(), page)
      if ('error' in result) {
        setError(result.error)
        return
      }
      setResults({ query: text.trim(), page, ...result })
    } catch {
      setError(t('cloud.searchFailed'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="cloud-capture-search" aria-labelledby="cloud-capture-title">
      <h3 id="cloud-capture-title">{t('cloud.productSearch')}</h3>
      <SegmentedControl<CloudCategory> aria-label={t('cloud.category')} value={category}
        options={[{ value: 'captures', label: t('cloud.captures') }, { value: 'presets', label: t('cloud.presets') }]}
        disabled={disabled || loading} onChange={(next) => {
          if (next === category) return
          setCategory(next)
          setResults(null)
          setError('')
        }} />
      <form className="cloud-form cloud-search-form" onSubmit={(event) => {
        event.preventDefault()
        void search(query, 0)
      }}>
        <label className="cloud-search-label" htmlFor="cloud-capture-query">{t('cloud.captureQuery')}</label>
        <input id="cloud-capture-query" type="search" value={query} maxLength={120} required
          placeholder={t('cloud.capturePlaceholder')} disabled={disabled}
          onChange={(event) => setQuery(event.target.value)} />
        <Button type="submit" variant="primary" disabled={disabled || loading || !query.trim()}>{t('cloud.search')}</Button>
      </form>
      {loading && <p className="cloud-message" role="status">{t('cloud.searching')}</p>}
      {error && <p className="cloud-error" role="alert">{error}</p>}
      {!loading && !error && results && (
        <>
          {results.products.length ? (
            <div className="cloud-capture-results" key={results.query + ':' + results.page}>
              {results.products.map((product) => <ProductDetails key={product.productId} product={product} category={category} />)}
            </div>
          ) : <p className="cloud-message" role="status">{t('cloud.searchEmpty')}</p>}
          {(results.page > 0 || results.hasNext) && (
            <div className="cloud-capture-pagination">
              <Button size="sm" disabled={!results.page || disabled}
                onClick={() => { void search(results.query, results.page - 1) }}>{t('cloud.previous')}</Button>
              <span>{t('cloud.page', { page: results.page + 1 })}</span>
              <Button size="sm" disabled={!results.hasNext || disabled}
                onClick={() => { void search(results.query, results.page + 1) }}>{t('cloud.next')}</Button>
            </div>
          )}
        </>
      )}
    </section>
  )
}
