/**
 * One at a time.
 *
 * `Daemon.start()` used to be the whole of connecting, and it guarded itself:
 * a second call while `state !== 'stopped'` returned immediately. Turning
 * connecting into a PLAN moved the daemon to the last step, so through the
 * launch and the boot-storm wait ahead of it the daemon still read 'stopped' —
 * and the two-second poll re-entered, ran a second plan, and killed the Cortex
 * Control the first one had just launched. It did that nine times in four
 * minutes, and each dying app handed the next one a corrupted USB report to
 * segfault on.
 *
 * A concurrent caller joins the run in flight instead of starting another.
 */
export function singleFlight<A extends unknown[], R>(
  fn: (...args: A) => Promise<R>
): ((...args: A) => Promise<R>) & { busy: () => boolean } {
  let inflight: Promise<R> | null = null
  const wrapped = (...args: A): Promise<R> => {
    if (inflight) return inflight
    const run = fn(...args).finally(() => { if (inflight === run) inflight = null })
    inflight = run
    return run
  }
  wrapped.busy = (): boolean => inflight !== null
  return wrapped
}
