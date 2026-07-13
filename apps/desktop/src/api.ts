import { isTauri } from '@tauri-apps/api/core';

let apiBase =
  import.meta.env.VITE_ORCHESTRATOR_URL ?? (import.meta.env.DEV ? '' : 'http://127.0.0.1:47831');
let launchToken: string | undefined;
export const VANTA_TOKEN_HEADER = 'X-Vanta-Token';

export type LocalServiceInfo = {
  state: string;
  phase: string;
  base_url: string | null;
  launch_token: string | null;
  sidecar_path: string | null;
  application_data_path: string;
  database_path: string;
  logs_path: string;
  port: number | null;
  health_check_state: string;
  last_process_exit_code: number | null;
  last_sanitized_error: string | null;
};

export function configureLocalService(service: LocalServiceInfo): void {
  if (service.state === 'ready' && service.base_url && service.launch_token) {
    apiBase = service.base_url;
    launchToken = service.launch_token;
  }
}

export async function getLocalServiceInfo(): Promise<LocalServiceInfo> {
  if (!isTauri()) {
    return {
      state: 'ready',
      phase: 'Ready',
      base_url: apiBase,
      launch_token: null,
      sidecar_path: null,
      application_data_path: 'Development runtime',
      database_path: 'Development runtime/vanta.db',
      logs_path: 'Development runtime/logs',
      port: null,
      health_check_state: 'ready',
      last_process_exit_code: null,
      last_sanitized_error: null,
    };
  }
  const { invoke } = await import('@tauri-apps/api/core');
  const service = await invoke<LocalServiceInfo>('service_info');
  configureLocalService(service);
  return service;
}

export async function restartLocalService(): Promise<LocalServiceInfo> {
  if (!isTauri()) return getLocalServiceInfo();
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<LocalServiceInfo>('restart_local_service');
}

export async function repairApplicationRuntime(): Promise<LocalServiceInfo> {
  if (!isTauri()) return getLocalServiceInfo();
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<LocalServiceInfo>('repair_application_runtime');
}

export async function chooseLocalModelFile(): Promise<string | null> {
  if (!isTauri()) return null;
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<string | null>('choose_local_model_file');
}

export async function exportDiagnostics(): Promise<void> {
  const response = await fetch(`${apiBase}/api/diagnostics/export`, {
    headers: launchToken ? { [VANTA_TOKEN_HEADER]: launchToken } : {},
  });
  if (!response.ok)
    throw new ApiError('Vanta could not export local diagnostics.', response.status);
  const link = document.createElement('a');
  link.href = URL.createObjectURL(await response.blob());
  link.download = 'vanta-diagnostics.zip';
  link.click();
  URL.revokeObjectURL(link.href);
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}/api${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(launchToken ? { [VANTA_TOKEN_HEADER]: launchToken } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({ detail: response.statusText }))) as {
      detail?: string;
    };
    throw new ApiError(
      body.detail ?? 'The local orchestrator could not complete this action.',
      response.status,
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(
      path,
      body === undefined ? { method: 'POST' } : { method: 'POST', body: JSON.stringify(body) },
    ),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(body) }),
  delete: (path: string) => request<void>(path, { method: 'DELETE' }),
  imageUrl: async (generationId: string): Promise<string> => {
    const response = await fetch(`${apiBase}/api/generations/${generationId}/image`, {
      headers: launchToken ? { [VANTA_TOKEN_HEADER]: launchToken } : {},
    });
    if (!response.ok) throw new ApiError('Vanta could not load this local image.', response.status);
    return URL.createObjectURL(await response.blob());
  },
};
