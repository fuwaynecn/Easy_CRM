import { api } from "./client";
import type { ImportJob } from "./types";

export function downloadCustomerTemplate(): Promise<Blob> {
  return api.blob("/api/v1/imports/customers/template");
}

export function uploadCustomers(file: File): Promise<ImportJob> {
  const form = new FormData();
  form.append("kind", "customers");
  form.append("file", file);
  return api.upload<ImportJob>("/api/v1/imports", form);
}

export function getImportJob(id: number): Promise<ImportJob> {
  return api.get<ImportJob>(`/api/v1/imports/${id}`);
}

export function commitImport(id: number): Promise<ImportJob> {
  return api.post<ImportJob>(`/api/v1/imports/${id}/commit`);
}