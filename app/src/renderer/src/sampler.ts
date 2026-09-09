import { useCallback, useEffect, useRef, useState } from 'react'
import type { AudioState, RiffList, SampleState } from '@shared/types'

/**
 * The reference-riff recorder, as state.
 *
 * Owned by the view rather than by the transport that draws it, because two
 * other things read it: the input rail follows the DI level while the recorder
 * is armed or rolling, and the drawer's tools need to know a take exists. The
 * transport is just the face.
 *
 * Nothing here is on a clock — the first note starts the take and the silence
 * after it ends the take — so the service is polled only while armed or
 * recording, never at rest.
 */

const POLL_MS = 150

export type RecorderState = 'idle' | 'armed' | 'recording' | 'done'

export interface Sampler {
  audio: AudioState | null
  sample: SampleState | null
  riffs: RiffList | null
  state: RecorderState
  /** There is a take to measure with and the recorder is at rest. */
  ready: boolean
  /** Seconds of quiet left before the take ends itself; null unless recording. */
  silenceLeft: number | null
  /** The riff being fetched, while a shipped one is loading. */
  loadingRiff: string | null
  error: string | null
  arm: () => Promise<void>
  keep: () => Promise<void>
  discard: () => Promise<void>
  pick: (name: string) => Promise<void>
  /** What the footswitch does right now: arm, stop, or cancel. */
  foot: () => void
  dismiss: () => void
}

export function useSampler(live: boolean): Sampler {
  const [audio, setAudio] = useState<AudioState | null>(null)
  const [sample, setSample] = useState<SampleState | null>(null)
  const [riffs, setRiffs] = useState<RiffList | null>(null)
  const [loadingRiff, setLoadingRiff] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const poll = useRef<ReturnType<typeof setInterval> | null>(null)
  const quietSince = useRef<number | null>(null)

  useEffect(() => {
    if (!live) return
    window.patchbay.leveling
      .audio()
      .then((a) => {
        setAudio(a)
        // A riff saved in an earlier session is only a path until it is read back;
        // without this "Ready" sits over a blank waveform and zeroed facts.
        if (a.sample) {
          window.patchbay.leveling.sampleStatus().then(setSample).catch(() => undefined)
        }
      })
      .catch(() => undefined)
    // The riffs the bench brings itself: until they were reachable the only way
    // to start was to plug a guitar in, and the first riff anyone records is
    // usually not good enough to measure with.
    void window.patchbay.leveling.riffs().then(setRiffs).catch(() => undefined)
  }, [live])

  const stopPoll = (): void => {
    if (poll.current) clearInterval(poll.current)
    poll.current = null
  }
  useEffect(() => stopPoll, [])

  const keep = useCallback(async (): Promise<void> => {
    stopPoll()
    try {
      const s = await window.patchbay.leveling.sampleStop()
      setSample(s)
      if (s.error) setError(s.error)
      setAudio(await window.patchbay.leveling.audio())
      setRiffs(await window.patchbay.leveling.riffs())
    } catch (e) { setError((e as Error).message) }
  }, [])

  /* A take ends by itself — on silence or at the cap — so the button cannot be
     what learns about it. */
  const watch = useCallback((): void => {
    stopPoll()
    poll.current = setInterval(() => {
      window.patchbay.leveling
        .sampleStatus()
        .then((s) => {
          setSample(s)
          if (s.state === 'recording') {
            const quiet = (s.input_dbfs ?? -99) < s.threshold_dbfs
            if (quiet && quietSince.current === null) quietSince.current = Date.now()
            if (!quiet) quietSince.current = null
          }
          if (s.state === 'done') { stopPoll(); void keep() }
          if (s.state === 'idle') stopPoll()
        })
        .catch(() => undefined)
    }, POLL_MS)
  }, [keep])

  const arm = useCallback(async (): Promise<void> => {
    setError(null); quietSince.current = null
    try {
      setSample(await window.patchbay.leveling.sampleArm())
      watch()
    } catch (e) { setError((e as Error).message) }
  }, [watch])

  const discard = useCallback(async (): Promise<void> => {
    stopPoll(); quietSince.current = null
    try {
      await window.patchbay.leveling.sampleDiscard()
      // discard answers only {state: idle}; the take already on disk is still
      // the reference, so read its facts back or the transport says "no riff"
      setSample(await window.patchbay.leveling.sampleStatus())
    } catch (e) { setError((e as Error).message) }
  }, [])

  const pick = useCallback(async (name: string): Promise<void> => {
    setLoadingRiff(name)
    try {
      const s = await window.patchbay.leveling.useRiff(name)
      setSample(s as SampleState)
      setAudio(await window.patchbay.leveling.audio())
      setRiffs(await window.patchbay.leveling.riffs())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoadingRiff(null)
    }
  }, [])

  const state = (sample?.state ?? 'idle') as RecorderState
  const recording = state === 'recording'
  const armed = state === 'armed'
  const ready = Boolean(audio?.sample) && !recording && !armed
  const silenceLeft = recording && quietSince.current !== null
    ? (sample?.silence_seconds ?? 1.5) - (Date.now() - quietSince.current) / 1000
    : null

  const foot = useCallback((): void => {
    if (!live) return
    if (recording) void keep()
    else if (armed) void discard()
    else void arm()
  }, [live, recording, armed, keep, discard, arm])

  return {
    audio, sample, riffs, state, ready, silenceLeft, loadingRiff, error,
    arm, keep, discard, pick, foot, dismiss: () => setError(null)
  }
}
