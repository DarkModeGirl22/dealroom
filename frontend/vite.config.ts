import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Builds the dashboard into the Flask app's static folder, under fixed names the template loads.
export default defineConfig({
  plugins: [react()],
  define: { 'process.env.NODE_ENV': '"production"' },
  build: {
    outDir: '../dealroom/static/dash',
    emptyOutDir: true,
    cssCodeSplit: false,
    chunkSizeWarningLimit: 8000,
    rollupOptions: {
      input: 'src/main.tsx',
      output: { entryFileNames: 'dash.js', assetFileNames: 'dash[extname]', inlineDynamicImports: true },
    },
  },
});
