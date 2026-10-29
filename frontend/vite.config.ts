import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 项目没装 @types/node，tsconfig 的 types 只有 vite/client，所以这里就地声明配置文件
// 运行环境（Node）里必然存在的 process.env，不新增任何依赖。运行时由 Node 提供。
declare const process: { env: Record<string, string | undefined> };

// 默认值保持 8000（文档与生产契约不变）。本机 8000 端口可能被系统禁用
// （WinError 10013 绑定失败），此时用 VITE_API_PROXY 覆盖成 8080 等可用端口：
//   $env:VITE_API_PROXY = "http://127.0.0.1:8080"; npm run dev
const apiTarget = process.env.VITE_API_PROXY ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    watch: {
      // 编辑器的原子写入会先建 .xxx.tmpdir/ 临时目录再改名。不排除的话，
      // vite 的文件监听会去 watch 这些随时消失的临时文件，在 Windows 上
      // 直接以 EBUSY 让整个 dev server 退出。
      ignored: ["**/.*.tmpdir/**", "**/*.tmp"],
    },
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
});
