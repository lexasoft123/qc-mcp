/**
 * One at a time — joining an identical request, queueing a different one.
 *
 * `Daemon.start()` used to be the whole of connecting, and it guarded itself: a
 * second call while `state !== 'stopped'` returned at once. Turning connecting
 * into a PLAN moved the daemon to the last step, so through the launch and the
 * boot-storm wait ahead of it the daemon still read 'stopped' — and the
 * two-second poll re-entered, ran a second plan, and killed the Cortex Control
 * the first one had just launched. Nine times in four minutes.
 *
 * The distinction between joining and queueing is the whole of it. The poll
 * asks for the same thing over and over and must be given the run already in
 * flight. A person pressing Disconnect during a twenty-second connect is asking
 * for something ELSE, and handing them the connect's promise would report
 * "Disconnected" over a session that was still opening — the exact class of lie
 * this file exists to remove. Different requests wait their turn instead.
 */

export interface Flight<A extends unknown[], R> {
  (...args: A): Promise<R>
  /** Is anything running or waiting? */
  busy: () => boolean
}

export function singleFlight<A extends unknown[], R>(
  fn: (...args: A) => Promise<R>,
  /** What makes two calls "the same request". Defaults to every call being the
   *  same, which is right for a function that takes no meaningful arguments. */
  keyOf: (...args: A) => string = () => ''
): Flight<A, R> {
  let inflight: Promise<R> | null = null
  let inflightKey: string | null = null
  let queued = 0

  const wrapped = (...args: A): Promise<R> => {
    const key = keyOf(...args)
    if (inflight && inflightKey === key) return inflight

    const previous = inflight
    queued++
    // `.then` on a swallowed rejection: a failed run must not cancel the next
    // request, only delay it.
    const run: Promise<R> = (previous ?? Promise.resolve())
      .then(() => undefined, () => undefined)
      .then(() => fn(...args))
      .finally(() => {
        queued--
        if (inflight === run) { inflight = null; inflightKey = null }
      })
    inflight = run
    inflightKey = key
    return run
  }

  wrapped.busy = (): boolean => queued > 0
  return wrapped
}
