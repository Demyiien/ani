import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  const api = env.ANI_API_URL || 'http://127.0.0.1:8000';
  const proxy = { '/api': { target: api, changeOrigin: true } };
  return { plugins: [react()], server: { proxy }, preview: { proxy } };
});
