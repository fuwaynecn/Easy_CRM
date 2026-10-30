import { api } from "./client";
import type { HomeSummary } from "./types";

/** 首页三块一次取回：不拆成三次列表请求，避免首屏 3 个 RTT。 */
export function getHome() {
  return api.get<HomeSummary>("/api/v1/home");
}