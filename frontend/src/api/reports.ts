/**
 * 报表接口（P5 Task 1–7 的七个只读端点）。
 *
 * 全部 GET，走 `api.get`：区间由后端 `resolve_period` 统一规整（缺省、边界、
 * 上海时区），前端只负责把用户选的日期原样传过去，不做任何日期换算。
 */

import { api } from "./client";
import type {
  ActivityReport,
  CompetitorReport,
  CycleReport,
  FunnelReport,
  LostReasonReport,
  PerformanceReport,
  WinRateReport,
} from "./types";

function query(from: string, to: string): string {
  return `?date_from=${encodeURIComponent(from)}&date_to=${encodeURIComponent(to)}`;
}

export function getFunnel() {
  return api.get<FunnelReport>("/api/v1/reports/funnel");
}

export function getWinRate(from: string, to: string) {
  return api.get<WinRateReport>(`/api/v1/reports/win-rate${query(from, to)}`);
}

export function getCycle(from: string, to: string) {
  return api.get<CycleReport>(`/api/v1/reports/cycle${query(from, to)}`);
}

export function getPerformance(from: string, to: string) {
  return api.get<PerformanceReport>(
    `/api/v1/reports/performance${query(from, to)}`,
  );
}

export function getActivities(from: string, to: string, granularity: "day" | "week") {
  return api.get<ActivityReport>(
    `/api/v1/reports/activities${query(from, to)}&granularity=${granularity}`,
  );
}

export function getLostReasons(from: string, to: string) {
  return api.get<LostReasonReport>(
    `/api/v1/reports/lost-reasons${query(from, to)}`,
  );
}

export function getCompetitors(from: string, to: string) {
  return api.get<CompetitorReport>(
    `/api/v1/reports/competitors${query(from, to)}`,
  );
}