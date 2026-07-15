import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { App } from './App';

let jobsFixture: unknown[] = [];
let installationJobsFixture: unknown[] = [];
let diagnosticsFailure = false;

vi.stubGlobal(
  'fetch',
  vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === '/api/engine/diagnostics' && diagnosticsFailure) {
      return new Response(JSON.stringify({ detail: 'Diagnostics backend unavailable' }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    const fixtures: Record<string, unknown> = {
      '/api/characters': [],
      '/api/presets': [],
      '/api/recipes': [],
      '/api/gallery': [],
      '/api/engine/components': [],
      '/api/loras': [],
      '/api/jobs': jobsFixture,
      '/api/installation-jobs': installationJobsFixture,
      '/api/poses': [],
      '/api/motion-assets': [],
      '/api/training/datasets': [],
      '/api/training/runs': [],
      '/api/engine/model-packs': {
        hardware: { gpu_name: 'RTX 4070 Super', vram_gb: 12, ram_gb: 32, free_disk_gb: 100 },
        packs: [],
      },
      '/api/settings': { values: {}, paths: { data: 'data', database: 'db', models: 'models' } },
      '/api/engine/diagnostics': {
        summary: 'Setup has not started',
        messages: ['Diagnostics are available before setup'],
        raw_logs: [],
      },
    };
    return new Response(JSON.stringify(fixtures[path]), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }),
);

beforeEach(() => {
  jobsFixture = [];
  installationJobsFixture = [];
  diagnosticsFailure = false;
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
  expect(screen.getByRole('button', { name: /Choose storage location/i })).toBeInTheDocument();
  expect(screen.queryByText(/Choose F:/i)).not.toBeInTheDocument();
  expect(screen.getByText('No cloud connection')).toBeInTheDocument();
});

it('opens diagnostics visibly before setup is complete', async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: 'Models & Engine' }));
  const diagnosticsButtons = screen.getAllByRole('button', { name: 'Diagnostics' });
  fireEvent.click(diagnosticsButtons.at(-1)!);
  expect(await screen.findByText('Setup has not started')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Copy diagnostics/i })).toBeInTheDocument();
});

it('shows a visible diagnostics error before setup is complete', async () => {
  diagnosticsFailure = true;
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: 'Models & Engine' }));
  const diagnosticsButtons = screen.getAllByRole('button', { name: 'Diagnostics' });
  fireEvent.click(diagnosticsButtons.at(-1)!);
  expect(await screen.findByRole('alert')).toHaveTextContent('Diagnostics backend unavailable');
  expect(screen.getByRole('button', { name: 'Retry diagnostics' })).toBeInTheDocument();
});

it('shows installation downloads separately from generations with real bytes', async () => {
  installationJobsFixture = [
    {
      id: 'install-engine',
      component_id: 'workflow-runtime',
      operation: 'install',
      state: 'downloading',
      stage: 'Downloading Local Image Engine',
      summary: 'Streaming the reviewed archive',
      source: 'https://github.com/approved/archive.7z',
      destination: 'D:\\VantaData\\engine\\archive.7z',
      partial_path: 'D:\\VantaData\\engine\\archive.7z.partial',
      downloaded_bytes: 1_048_576,
      total_bytes: 2_097_152,
      percentage: 50,
      speed_bytes_per_second: 524_288,
      elapsed_seconds: 2,
      eta_seconds: 2,
      resumable: true,
      cancellation_requested: false,
      paused_requested: false,
      retry_count: 0,
      created_at: '2026-07-15T12:00:00Z',
      started_at: '2026-07-15T12:00:00Z',
      updated_at: '2026-07-15T12:00:02Z',
    },
  ];
  render(<App />);
  fireEvent.click(await screen.findByRole('button', { name: /Jobs/i }));
  expect(screen.getByText('Downloads & setup')).toBeInTheDocument();
  expect(screen.getByText('Generations')).toBeInTheDocument();
  expect(screen.getByText('1.0 MB / 2.0 MB')).toBeInTheDocument();
  expect(screen.getByText('No generation jobs')).toBeInTheDocument();
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
