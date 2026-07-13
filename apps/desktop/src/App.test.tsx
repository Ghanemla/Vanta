import '@testing-library/jest-dom/vitest';
import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { App } from './App';

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
      '/api/jobs': [],
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
