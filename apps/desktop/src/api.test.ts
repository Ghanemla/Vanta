import { beforeEach, expect, it, vi } from 'vitest';
import { api, configureLocalService, type LocalServiceInfo, VANTA_TOKEN_HEADER } from './api';

const service: LocalServiceInfo = {
  state: 'ready',
  phase: 'Ready',
  base_url: 'http://127.0.0.1:58123',
  launch_token: 'test-launch-token',
  sidecar_path: null,
  application_data_path: 'data',
  database_path: 'data/vanta.db',
  logs_path: 'data/logs',
  port: 58123,
  health_check_state: 'ready',
  last_process_exit_code: null,
  last_sanitized_error: null,
};

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify([]), { status: 200 })),
  );
  configureLocalService(service);
});

it('uses the dynamic local service URL and per-launch token header', async () => {
  await api.get('/characters');

  expect(fetch).toHaveBeenCalledWith(
    'http://127.0.0.1:58123/api/characters',
    expect.objectContaining({
      headers: expect.objectContaining({ [VANTA_TOKEN_HEADER]: 'test-launch-token' }),
    }),
  );
});
