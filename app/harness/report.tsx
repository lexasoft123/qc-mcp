import './stub'
import '@fontsource-variable/bricolage-grotesque'
import '@fontsource-variable/martian-mono'
import '@singz/ui/kit.css'
import '../src/renderer/src/styles.css'
import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import type { BenchSlot, ReportRow } from '../src/shared/types'
import { LevelReport } from '../src/renderer/src/components/LevelReport'

/* The report in the three states that matter: proposed, adjusted, applied. */

const SLOTS: BenchSlot[] = [
  { folderKey: 'f', position: 0, name: 'Clean Twin', cloudId: '', scene: null },
  { folderKey: 'f', position: 2, name: 'Crunch JCM', cloudId: '', scene: null },
  { folderKey: 'f', position: 6, name: 'Lead Rectifier', cloudId: '', scene: null },
  { folderKey: 'f', position: 25, name: 'Ambient Clean', cloudId: '', scene: null },
  { folderKey: 'f', position: 30, name: 'Fuzz Octave', cloudId: '', scene: null }
] as BenchSlot[]

const ROWS: Record<number, ReportRow> = {
  0: { position: 0, row: 0, measured: -17.9, true_peak: -3.1, correction_db: -0.1 },
  2: { position: 2, row: 2, measured: -27.8, true_peak: -9.4, correction_db: 9.8 },
  6: { position: 6, row: 1, measured: -33.7, true_peak: -14.2, correction_db: 15.7 },
  25: { position: 25, row: 0, measured: -23.2, true_peak: -6.6, correction_db: 5.2 },
  30: { position: 30, row: 2, measured: -15.4, true_peak: -1.2, correction_db: -2.6 }
}

function Demo({ title, applied, saved, proposals }: {
  title: string
  applied: Record<number, number>
  saved: number[]
  proposals: Record<number, number>
}): React.JSX.Element {
  const [prop, setProp] = useState(proposals)
  return (
    <div style={{ marginBottom: 30 }}>
      <div style={{
        font: "700 9.5px 'Bricolage Grotesque', system-ui", letterSpacing: '.14em',
        textTransform: 'uppercase', color: '#6b6355', marginBottom: 9
      }}>{title}</div>
      <LevelReport
        slots={SLOTS} rows={ROWS} target={-18} metric="lufs" busy={false} progress={null}
        applied={applied} saved={saved} proposals={prop}
        selected={SLOTS.map((s) => s.position)}
        onToggle={() => {}}
        onPropose={(pos, db) => setProp((p) => ({ ...p, [pos]: db }))}
        onNudge={(pos, by) => setProp((p) => ({
          ...p, [pos]: Math.round(((p[pos] ?? ROWS[pos]?.correction_db ?? 0) + by) * 10) / 10
        }))}
        onResetProposal={(pos) => setProp((p) => { const { [pos]: _g, ...r } = p; return r })}
        onMeasure={() => {}} onApply={() => {}} onSave={() => {}} onRevert={() => {}}
      />
    </div>
  )
}

document.body.style.background = '#12100d'
createRoot(document.getElementById('root')!).render(
  <div style={{ padding: 20, width: 940 }}>
    <Demo title="1 · measured — a proposal, nothing written" applied={{}} saved={[]} proposals={{}} />
    <Demo title="2 · two adjusted by hand (lead +2 louder, fuzz left alone)"
          applied={{}} saved={[]} proposals={{ 6: 17.7, 30: 0 }} />
    <Demo title="3 · applied — yellow dots: on the device, not in the presets"
          applied={{ 2: 9.8, 6: 12.0, 25: 5.2 }} saved={[]} proposals={{}} />
    <Demo title="4 · two of them saved, one still unsaved"
          applied={{ 2: 9.8, 6: 12.0, 25: 5.2 }} saved={[2, 25]} proposals={{}} />
  </div>
)
