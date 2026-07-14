import {
  useEffect,
  useMemo,
  useState,
  type ImgHTMLAttributes,
  type VideoHTMLAttributes,
} from 'react';
import { ApiError, api, type MediaRequest } from './api';

type CacheEntry = {
  url?: string;
  promise?: Promise<string> | undefined;
  touched: number;
  cancelled?: boolean;
};

type MediaLoader = (request: MediaRequest) => Promise<Blob>;

export class AuthenticatedMediaCache {
  private readonly entries = new Map<string, CacheEntry>();
  private tick = 0;

  constructor(
    private readonly loader: MediaLoader = api.mediaBlob,
    private readonly maxEntries = 192,
  ) {}

  key(request: MediaRequest): string {
    return `${request.entity}:${request.id}:${request.variant}`;
  }

  async get(request: MediaRequest, fallbackVariant?: string): Promise<string> {
    const key = this.key(request);
    const existing = this.entries.get(key);
    if (existing?.url) {
      existing.touched = ++this.tick;
      return existing.url;
    }
    if (existing?.promise) return existing.promise;
    const entry: CacheEntry = { touched: ++this.tick };
    entry.promise = this.load(request, fallbackVariant)
      .then((url) => {
        if (entry.cancelled || this.entries.get(key) !== entry) {
          URL.revokeObjectURL(url);
          throw new Error('Media request was invalidated.');
        }
        entry.url = url;
        entry.promise = undefined;
        entry.touched = ++this.tick;
        this.evict();
        return url;
      })
      .catch((error: unknown) => {
        if (this.entries.get(key) === entry) this.entries.delete(key);
        throw error;
      });
    this.entries.set(key, entry);
    return entry.promise;
  }

  private async load(request: MediaRequest, fallbackVariant?: string): Promise<string> {
    try {
      return URL.createObjectURL(await this.loader(request));
    } catch (error) {
      if (fallbackVariant && error instanceof ApiError && error.status === 404) {
        return URL.createObjectURL(await this.loader({ ...request, variant: fallbackVariant }));
      }
      throw error;
    }
  }

  invalidate(request: MediaRequest): void {
    const key = this.key(request);
    const entry = this.entries.get(key);
    if (entry) entry.cancelled = true;
    if (entry?.url) URL.revokeObjectURL(entry.url);
    this.entries.delete(key);
  }

  invalidateEntity(entity: MediaRequest['entity'], id: string): void {
    const prefix = `${entity}:${id}:`;
    for (const [key, entry] of this.entries) {
      if (!key.startsWith(prefix)) continue;
      entry.cancelled = true;
      if (entry.url) URL.revokeObjectURL(entry.url);
      this.entries.delete(key);
    }
  }

  clear(): void {
    for (const entry of this.entries.values()) {
      entry.cancelled = true;
      if (entry.url) URL.revokeObjectURL(entry.url);
    }
    this.entries.clear();
  }

  private evict(): void {
    while (this.entries.size > this.maxEntries) {
      const candidate = [...this.entries.entries()]
        .filter(([, entry]) => !entry.promise)
        .sort((left, right) => left[1].touched - right[1].touched)[0];
      if (!candidate) return;
      const [key, entry] = candidate;
      if (entry.url) URL.revokeObjectURL(entry.url);
      this.entries.delete(key);
    }
  }
}

export const mediaCache = new AuthenticatedMediaCache();

if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', () => mediaCache.clear(), { once: true });
}

type MediaStatus = 'loading' | 'ready' | 'missing' | 'unauthorized' | 'decode-failed' | 'failed';

function statusFor(error: unknown): MediaStatus {
  if (error instanceof ApiError && error.status === 404) return 'missing';
  if (error instanceof ApiError && error.status === 401) return 'unauthorized';
  return 'failed';
}

function messageFor(status: MediaStatus): string {
  if (status === 'missing') return 'Local file missing';
  if (status === 'unauthorized') return 'Local service changed';
  if (status === 'decode-failed') return 'Preview could not be decoded';
  if (status === 'failed') return 'Preview unavailable';
  return 'Loading local media';
}

function useAuthenticatedMedia(request: MediaRequest, fallbackVariant?: string) {
  const stableRequest = useMemo(() => request, [request.entity, request.id, request.variant]);
  const [version, setVersion] = useState(0);
  const [state, setState] = useState<{ status: MediaStatus; url: string }>({
    status: 'loading',
    url: '',
  });

  useEffect(() => {
    let mounted = true;
    setState({ status: 'loading', url: '' });
    void mediaCache
      .get(stableRequest, fallbackVariant)
      .then((url) => {
        if (mounted) setState({ status: 'ready', url });
      })
      .catch((error: unknown) => {
        if (mounted) setState({ status: statusFor(error), url: '' });
      });
    return () => {
      mounted = false;
    };
  }, [fallbackVariant, stableRequest, version]);

  const retry = () => {
    mediaCache.invalidate(stableRequest);
    setVersion((current) => current + 1);
  };
  const decodingFailed = () => {
    mediaCache.invalidate(stableRequest);
    setState({ status: 'decode-failed', url: '' });
  };
  return { ...state, retry, decodingFailed };
}

function MediaState({
  status,
  onRetry,
  className,
}: {
  status: MediaStatus;
  onRetry: () => void;
  className?: string | undefined;
}) {
  const recoverable = status !== 'loading';
  return (
    <div className={`local-media-state ${className ?? ''}`} role={recoverable ? 'alert' : 'status'}>
      <span>{messageFor(status)}</span>
      {recoverable && (
        <button type="button" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

type AuthenticatedImageProps = Omit<ImgHTMLAttributes<HTMLImageElement>, 'src'> & {
  media: MediaRequest;
  fallbackVariant?: string | undefined;
  placeholderClassName?: string | undefined;
};

export function AuthenticatedImage({
  media,
  fallbackVariant,
  placeholderClassName,
  alt,
  ...props
}: AuthenticatedImageProps) {
  const state = useAuthenticatedMedia(media, fallbackVariant);
  if (state.status !== 'ready') {
    return (
      <MediaState status={state.status} onRetry={state.retry} className={placeholderClassName} />
    );
  }
  return <img {...props} src={state.url} alt={alt} onError={state.decodingFailed} />;
}

type AuthenticatedVideoProps = Omit<VideoHTMLAttributes<HTMLVideoElement>, 'src'> & {
  media: MediaRequest;
  placeholderClassName?: string | undefined;
};

export function AuthenticatedVideo({
  media,
  placeholderClassName,
  ...props
}: AuthenticatedVideoProps) {
  const state = useAuthenticatedMedia(media);
  if (state.status !== 'ready') {
    return (
      <MediaState status={state.status} onRetry={state.retry} className={placeholderClassName} />
    );
  }
  return <video {...props} src={state.url} onError={state.decodingFailed} />;
}
