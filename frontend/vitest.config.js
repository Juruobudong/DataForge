import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    include: ['src/components/flow/standard/*.test.js', 'src/components/flow/inspector/*.test.js', 'src/components/flow/palette/*.test.js', 'src/components/graph/*.test.js', 'src/views/developer/__tests__/*.test.js'],
    clearMocks: true,
    restoreMocks: true,
  },
})
