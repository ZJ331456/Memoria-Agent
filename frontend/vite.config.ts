import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({
  plugins:[react()],
  resolve:{alias:{'@':new URL('./src',import.meta.url).pathname}},
  server:{proxy:{'/api':'http://127.0.0.1:2237'}},
  build:{rollupOptions:{output:{manualChunks(id){
    if(id.includes('node_modules/motion')||id.includes('node_modules/framer-motion'))return 'motion'
    if(id.includes('node_modules/@base-ui')||id.includes('node_modules/@shadcn'))return 'ui-primitives'
    if(id.includes('node_modules/react')||id.includes('node_modules/react-dom'))return 'react-vendor'
  }}}}
})
