import type { Api } from '../shared/types.js'

declare global {
  interface Window {
    patchbay: Api
  }
}

export {}
