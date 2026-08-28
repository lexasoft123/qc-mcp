import { useSyncExternalStore } from 'react'
import type { Progress, Snapshot } from '@shared/types'

// ── the snapshot the main process owns ──────────────────────────────────

let snap: Snapshot | null = null
const subs = new Set<() => void>()

export function publish(s: Snapshot): void {
  snap = s
  subs.forEach((f) => f())
}

const subscribe = (cb: () => void): (() => void) => {
  subs.add(cb)
  return () => { subs.delete(cb) }
}

export function initStore(): void {
  void window.patchbay.snapshot().then(publish)
  window.patchbay.onSnapshot(publish)
}

export const useSnapshot = (): Snapshot | null =>
  useSyncExternalStore(subscribe, () => snap)

/** Run an api call and publish whatever snapshot it returns. */
export async function act(fn: () => Promise<Snapshot>): Promise<void> {
  publish(await fn())
}

// ── toasts ──────────────────────────────────────────────────────────────

export interface Toast { text: string; bad: boolean; id: number }

let toast: Toast | null = null
let seq = 0
const toastSubs = new Set<() => void>()
let timer: ReturnType<typeof setTimeout> | null = null

export function say(text: string, bad = false): void {
  toast = { text, bad, id: ++seq }
  toastSubs.forEach((f) => f())
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => {
    toast = null
    toastSubs.forEach((f) => f())
  }, 3600)
}

export const useToast = (): Toast | null =>
  useSyncExternalStore(
    (cb) => { toastSubs.add(cb); return () => { toastSubs.delete(cb) } },
    () => toast
  )

// ── setup / build progress ──────────────────────────────────────────────

let progress: Progress | null = null
const progSubs = new Set<() => void>()

window.patchbay.onProgress((p) => {
  progress = p.finished ? null : p
  progSubs.forEach((f) => f())
  if (p.error) say(p.error, true)
})

export const useProgress = (): Progress | null =>
  useSyncExternalStore(
    (cb) => { progSubs.add(cb); return () => { progSubs.delete(cb) } },
    () => progress
  )

export function clearProgress(): void {
  progress = null
  progSubs.forEach((f) => f())
}
