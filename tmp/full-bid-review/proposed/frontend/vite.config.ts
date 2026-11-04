import path from 'node:path'
import { defineConfig, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

/** 启动日志里隐藏 Hyper-V / WSL 等虚拟网卡地址（常见 172.16/12） */
function filterVirtualNetworkUrls(): Plugin {
  return {
    name: 'filter-virtual-network-urls',
    configureServer(server) {
      const originalPrintUrls = server.printUrls.bind(server)
      server.printUrls = () => {
        const network = server.resolvedUrls?.network
        if (network) {
          server.resolvedUrls!.network = network.filter((url) => {
            try {
              const { hostname } = new URL(url)
              return !/^172\.(1[6-9]|2\d|3[0-1])\./.test(hostname)
            } catch {
              return true
            }
          })
        }
        originalPrintUrls()
      }
    },
  }
}

// 生产挂子路径时设置，例如 VITE_BASE_PATH=/weitai/ （须以 / 开头、/ 结尾）
const base = process.env.VITE_BASE_PATH || '/'

export default defineConfig({
  base,
  plugins: [vue(), tailwindcss(), filterVirtualNetworkUrls()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    host: true,
    port: 5173,
    // Chrome 偶发 ERR_CACHE_READ_FAILURE，开发态不要把模块写进磁盘缓存
    headers: {
      'Cache-Control': 'no-store',
    },
    // 允许 cpolar 等隧道域名访问（子域名会变，用前导点匹配所有子域）
    allowedHosts: ['.cpolar.top', '.cpolar.cn'],
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8100',
        changeOrigin: true,
        timeout: 0,
        proxyTimeout: 0,
        configure(proxy) {
          proxy.on('proxyReq', (proxyReq, req) => {
            const auth = req.headers.authorization
            if (typeof auth === 'string' && auth && !proxyReq.getHeader('authorization')) {
              proxyReq.setHeader('Authorization', auth)
            }
            const extra = req.headers['x-access-token']
            if (typeof extra === 'string' && extra && !proxyReq.getHeader('x-access-token')) {
              proxyReq.setHeader('X-Access-Token', extra)
            }
          })
        },
      },
    },
  },
})
