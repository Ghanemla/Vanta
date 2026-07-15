import { isTauri } from '@tauri-apps/api/core';

let apiBase =
  import.meta.env.VITE_ORCHESTRATOR_URL ?? (import.meta.env.DEV ? '' : 'http://127.0.0.1:47831');
let launchToken: string | undefined;
export const VANTA_TOKEN_HEADER = 'X-Vanta-Token';

export type MediaEntity =
  | 'generation'
  | 'pose'
  | 'training-image'
  | 'training-validation'
  | 'character-reference'
  | 'motion';

export type MediaRequest = {
  entity: MediaEntity;
  id: string;
  variant: string;
};

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

export type StorageInfo = {
  current_root: string;
  default_root: string;
  bootstrap_config: string;
  current_bytes: number;
  current_files: number;
  destination_free_bytes: number;
  redirected_target: string | null;
  operation: string;
  phase: string;
  destination: string | null;
  current_file: string | null;
  copied_bytes: number;
  total_bytes: number;
  copied_files: number;
  total_files: number;
  elapsed_seconds: number;
  eta_seconds: number | null;
  can_cancel: boolean;
  last_error: string | null;
  previous_root: string | null;
  default_export_folder: string | null;
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

async function chooseLocalFile(command: string): Promise<string | null> {
  if (!isTauri()) return null;
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<string | null>(command);
}

export const chooseLocalImageFile = () => chooseLocalFile('choose_local_image_file');
export async function chooseLocalTrainingImages(): Promise<string[]> {
  if (!isTauri()) return [];
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<string[]>('choose_local_training_images');
}
export const chooseLocalVideoFile = () => chooseLocalFile('choose_local_video_file');
export const chooseLocalLoraFile = () => chooseLocalFile('choose_local_lora_file');
export const chooseLocalUpscalerFile = () => chooseLocalFile('choose_local_upscaler_file');

export async function openLocalPath(kind: 'data' | 'models' | 'logs' | 'database'): Promise<void> {
  if (!isTauri()) return;
  const { invoke } = await import('@tauri-apps/api/core');
  await invoke('open_local_path', { kind });
}

async function invokeDesktop<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  if (!isTauri()) throw new Error('This action is available in the Vanta desktop app.');
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<T>(command, args);
}

export const getStorageInfo = () =>
  isTauri()
    ? invokeDesktop<StorageInfo>('storage_info')
    : Promise.resolve({
        current_root: 'F:\\VantaData',
        default_root: 'F:\\VantaData',
        bootstrap_config: 'Development runtime',
        current_bytes: 0,
        current_files: 0,
        destination_free_bytes: 1_000_000_000_000,
        redirected_target: null,
        operation: 'idle',
        phase: 'Development storage ready',
        destination: null,
        current_file: null,
        copied_bytes: 0,
        total_bytes: 0,
        copied_files: 0,
        total_files: 0,
        elapsed_seconds: 0,
        eta_seconds: null,
        can_cancel: false,
        last_error: null,
        previous_root: null,
        default_export_folder: null,
      });
export const chooseStorageLocation = () => invokeDesktop<string | null>('choose_storage_location');
export const startStorageMove = (destination: string) =>
  invokeDesktop<StorageInfo>('start_storage_move', { destination });
export const cancelStorageMove = () => invokeDesktop<StorageInfo>('cancel_storage_move');
export const adoptRedirectedStorage = () => invokeDesktop<StorageInfo>('adopt_redirected_storage');
export const setDefaultExportFolder = (folder: string) =>
  invokeDesktop<StorageInfo>('set_default_export_folder', { folder });

export type ManagedMedia = { entity: MediaEntity; id: string; variant: string };
export const openManagedMedia = (media: ManagedMedia) =>
  invokeDesktop<void>('open_managed_media', media);
export const revealManagedMedia = (media: ManagedMedia) =>
  invokeDesktop<void>('reveal_managed_media', media);
export const saveManagedMediaCopy = (media: ManagedMedia) =>
  invokeDesktop<string>('save_managed_media_copy', media);
export const copyManagedMediaPath = (media: ManagedMedia) =>
  invokeDesktop<void>('copy_managed_media_path', media);

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
    readonly code?: string,
  ) {
    super(message);
  }
}

async function refreshServiceConnection(): Promise<void> {
  if (!isTauri()) return;
  const { invoke } = await import('@tauri-apps/api/core');
  configureLocalService(await invoke<LocalServiceInfo>('service_info'));
}

async function fetchMedia(requested: MediaRequest): Promise<Blob> {
  const path = `/api/media/${encodeURIComponent(requested.entity)}/${encodeURIComponent(requested.id)}/${encodeURIComponent(requested.variant)}`;
  let response: Response | undefined;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      response = await fetch(`${apiBase}${path}`, {
        headers: launchToken ? { [VANTA_TOKEN_HEADER]: launchToken } : {},
      });
    } catch (error) {
      if (attempt === 0) {
        await refreshServiceConnection();
        continue;
      }
      throw new ApiError(
        error instanceof Error ? error.message : 'The local media service is unavailable.',
        0,
        'media_service_unavailable',
      );
    }
    if (response.status === 401 && attempt === 0) {
      await refreshServiceConnection();
      continue;
    }
    break;
  }
  if (!response?.ok) {
    const body = (await response?.json().catch(() => undefined)) as
      { detail?: string | { code?: string; message?: string } } | undefined;
    const detail = body?.detail;
    const message =
      typeof detail === 'string'
        ? detail
        : (detail?.message ?? 'Vanta could not load this local media file.');
    throw new ApiError(
      message,
      response?.status ?? 0,
      typeof detail === 'object' ? detail.code : undefined,
    );
  }
  const contentType = response.headers.get('Content-Type')?.split(';')[0]?.trim();
  if (!contentType || (!contentType.startsWith('image/') && !contentType.startsWith('video/'))) {
    throw new ApiError(
      'The local service returned an invalid media type.',
      502,
      'media_mime_invalid',
    );
  }
  const blob = await response.blob();
  if (!blob.size) {
    throw new ApiError('The local media file is empty.', 502, 'media_empty');
  }
  return blob.type ? blob : blob.slice(0, blob.size, contentType);
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
  delete: <T = void>(path: string) => request<T>(path, { method: 'DELETE' }),
  mediaBlob: fetchMedia,
};
