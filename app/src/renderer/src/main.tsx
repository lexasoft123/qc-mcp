import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { applyPlatformClasses } from '@singz/ui'
import '@fontsource-variable/bricolage-grotesque'
import '@fontsource-variable/martian-mono'
import '@singz/ui/kit.css'
import './styles.css'
import { App } from './App.js'
import { initStore } from './store.js'

// Must run BEFORE the first render: App reads body.win during render to decide
// whether to mount the kit's window buttons.
applyPlatformClasses()
initStore()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>
)
