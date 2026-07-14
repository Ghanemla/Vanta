import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { App } from './App';

let jobsFixture: unknown[] = [];

vi.stubGlobal(
  'fetch',
  vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    const fixtures: Record<string, unknown> = {
      '/api/characters': [],
      '/api/presets': [],
      '/api/recipes': [],
      '/api/gallery': [],
      '/api/engine/components': [],
      '/api/loras': [],
      '/api/jobs': jobsFixture,
      '/api/poses': [],
      '/api/motion-assets': [],
      '/api/training/datasets': [],
      '/api/training/runs': [],
      '/api/engine/model-packs': {
        hardware: { gpu_name: 'RTX 4070 Super', vram_gb: 12, ram_gb: 32, free_disk_gb: 100 },
        packs: [],
      },
      '/api/settings': { values: {}, paths: { data: 'data', database: 'db', models: 'models' } },
    };
    return new Response(JSON.stringify(fixtures[path]), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }),
);

beforeEach(() => {
  jobsFixture = [];
  window.localStorage.clear();
});
afterEach(cleanup);

it('renders the local create workspace after loading', async () => {
  render(<App />);
  expect(screen.getByRole('button', { name: 'Minimize window' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Maximize window' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Close window' })).toBeInTheDocument();
  expect(await screen.findByRole('heading', { name: 'Direct the scene.' })).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Prepare your private studio.' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Install local engine/i })).toBeInTheDocument();
  expect(screen.getByText('No cloud connection')).toBeInTheDocument();
});

it('restores a tracked generation panel until the user dismisses it', async () => {
  jobsFixture = [
    {
      id: 'job-persisted',
      status: 'completed',
      progress: 100,
      current_step: 30,
      total_steps: 30,
      elapsed_seconds: 48,
      eta_seconds: 0,
      result_generation_id: 'generation-result',
      model_alias: 'photoreal_balanced',
      model_family: 'SDXL',
      output_width: 832,
      output_height: 1216,
      progress_determinate: true,
    },
  ];
  window.localStorage.setItem('vanta.active-generation-job', 'job-persisted');

  render(<App />);

  expect(await screen.findByRole('heading', { name: 'Completed' })).toBeInTheDocument();
  expect(screen.getByText('30 / 30')).toBeInTheDocument();
  expect(
    within(screen.getByLabelText('Local generation progress')).getByText('SDXL', { exact: false }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
  await waitFor(() => expect(screen.queryByLabelText('Local generation progress')).toBeNull());
  expect(window.localStorage.getItem('vanta.active-generation-job')).toBeNull();
});
