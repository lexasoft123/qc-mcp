import './stub'
import '@fontsource-variable/bricolage-grotesque'
import '@fontsource-variable/martian-mono'
import '@singz/ui/kit.css'
import '../src/renderer/src/styles.css'
import { createRoot } from 'react-dom/client'
import { Shortcuts } from '../src/renderer/src/modals/Shortcuts'

document.body.style.background = '#12100d'
createRoot(document.getElementById('root')!).render(<Shortcuts onClose={() => {}} />)
