import { expect, it, vi } from 'vitest';
import { ApiError, type MediaRequest } from './api';
import { AuthenticatedMediaCache } from './media';

const request: MediaRequest = {
  entity: 'generation',
  id: 'generation-1',
  variant: 'thumbnail',
};

it('caches one stable object URL until explicit invalidation', async () => {
  const create = vi.fn(() => 'blob:vanta-1');
  const revoke = vi.fn();
  vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke });
  const loader = vi.fn(async () => new Blob(['image'], { type: 'image/jpeg' }));
  const cache = new AuthenticatedMediaCache(loader);

  expect(await cache.get(request)).toBe('blob:vanta-1');
  expect(await cache.get(request)).toBe('blob:vanta-1');
  expect(loader).toHaveBeenCalledTimes(1);
  expect(revoke).not.toHaveBeenCalled();

  cache.invalidateEntity('generation', 'generation-1');
  expect(revoke).toHaveBeenCalledWith('blob:vanta-1');
});

it('falls back from a missing thumbnail to the original bytes', async () => {
  vi.stubGlobal('URL', {
    createObjectURL: vi.fn(() => 'blob:fallback'),
    revokeObjectURL: vi.fn(),
  });
  const loader = vi.fn(async (item: MediaRequest) => {
    if (item.variant === 'thumbnail') throw new ApiError('missing', 404, 'media_missing');
    return new Blob(['original'], { type: 'image/png' });
  });
  const cache = new AuthenticatedMediaCache(loader);

  expect(await cache.get(request, 'original')).toBe('blob:fallback');
  expect(loader).toHaveBeenNthCalledWith(2, { ...request, variant: 'original' });
});

it('revokes a late object URL when an in-flight request is invalidated', async () => {
  let resolveBlob!: (blob: Blob) => void;
  const loader = vi.fn(
    () =>
      new Promise<Blob>((resolve) => {
        resolveBlob = resolve;
      }),
  );
  const revoke = vi.fn();
  vi.stubGlobal('URL', {
    createObjectURL: vi.fn(() => 'blob:late'),
    revokeObjectURL: revoke,
  });
  const cache = new AuthenticatedMediaCache(loader);
  const pending = cache.get(request);

  cache.invalidate(request);
  resolveBlob(new Blob(['late'], { type: 'image/jpeg' }));

  await expect(pending).rejects.toThrow('invalidated');
  expect(revoke).toHaveBeenCalledWith('blob:late');
});
