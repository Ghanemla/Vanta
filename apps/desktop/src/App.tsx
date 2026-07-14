import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from 'react';
import { getCurrentWindow } from '@tauri-apps/api/window';
import {
  Archive,
  BookOpen,
  Box,
  Check,
  ChevronRight,
  CircleUserRound,
  Copy,
  Database,
  Download,
  Edit3,
  Eraser,
  FileDown,
  FileUp,
  Film,
  FolderLock,
  Gauge,
  Grid3X3,
  Heart,
  Image,
  Info,
  Menu,
  Minus,
  Move,
  MoreHorizontal,
  Plus,
  Paintbrush,
  Play,
  RotateCcw,
  Search,
  Scan,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Star,
  Trash2,
  Upload,
  Wrench,
  X,
  Zap,
} from 'lucide-react';
import { Button, Drawer, EmptyState, Panel, StatusPill } from '@vanta/ui';
import {
  api,
  adoptRedirectedStorage,
  cancelStorageMove,
  chooseLocalImageFile,
  chooseLocalLoraFile,
  chooseLocalModelFile,
  chooseLocalUpscalerFile,
  chooseLocalVideoFile,
  chooseLocalTrainingImages,
  exportDiagnostics,
  getStorageInfo,
  getLocalServiceInfo,
  openLocalPath,
  openManagedMedia,
  repairApplicationRuntime,
  revealManagedMedia,
  restartLocalService,
  saveManagedMediaCopy,
  setDefaultExportFolder,
  startStorageMove,
  chooseStorageLocation,
  copyManagedMediaPath,
  type LocalServiceInfo,
  type StorageInfo,
} from './api';
import { AuthenticatedImage, AuthenticatedVideo, mediaCache } from './media';
import type {
  CharacterRecord,
  Diagnostics,
  EngineComponent,
  GenerationJob,
  GenerationRecord,
  LoraRecord,
  ModelPack,
  MotionAsset,
  PoseRecord,
  PresetRecord,
  RecipeRecord,
  SettingsRecord,
  TrainingDataset,
  TrainingRun,
  VideoCapabilities,
  VideoSequence,
} from './types';

type Screen =
  | 'create'
  | 'characters'
  | 'poses'
  | 'motion'
  | 'training'
  | 'presets'
  | 'gallery'
  | 'engine'
  | 'settings';
type AppData = {
  characters: CharacterRecord[];
  presets: PresetRecord[];
  recipes: RecipeRecord[];
  gallery: GenerationRecord[];
  components: EngineComponent[];
  packs: ModelPack[];
  loras: LoraRecord[];
  jobs: GenerationJob[];
  poses: PoseRecord[];
  motion: MotionAsset[];
  trainingDatasets: TrainingDataset[];
  trainingRuns: TrainingRun[];
  hardware: { gpu_name: string; vram_gb: number; ram_gb: number; free_disk_gb: number };
  settings: SettingsRecord;
};

const navItems: { id: Screen; label: string; icon: typeof Sparkles }[] = [
  { id: 'create', label: 'Create', icon: Sparkles },
  { id: 'characters', label: 'Characters', icon: CircleUserRound },
  { id: 'poses', label: 'Pose Library', icon: Image },
  { id: 'motion', label: 'Motion Library', icon: Film },
  { id: 'training', label: 'LoRA Training', icon: Database },
  { id: 'presets', label: 'Presets', icon: BookOpen },
  { id: 'gallery', label: 'Gallery', icon: Grid3X3 },
  { id: 'engine', label: 'Models & Engine', icon: Gauge },
  { id: 'settings', label: 'Settings', icon: Settings },
];

const stateLabels: Record<string, string> = {
  not_installed: 'Not installed',
  installing: 'Installing',
  ready: 'Ready',
  update_available: 'Update available',
  repair_needed: 'Repair needed',
  unsupported: 'Unsupported',
  paused: 'Paused',
  verifying: 'Verifying',
  stopped: 'Stopped',
  starting: 'Starting',
  starting_engine: 'Starting engine',
  checking_engine: 'Checking engine',
  crashed: 'Crashed',
  queued: 'Queued',
  extracting: 'Extracting motion',
  encoding: 'Encoding MP4',
  preparing: 'Preparing prompt',
  preparing_prompt: 'Preparing prompt',
  applying_loras: 'Applying LoRAs',
  applying_identity: 'Applying identity',
  applying_pose: 'Applying pose',
  loading_model: 'Loading model',
  decoding: 'Decoding',
  saving: 'Saving',
  creating_thumbnail: 'Creating thumbnail',
  finalizing_metadata: 'Finalizing metadata',
  training: 'Training',
  cancelling: 'Cancelling',
  restarting: 'Restarting',
  generating: 'Generating',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
};
const stateTone = (state: string): 'ready' | 'warning' | 'danger' | 'neutral' =>
  ['ready', 'completed'].includes(state)
    ? 'ready'
    : ['repair_needed', 'failed', 'crashed'].includes(state)
      ? 'danger'
      : [
            'installing',
            'update_available',
            'paused',
            'queued',
            'preparing',
            'training',
            'cancelling',
          ].includes(state)
        ? 'warning'
        : 'neutral';

const terminalJobStates = ['completed', 'failed', 'cancelled'];
const activeJobStorageKey = 'vanta.active-generation-job';
const isActiveJob = (job: GenerationJob | null | undefined) =>
  Boolean(job && !terminalJobStates.includes(job.status));

function jobFailureSummary(message: string | null | undefined): string {
  const normalized = (message ?? '').toLowerCase();
  if (normalized.includes('out of memory') || normalized.includes('cuda oom')) {
    return 'The GPU ran out of memory. Try a smaller canvas, fewer controls, or the balanced model.';
  }
  if (
    normalized.includes('model') &&
    (normalized.includes('missing') || normalized.includes('not found'))
  ) {
    return 'A required local model is unavailable. Verify the selected model in Models & Engine.';
  }
  if (normalized.includes('engine') || normalized.includes('comfy')) {
    return 'The local generation engine stopped responding. Open diagnostics, then verify or restart it.';
  }
  return 'The local job could not finish. Your existing media and settings are unchanged.';
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '—';
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return `${minutes}m ${remainder.toString().padStart(2, '0')}s`;
}

function formatBytes(bytes: number): string {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index < 2 ? 0 : 1)} ${units[index]}`;
}

function JobProgressPanel({
  job,
  onCancel,
  onDismiss,
  onDetails,
  onViewResult,
  onDiagnostics,
  compact = false,
}: {
  job: GenerationJob;
  onCancel?: () => void;
  onDismiss?: () => void;
  onDetails?: () => void;
  onViewResult?: () => void;
  onDiagnostics?: () => void;
  compact?: boolean;
}) {
  const active = isActiveJob(job);
  const determinate = Boolean(job.progress_determinate);
  const percentage = job.status === 'completed' ? 100 : determinate ? job.progress : null;
  return (
    <section
      className={`job-progress-panel ${compact ? 'job-progress-panel--compact' : ''}`}
      aria-live="polite"
      aria-label="Local generation progress"
    >
      <header>
        <div>
          <span className="eyebrow">Local job</span>
          <h2>{stateLabels[job.status] ?? job.status.replaceAll('_', ' ')}</h2>
        </div>
        <StatusPill tone={job.status === 'failed' ? 'danger' : active ? 'warning' : 'ready'}>
          {percentage == null ? (active ? 'Working' : stateLabels[job.status]) : `${percentage}%`}
        </StatusPill>
      </header>
      <div
        className={`progress ${determinate ? '' : 'progress--indeterminate'}`}
        role="progressbar"
        aria-valuemin={determinate ? 0 : undefined}
        aria-valuemax={determinate ? 100 : undefined}
        aria-valuenow={percentage ?? undefined}
        aria-label={
          determinate ? `${percentage}% complete` : `${stateLabels[job.status]} in progress`
        }
      >
        <span style={determinate ? { width: `${percentage}%` } : undefined} />
      </div>
      <dl className="job-progress-metrics">
        <div>
          <dt>Step</dt>
          <dd>
            {job.current_step != null && job.total_steps != null
              ? `${job.current_step} / ${job.total_steps}`
              : '—'}
          </dd>
        </div>
        <div>
          <dt>Elapsed</dt>
          <dd>{formatDuration(job.elapsed_seconds)}</dd>
        </div>
        <div>
          <dt>ETA</dt>
          <dd>{job.eta_seconds != null ? `~${formatDuration(job.eta_seconds)}` : '—'}</dd>
        </div>
        <div>
          <dt>Queue</dt>
          <dd>{job.queue_position ? `#${job.queue_position}` : active ? 'Active' : '—'}</dd>
        </div>
      </dl>
      <div className="job-progress-context">
        <span>
          <strong>{job.model_alias ?? 'Local model'}</strong>
          {job.model_family ? ` · ${job.model_family}` : ''}
        </span>
        <span>
          {job.output_width && job.output_height
            ? `${job.output_width} × ${job.output_height}`
            : 'Output size resolves locally'}
        </span>
      </div>
      {job.status === 'failed' && job.error_message && (
        <p className="inline-error">{jobFailureSummary(job.error_message)}</p>
      )}
      <footer>
        {active && onCancel && (
          <Button onClick={onCancel}>
            <X /> Cancel
          </Button>
        )}
        {job.status === 'completed' && job.result_generation_id && onViewResult && (
          <Button variant="primary" onClick={onViewResult}>
            <Image /> View result
          </Button>
        )}
        {onDetails && <Button onClick={onDetails}>View job details</Button>}
        {job.status === 'failed' && onDiagnostics && (
          <Button onClick={onDiagnostics}>
            <Gauge /> Open diagnostics
          </Button>
        )}
        {!active && onDismiss && (
          <Button variant="ghost" onClick={onDismiss}>
            Dismiss
          </Button>
        )}
      </footer>
    </section>
  );
}

function PageTitle({
  eyebrow,
  title,
  body,
  actions,
}: {
  eyebrow: string;
  title: string;
  body?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-title">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        {body && <p>{body}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

function LoadingView() {
  return (
    <div className="loading-view" aria-busy="true" aria-label="Loading local studio">
      <div className="skeleton skeleton-title" />
      <div className="skeleton-grid">
        <div className="skeleton" />
        <div className="skeleton" />
        <div className="skeleton" />
      </div>
    </div>
  );
}

function StartupView({ service }: { service: LocalServiceInfo }) {
  const phases = [
    'Preparing local workspace',
    'Opening database',
    'Starting local service',
    'Verifying local service',
    'Ready',
  ];
  const activeIndex = Math.max(0, phases.indexOf(service.phase));
  return (
    <div className="startup-view" role="status" aria-live="polite">
      <span className="eyebrow">Starting local studio</span>
      <h1>{service.phase}</h1>
      <p>Vanta is preparing its private local workspace. Nothing leaves this computer.</p>
      <ol className="startup-steps">
        {phases.map((phase, index) => (
          <li className={index <= activeIndex ? 'complete' : ''} key={phase}>
            <span aria-hidden="true" />
            {phase}
          </li>
        ))}
      </ol>
    </div>
  );
}

function TitleBar() {
  const [maximized, setMaximized] = useState(false);
  const [desktopWindow] = useState(() =>
    '__TAURI_INTERNALS__' in window ? getCurrentWindow() : null,
  );

  useEffect(() => {
    if (!desktopWindow) return;
    let unlisten: (() => void) | undefined;
    const syncMaximized = async () => setMaximized(await desktopWindow.isMaximized());
    void syncMaximized();
    void desktopWindow
      .onResized(() => void syncMaximized())
      .then((stop) => {
        unlisten = stop;
      });
    return () => unlisten?.();
  }, [desktopWindow]);

  const runWindowCommand = (
    command: (appWindow: ReturnType<typeof getCurrentWindow>) => Promise<void>,
  ) => {
    if (desktopWindow) void command(desktopWindow);
  };

  const toggleMaximized = () => {
    runWindowCommand(async (appWindow) => {
      await appWindow.toggleMaximize();
      setMaximized(await appWindow.isMaximized());
    });
  };

  return (
    <header className="titlebar" aria-label="Window title bar">
      <div className="titlebar__drag" data-tauri-drag-region onDoubleClick={toggleMaximized}>
        <span className="titlebar__mark" aria-hidden="true">
          <span />
        </span>
        <span className="titlebar__name" data-tauri-drag-region>
          Vanta
        </span>
        <span className="titlebar__section" data-tauri-drag-region>
          Local Character Studio
        </span>
      </div>
      <div className="titlebar__controls" role="group" aria-label="Window controls">
        <button
          type="button"
          className="titlebar__control"
          onClick={() => runWindowCommand((appWindow) => appWindow.minimize())}
          aria-label="Minimize window"
          title="Minimize"
        >
          <Minus aria-hidden="true" />
        </button>
        <button
          type="button"
          className="titlebar__control"
          onClick={toggleMaximized}
          aria-label={maximized ? 'Restore window' : 'Maximize window'}
          title={maximized ? 'Restore' : 'Maximize'}
        >
          {maximized ? <Copy aria-hidden="true" /> : <span className="titlebar__maximize" />}
        </button>
        <button
          type="button"
          className="titlebar__control titlebar__control--close"
          onClick={() => runWindowCommand((appWindow) => appWindow.close())}
          aria-label="Close window"
          title="Close"
        >
          <X aria-hidden="true" />
        </button>
      </div>
    </header>
  );
}

export function App() {
  const [screen, setScreen] = useState<Screen>('create');
  const [data, setData] = useState<AppData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [service, setService] = useState<LocalServiceInfo | null>(null);
  const [serviceRetry, setServiceRetry] = useState(0);
  const [showRuntimeDiagnostics, setShowRuntimeDiagnostics] = useState(false);
  const [toast, setToast] = useState('');
  const [navOpen, setNavOpen] = useState(false);
  const [generationDraft, setGenerationDraft] = useState<Record<string, unknown> | null>(null);
  const [showJobs, setShowJobs] = useState(false);
  const [createJobId, setCreateJobId] = useState<string | null>(() => {
    try {
      return window.localStorage.getItem(activeJobStorageKey);
    } catch {
      return null;
    }
  });
  const [gallerySelectionId, setGallerySelectionId] = useState<string | null>(null);
  const [diagnosticsRequest, setDiagnosticsRequest] = useState(0);
  const completedJobNotifications = useRef(new Set<string>());

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [
        characters,
        presets,
        recipes,
        gallery,
        components,
        modelResponse,
        settings,
        loras,
        jobs,
        poses,
        motion,
        trainingDatasets,
        trainingRuns,
      ] = await Promise.all([
        api.get<CharacterRecord[]>('/characters'),
        api.get<PresetRecord[]>('/presets'),
        api.get<RecipeRecord[]>('/recipes'),
        api.get<GenerationRecord[]>('/gallery'),
        api.get<EngineComponent[]>('/engine/components'),
        api.get<{ hardware: AppData['hardware']; packs: ModelPack[] }>('/engine/model-packs'),
        api.get<SettingsRecord>('/settings'),
        api.get<LoraRecord[]>('/loras'),
        api.get<GenerationJob[]>('/jobs'),
        api.get<PoseRecord[]>('/poses'),
        api.get<MotionAsset[]>('/motion-assets'),
        api.get<TrainingDataset[]>('/training/datasets'),
        api.get<TrainingRun[]>('/training/runs'),
      ]);
      setData({
        characters,
        presets,
        recipes,
        gallery,
        components,
        packs: modelResponse.packs,
        hardware: modelResponse.hardware,
        settings,
        loras,
        jobs,
        poses,
        motion,
        trainingDatasets,
        trainingRuns,
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The local studio could not start.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let disposed = false;
    let timer: number | undefined;
    const poll = async () => {
      const info = await getLocalServiceInfo();
      if (disposed) return;
      setService(info);
      if (info.state === 'ready') {
        await load();
        return;
      }
      setLoading(false);
      if (info.state === 'failed') {
        setError(info.last_sanitized_error ?? 'The local service could not start.');
        return;
      }
      timer = window.setTimeout(() => void poll(), 450);
    };
    void poll();
    return () => {
      disposed = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [load, serviceRetry]);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(''), 3800);
    return () => window.clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      void Promise.all([
        api.get<GenerationJob[]>('/jobs'),
        api.get<TrainingRun[]>('/training/runs'),
      ]).then(([jobs, trainingRuns]) => {
        setData((current) => (current ? { ...current, jobs, trainingRuns } : current));
      });
    }, 1100);
    return () => window.clearInterval(timer);
  }, []);

  const createJob = data?.jobs.find((job) => job.id === createJobId) ?? null;
  const recordCreateJob = useCallback((job: GenerationJob) => {
    setCreateJobId(job.id);
    try {
      window.localStorage.setItem(activeJobStorageKey, job.id);
    } catch {
      // The job remains visible for this session when browser storage is unavailable.
    }
    setData((current) => {
      if (!current) return current;
      const jobs = current.jobs.some((item) => item.id === job.id)
        ? current.jobs.map((item) => (item.id === job.id ? job : item))
        : [job, ...current.jobs];
      return { ...current, jobs };
    });
  }, []);
  const dismissCreateJob = useCallback(() => {
    setCreateJobId(null);
    try {
      window.localStorage.removeItem(activeJobStorageKey);
    } catch {
      // In-memory dismissal still succeeds.
    }
  }, []);

  useEffect(() => {
    if (
      !createJob ||
      createJob.status !== 'completed' ||
      completedJobNotifications.current.has(createJob.id)
    ) {
      return;
    }
    completedJobNotifications.current.add(createJob.id);
    void load();
    setToast('Generation saved to your local Gallery');
  }, [createJob, load]);

  const notify = (message: string) => setToast(message);
  const navigate = (next: Screen) => {
    setScreen(next);
    setNavOpen(false);
    document.getElementById('main-content')?.focus();
  };
  const engineReady =
    data?.components.some(
      (component) => component.id === 'workflow-runtime' && component.state === 'ready',
    ) && data.packs.some((pack) => pack.is_default && pack.verified);

  return (
    <div className="desktop-frame">
      <TitleBar />
      <div className="app-shell">
        <a className="skip-link" href="#main-content">
          Skip to main content
        </a>
        <button
          className="mobile-menu"
          onClick={() => setNavOpen((value) => !value)}
          aria-label="Toggle navigation"
          aria-expanded={navOpen}
        >
          <Menu />
        </button>
        <aside className={`sidebar ${navOpen ? 'sidebar--open' : ''}`}>
          <div className="brand">
            <span className="brand-mark">
              <span />
            </span>
            <div>
              <strong>Vanta</strong>
              <small>Character studio</small>
            </div>
          </div>
          <nav aria-label="Primary navigation">
            {navItems.map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.id}
                  className={screen === item.id ? 'active' : ''}
                  onClick={() => navigate(item.id)}
                  aria-current={screen === item.id ? 'page' : undefined}
                >
                  <Icon aria-hidden="true" />
                  <span>{item.label}</span>
                  {screen === item.id && (
                    <ChevronRight className="nav-chevron" aria-hidden="true" />
                  )}
                </button>
              );
            })}
          </nav>
          <div className="local-card">
            <div className="local-card__title">
              <span className={`engine-dot ${engineReady ? '' : 'engine-dot--warning'}`} />
              <strong>{engineReady ? 'Studio ready' : 'Setup needed'}</strong>
            </div>
            <p>{data?.hardware.gpu_name ?? 'Local hardware'} · private</p>
            <button onClick={() => navigate('engine')}>
              View local engine <ChevronRight />
            </button>
          </div>
          <div className="privacy-mark">
            <ShieldCheck />
            <span>
              Local by design
              <br />
              <small>No cloud connection</small>
            </span>
          </div>
        </aside>
        <main id="main-content" tabIndex={-1}>
          {data && (
            <button
              className={`jobs-button ${data.jobs.some(isActiveJob) ? 'jobs-button--active' : ''}`}
              onClick={() => setShowJobs(true)}
            >
              <Zap /> Jobs
              {data.jobs.some(
                (job) => !['completed', 'failed', 'cancelled'].includes(job.status),
              ) && (
                <span>
                  {
                    data.jobs.filter(
                      (job) => !['completed', 'failed', 'cancelled'].includes(job.status),
                    ).length
                  }
                </span>
              )}
            </button>
          )}
          {loading && !service && <LoadingView />}
          {!loading && service && service.state !== 'ready' && !error && (
            <StartupView service={service} />
          )}
          {!loading && error && (
            <div className="failure-state">
              <div className="failure-mark">
                <Wrench />
              </div>
              <span className="eyebrow">Local service unavailable</span>
              <h1>Vanta could not reach its orchestrator.</h1>
              <p>{error} Nothing has been sent off this device.</p>
              <div className="failure-actions">
                <Button
                  variant="primary"
                  onClick={async () => {
                    await restartLocalService();
                    setError('');
                    setLoading(true);
                    setServiceRetry((value) => value + 1);
                  }}
                >
                  Restart local service
                </Button>
                <Button
                  onClick={async () => {
                    await repairApplicationRuntime();
                    setError('');
                    setLoading(true);
                    setServiceRetry((value) => value + 1);
                  }}
                >
                  Repair application runtime
                </Button>
                <Button onClick={() => setShowRuntimeDiagnostics((value) => !value)}>
                  Open diagnostics
                </Button>
              </div>
              {showRuntimeDiagnostics && service && (
                <dl className="runtime-diagnostics">
                  <div>
                    <dt>Sidecar state</dt>
                    <dd>{service.state}</dd>
                  </div>
                  <div>
                    <dt>Application data</dt>
                    <dd>{service.application_data_path}</dd>
                  </div>
                  <div>
                    <dt>Logs</dt>
                    <dd>{service.logs_path}</dd>
                  </div>
                  <div>
                    <dt>Last exit</dt>
                    <dd>{service.last_process_exit_code ?? 'None'}</dd>
                  </div>
                </dl>
              )}
            </div>
          )}
          {!loading && data && (
            <>
              {!engineReady && (
                <SetupWizard
                  data={data}
                  refresh={load}
                  notify={notify}
                  goEngine={() => navigate('engine')}
                />
              )}
              {screen === 'create' && (
                <CreateScreen
                  data={data}
                  refresh={load}
                  notify={notify}
                  goEngine={() => navigate('engine')}
                  goPoses={() => navigate('poses')}
                  initialDraft={generationDraft}
                  onDraftUsed={() => setGenerationDraft(null)}
                  job={createJob}
                  onJob={recordCreateJob}
                  onDismissJob={dismissCreateJob}
                  onJobDetails={() => setShowJobs(true)}
                  onViewResult={(generationId) => {
                    setGallerySelectionId(generationId);
                    navigate('gallery');
                  }}
                  onDiagnostics={() => {
                    setDiagnosticsRequest((value) => value + 1);
                    navigate('engine');
                  }}
                />
              )}
              {screen === 'characters' && (
                <CharactersScreen
                  items={data.characters}
                  loras={data.loras}
                  refresh={load}
                  notify={notify}
                />
              )}
              {screen === 'poses' && (
                <PoseLibraryScreen
                  items={data.poses}
                  characters={data.characters}
                  refresh={load}
                  notify={notify}
                />
              )}
              {screen === 'motion' && (
                <MotionLibraryScreen items={data.motion} refresh={load} notify={notify} />
              )}
              {screen === 'training' && (
                <TrainingScreen
                  data={data}
                  refresh={load}
                  notify={notify}
                  goEngine={() => navigate('engine')}
                />
              )}
              {screen === 'presets' && (
                <PresetsScreen
                  items={data.presets}
                  recipes={data.recipes}
                  characters={data.characters}
                  refresh={load}
                  notify={notify}
                />
              )}
              {screen === 'gallery' && (
                <GalleryScreen
                  items={data.gallery}
                  motion={data.motion}
                  videoReady={
                    data.components.find((item) => item.id === 'video-generation')?.state ===
                    'ready'
                  }
                  editingReady={
                    data.components.find((item) => item.id === 'image-finishing')?.state === 'ready'
                  }
                  notify={notify}
                  refresh={load}
                  onGenerateSimilar={(draft) => {
                    setGenerationDraft(draft);
                    navigate('create');
                  }}
                  onCreateVariation={(generation) => {
                    setGenerationDraft({
                      ...(generation.metadata.request ?? {}),
                      source_generation_id: generation.id,
                      variation_strength: 0.45,
                      variation_mode: 'general',
                      variation_prompt: 'a refined editorial reinterpretation',
                      seed: Math.floor(Math.random() * 2_000_000_000),
                    });
                    navigate('create');
                  }}
                  onUpscale={async (generation) => {
                    const job = await api.post<GenerationJob>('/generations', {
                      operation: 'upscale',
                      source_generation_id: generation.id,
                      seed: 0,
                      upscale_profile: 'realesrgan_x2plus',
                    });
                    recordCreateJob(job);
                    notify('2× upscale queued locally');
                    navigate('create');
                  }}
                  initialSelectionId={gallerySelectionId}
                  onSelectionHandled={() => setGallerySelectionId(null)}
                  onJob={recordCreateJob}
                  onDiagnostics={() => {
                    setDiagnosticsRequest((value) => value + 1);
                    navigate('engine');
                  }}
                />
              )}
              {screen === 'engine' && (
                <EngineScreen
                  data={data}
                  refresh={load}
                  notify={notify}
                  diagnosticsRequest={diagnosticsRequest}
                />
              )}
              {screen === 'settings' && (
                <SettingsScreen
                  settings={data.settings}
                  hardware={data.hardware}
                  refresh={load}
                  notify={notify}
                />
              )}
            </>
          )}
        </main>
        <Drawer open={showJobs} title="Local jobs" onClose={() => setShowJobs(false)}>
          <div className="metadata-drawer">
            {data?.jobs.length ? (
              data.jobs.map((job) => (
                <JobProgressPanel
                  key={job.id}
                  job={job}
                  compact
                  {...(isActiveJob(job)
                    ? {
                        onCancel: () => {
                          void api
                            .post<GenerationJob>(`/generations/${job.id}/cancel`)
                            .then(recordCreateJob);
                        },
                      }
                    : {})}
                  {...(job.result_generation_id
                    ? {
                        onViewResult: () => {
                          setGallerySelectionId(job.result_generation_id ?? null);
                          setShowJobs(false);
                          navigate('gallery');
                        },
                      }
                    : {})}
                  onDiagnostics={() => {
                    setShowJobs(false);
                    setDiagnosticsRequest((value) => value + 1);
                    navigate('engine');
                  }}
                />
              ))
            ) : (
              <EmptyState
                title="No local jobs"
                body="Generation activity will stay here while you work elsewhere."
              />
            )}
          </div>
        </Drawer>
        {toast && (
          <div className="toast" role="status">
            <Check />
            {toast}
          </div>
        )}
      </div>
    </div>
  );
}

function SetupWizard({
  data,
  refresh,
  notify,
  goEngine,
}: {
  data: AppData;
  refresh: () => Promise<void>;
  notify: (message: string) => void;
  goEngine: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const runtime = data.components.find((item) => item.id === 'workflow-runtime');
  const model =
    data.packs.find((item) => item.is_default) ??
    data.packs.find((item) => item.alias === 'photoreal_balanced');
  const engineReady = runtime?.state === 'ready';
  const modelReady = Boolean(model?.installed && model.verified);
  const installEngine = async () => {
    if (!runtime) return;
    setBusy(true);
    setError('');
    try {
      const action =
        runtime.state === 'repair_needed'
          ? 'repair'
          : runtime.state === 'stopped'
            ? 'start'
            : 'install';
      await api.post(`/engine/components/${runtime.id}/${action}`);
      await api.put('/settings/setup_step', { value: 'engine' });
      notify(action === 'start' ? 'Starting the local image engine' : 'Local engine setup started');
      await refresh();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : 'Vanta could not prepare the local image engine.',
      );
    } finally {
      setBusy(false);
    }
  };
  const importModel = async () => {
    const sourcePath = await chooseLocalModelFile();
    if (!sourcePath) return;
    setBusy(true);
    setError('');
    try {
      await api.post('/engine/models/import', {
        source_path: sourcePath,
        alias: 'photoreal_balanced',
        license_notes: 'User-selected local checkpoint; review its license before redistribution.',
      });
      await api.put('/settings/setup_step', { value: 'model_verified' });
      notify('Local model imported and verified');
      await refresh();
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : 'Vanta could not verify this local model.',
      );
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    if (engineReady && modelReady && data.settings.values.setup_completed !== 'true') {
      void api.put('/settings/setup_completed', { value: 'true' });
    }
  }, [data.settings.values.setup_completed, engineReady, modelReady]);
  const step = modelReady ? 4 : engineReady ? 3 : 2;
  return (
    <section className="setup-wizard" aria-labelledby="setup-title">
      <div className="setup-wizard__intro">
        <span className="eyebrow">First-run setup</span>
        <h1 id="setup-title">Prepare your private studio.</h1>
        <p>
          Vanta stays on this device. Setup installs only the reviewed local engine and a model you
          choose.
        </p>
      </div>
      <ol className="setup-steps" aria-label="Local setup progress">
        {[
          ['Private by design', 'No account, telemetry, cloud inference, or public service.'],
          [
            'Check this device',
            `${data.hardware.gpu_name} · ${data.hardware.vram_gb} GB VRAM · ${data.hardware.free_disk_gb} GB free`,
          ],
          ['Prepare local engine', runtime?.last_health_message ?? 'Checking engine status'],
          [
            'Import a model',
            modelReady
              ? 'Verified local SDXL model and diagnostic generation complete.'
              : 'Choose a compatible SDXL .safetensors checkpoint.',
          ],
        ].map(([title, detail], index) => (
          <li
            className={index + 1 < step ? 'complete' : index + 1 === step ? 'current' : ''}
            key={title}
          >
            <span>{index + 1 < step ? <Check /> : index + 1}</span>
            <div>
              <strong>{title}</strong>
              <small>{detail}</small>
            </div>
          </li>
        ))}
      </ol>
      {error && (
        <p className="setup-wizard__error" role="alert">
          {error}
        </p>
      )}
      <div className="setup-wizard__actions">
        {!engineReady ? (
          <Button
            variant="primary"
            onClick={() => void installEngine()}
            disabled={busy || runtime?.state === 'installing'}
          >
            <Download />
            {runtime?.state === 'installing'
              ? `Installing ${runtime.progress}%`
              : runtime?.state === 'stopped'
                ? 'Start local engine'
                : runtime?.state === 'repair_needed'
                  ? 'Repair local engine'
                  : 'Install local engine'}
          </Button>
        ) : !modelReady ? (
          <Button variant="primary" onClick={() => void importModel()} disabled={busy}>
            <Upload /> {busy ? 'Verifying local model…' : 'Import local SDXL model'}
          </Button>
        ) : (
          <Button variant="primary" onClick={goEngine}>
            <Check /> Studio ready
          </Button>
        )}
        <Button variant="ghost" onClick={goEngine}>
          View technical details
        </Button>
      </div>
      <p className="setup-wizard__note">
        Storage: {data.settings.paths.data}. The engine requires about 2 GB; model size depends on
        the file you import.
      </p>
    </section>
  );
}

function CreateScreen({
  data,
  refresh,
  notify,
  goEngine,
  goPoses,
  initialDraft,
  onDraftUsed,
  job,
  onJob,
  onDismissJob,
  onJobDetails,
  onViewResult,
  onDiagnostics,
}: {
  data: AppData;
  refresh: () => Promise<void>;
  notify: (message: string) => void;
  goEngine: () => void;
  goPoses: () => void;
  initialDraft: Record<string, unknown> | null;
  onDraftUsed: () => void;
  job: GenerationJob | null;
  onJob: (job: GenerationJob) => void;
  onDismissJob: () => void;
  onJobDetails: () => void;
  onViewResult: (generationId: string) => void;
  onDiagnostics: () => void;
}) {
  const [mode, setMode] = useState<'simple' | 'studio'>(
    data.settings.values.default_mode === 'studio' ? 'studio' : 'simple',
  );
  const [recipeName, setRecipeName] = useState('Y2K Bedroom Study');
  const [recipeId, setRecipeId] = useState('');
  const [prompt, setPrompt] = useState(
    'Moody Y2K bedroom portrait, intimate indie editorial mood, low phone-camera angle, black bedspread scattered with fashion magazines and a vinyl record.',
  );
  const [tags, setTags] = useState(['Cinematic', 'Film grain']);
  const [saving, setSaving] = useState(false);
  const [characterId, setCharacterId] = useState(data.characters[0]?.id ?? '');
  const [steps, setSteps] = useState(30);
  const [guidance, setGuidance] = useState(5.5);
  const [width, setWidth] = useState(832);
  const [height, setHeight] = useState(1216);
  const [sampler, setSampler] = useState('euler');
  const [scheduler, setScheduler] = useState('normal');
  const [modelAlias, setModelAlias] = useState(
    data.packs.find((item) => item.is_default && item.installed && item.verified)?.alias ??
      'photoreal_balanced',
  );
  const [seed, setSeed] = useState(() => Math.floor(Math.random() * 2_000_000_000));
  const [negativePrompt, setNegativePrompt] = useState(
    'low quality, malformed hands, artificial skin texture',
  );
  const [sourceGenerationId, setSourceGenerationId] = useState<string | null>(null);
  const [variationStrength, setVariationStrength] = useState(0.45);
  const [variationMode, setVariationMode] = useState('general');
  const [variationPrompt, setVariationPrompt] = useState('');
  const [poseId, setPoseId] = useState('');
  const [poseStrength, setPoseStrength] = useState(0.8);
  const [identityReferenceId, setIdentityReferenceId] = useState('');
  const [identityStrength, setIdentityStrength] = useState(0.6);
  const [loraIds, setLoraIds] = useState<string[]>([]);
  const [loraStrengths, setLoraStrengths] = useState<Record<string, number>>({});
  const [loraClipStrengths, setLoraClipStrengths] = useState<Record<string, number>>({});
  const [videoProfile, setVideoProfile] = useState('safe');
  const [videoDuration, setVideoDuration] = useState(2);
  const [motionPrompt, setMotionPrompt] = useState('subtle breathing and a gentle posture shift');
  const availablePoses = data.poses.filter(
    (item) => item.status === 'ready' && (!item.character_id || item.character_id === characterId),
  );
  const runtime = data.components.find((item) => item.id === 'workflow-runtime');
  const selectableModels = data.packs.filter(
    (item) =>
      ['photoreal_balanced', 'preview_fast', 'photoreal_max'].includes(item.alias) &&
      item.installed &&
      item.verified,
  );
  const model = data.packs.find((item) => item.alias === modelAlias);
  const isFlux = modelAlias === 'photoreal_max';
  const canGenerate = runtime?.state === 'ready' && Boolean(model?.installed && model.verified);
  const categories = [
    'identity_modifier',
    'wardrobe',
    'expression',
    'pose',
    'location',
    'lighting',
    'camera',
    'quality',
    'negative',
  ];
  const variationModes = [
    ['general', 'General variation', 'A fresh interpretation with the source as structure.'],
    ['preserve_composition', 'Preserve composition', 'Keep framing and spatial relationships.'],
    ['preserve_identity', 'Preserve identity', 'Use the character reference with image-to-image.'],
    ['preserve_pose', 'Preserve pose', 'Restore the source Pose Control asset.'],
    ['clothing', 'Change clothing', 'Replace wardrobe while preserving the scene.'],
    ['background', 'Change background', 'Replace location while preserving the subject.'],
    ['lighting', 'Change lighting', 'Relight the same composition.'],
    ['expression', 'Change expression', 'Adjust expression with restrained denoise.'],
    ['custom', 'Custom prompt override', 'Direct a specific controlled change.'],
  ] as const;
  const variationDefaults: Record<string, string> = {
    general: 'a refined editorial reinterpretation',
    preserve_composition: 'subtle detail variation, preserve framing and composition',
    preserve_identity: 'preserve the original fictional character identity',
    preserve_pose: 'preserve the selected structural pose',
    clothing: 'tailored deep rose blazer and black trousers, natural fabric folds',
    background: 'restrained plum editorial studio with a seamless backdrop',
    lighting: 'soft rose-gold side light with controlled cinematic shadows',
    expression: 'calm confident expression with a subtle closed-mouth smile',
    custom: '',
  };
  const options = (category: string) =>
    data.presets.filter((preset) => preset.category === category);
  const [selected, setSelected] = useState<Record<string, string>>(() =>
    Object.fromEntries(categories.map((category) => [category, options(category)[0]?.id ?? ''])),
  );
  useEffect(() => {
    if (!initialDraft) return;
    setPrompt(String(initialDraft.direction ?? initialDraft.freeform_direction ?? prompt));
    setTags(Array.isArray(initialDraft.custom_tags) ? initialDraft.custom_tags.map(String) : tags);
    setCharacterId(String(initialDraft.character_id ?? characterId));
    setSteps(Number(initialDraft.steps ?? steps));
    setGuidance(Number(initialDraft.guidance ?? guidance));
    setWidth(Number(initialDraft.width ?? width));
    setHeight(Number(initialDraft.height ?? height));
    setSampler(String(initialDraft.sampler ?? sampler));
    setScheduler(String(initialDraft.scheduler ?? scheduler));
    setModelAlias(String(initialDraft.model_alias ?? modelAlias));
    setSeed(Number(initialDraft.seed ?? seed));
    setNegativePrompt(String(initialDraft.negative_prompt ?? negativePrompt));
    setSourceGenerationId(
      initialDraft.source_generation_id ? String(initialDraft.source_generation_id) : null,
    );
    setVariationStrength(Number(initialDraft.variation_strength ?? 0.45));
    setVariationMode(String(initialDraft.variation_mode ?? 'general'));
    setVariationPrompt(String(initialDraft.variation_prompt ?? ''));
    setPoseId(String(initialDraft.pose_id ?? ''));
    setPoseStrength(Number(initialDraft.pose_strength ?? 0.8));
    setIdentityReferenceId(String(initialDraft.identity_reference_id ?? ''));
    setIdentityStrength(Number(initialDraft.identity_strength ?? 0.6));
    setLoraIds(Array.isArray(initialDraft.lora_ids) ? initialDraft.lora_ids.map(String) : []);
    setLoraStrengths(
      typeof initialDraft.lora_weights === 'object' && initialDraft.lora_weights
        ? (initialDraft.lora_weights as Record<string, number>)
        : {},
    );
    setLoraClipStrengths(
      typeof initialDraft.lora_clip_weights === 'object' && initialDraft.lora_clip_weights
        ? (initialDraft.lora_clip_weights as Record<string, number>)
        : {},
    );
    onDraftUsed();
  }, [initialDraft]);
  useEffect(() => {
    if (poseId && !availablePoses.some((item) => item.id === poseId)) setPoseId('');
  }, [availablePoses, poseId]);
  const presetText = (category: string) =>
    options(category).find((item) => item.id === selected[category])?.prompt ?? '';
  const generationRequest = () => ({
    character_id: characterId || null,
    recipe_id: recipeId || null,
    character_identity: [
      data.characters.find((item) => item.id === characterId)?.identity_description ?? '',
      presetText('identity_modifier'),
    ]
      .filter(Boolean)
      .join(', '),
    wardrobe: variationMode === 'clothing' ? variationPrompt : presetText('wardrobe'),
    expression: variationMode === 'expression' ? variationPrompt : presetText('expression'),
    pose: presetText('pose'),
    location: variationMode === 'background' ? variationPrompt : presetText('location'),
    lighting: variationMode === 'lighting' ? variationPrompt : presetText('lighting'),
    camera: presetText('camera'),
    quality: presetText('quality'),
    direction: prompt,
    custom_tags: tags,
    negative_prompt: [negativePrompt, presetText('negative')].filter(Boolean).join(', '),
    model_alias: modelAlias,
    seed,
    width: poseId || isFlux ? 768 : width,
    height: poseId || isFlux ? 1024 : height,
    steps,
    guidance,
    sampler: isFlux ? 'euler' : sampler,
    scheduler: isFlux ? 'simple' : scheduler,
    lora_ids: loraIds,
    lora_weights: loraStrengths,
    lora_clip_weights: loraClipStrengths,
    identity_reference_id: identityReferenceId || null,
    identity_strength: identityStrength,
    source_generation_id: sourceGenerationId,
    variation_strength: variationStrength,
    variation_mode: variationMode,
    variation_prompt: variationPrompt,
    pose_id: poseId || null,
    pose_strength: poseId ? poseStrength : null,
  });
  const saveRecipe = async () => {
    setSaving(true);
    try {
      const payload = {
        name: recipeName.trim() || 'Untitled recipe',
        character_id: characterId || null,
        freeform_prompt: prompt,
        negative_prompt: negativePrompt,
        model_profile: modelAlias,
        preset_ids: Object.values(selected).filter(Boolean),
        scope: characterId ? 'character' : 'global',
        scope_id: characterId || null,
        favorite: false,
        tags,
        model_family: isFlux ? 'FLUX' : 'SDXL',
        model_file: model?.filename ?? '',
        lora_stack: loraIds.map((id) => ({
          id,
          strength: loraStrengths[id] ?? 1,
          clip_strength: loraClipStrengths[id] ?? 1,
        })),
        identity_settings: {
          reference_id: identityReferenceId || null,
          strength: identityStrength,
        },
        pose_settings: { pose_id: poseId || null, strength: poseStrength },
        variation_settings: {
          source_generation_id: sourceGenerationId,
          mode: variationMode,
          prompt: variationPrompt,
          strength: variationStrength,
        },
        video_settings: {
          profile: videoProfile,
          duration_seconds: videoDuration,
          motion_prompt: motionPrompt,
        },
        generation_settings: { width, height, steps, guidance, sampler, scheduler, mode },
      };
      const saved = recipeId
        ? await api.put<RecipeRecord>(`/recipes/${recipeId}`, payload)
        : await api.post<RecipeRecord>('/recipes', payload);
      setRecipeId(saved.id);
      notify(recipeId ? 'Recipe updated locally' : 'Recipe saved to your local library');
      await refresh();
    } finally {
      setSaving(false);
    }
  };
  const applyRecipe = (recipe: RecipeRecord) => {
    const generation = recipe.generation_settings;
    const identity = recipe.identity_settings;
    const pose = recipe.pose_settings;
    const variation = recipe.variation_settings;
    const video = recipe.video_settings;
    setRecipeId(recipe.id);
    setRecipeName(recipe.name);
    setCharacterId(recipe.character_id ?? '');
    setPrompt(recipe.freeform_prompt);
    setNegativePrompt(recipe.negative_prompt);
    setModelAlias(recipe.model_profile);
    setTags(recipe.tags);
    setSelected((current) => ({
      ...current,
      ...Object.fromEntries(recipe.items.map((item) => [item.category, item.preset_id])),
    }));
    setLoraIds(recipe.lora_stack.map((item) => item.id));
    setLoraStrengths(Object.fromEntries(recipe.lora_stack.map((item) => [item.id, item.strength])));
    setLoraClipStrengths(
      Object.fromEntries(recipe.lora_stack.map((item) => [item.id, item.clip_strength])),
    );
    setIdentityReferenceId(String(identity.reference_id ?? ''));
    setIdentityStrength(Number(identity.strength ?? 0.6));
    setPoseId(String(pose.pose_id ?? ''));
    setPoseStrength(Number(pose.strength ?? 0.8));
    setSourceGenerationId(
      variation.source_generation_id ? String(variation.source_generation_id) : null,
    );
    setVariationMode(String(variation.mode ?? 'general'));
    setVariationPrompt(String(variation.prompt ?? ''));
    setVariationStrength(Number(variation.strength ?? 0.45));
    setVideoProfile(String(video.profile ?? 'safe'));
    setVideoDuration(Number(video.duration_seconds ?? 2));
    setMotionPrompt(String(video.motion_prompt ?? ''));
    setWidth(Number(generation.width ?? 832));
    setHeight(Number(generation.height ?? 1216));
    setSteps(Number(generation.steps ?? 30));
    setGuidance(Number(generation.guidance ?? 5.5));
    setSampler(String(generation.sampler ?? 'euler'));
    setScheduler(String(generation.scheduler ?? 'normal'));
    setMode(generation.mode === 'studio' ? 'studio' : 'simple');
    notify(`Loaded recipe ${recipe.name}`);
  };
  const generate = async () => {
    if (!canGenerate) {
      goEngine();
      return;
    }
    onJob(await api.post<GenerationJob>('/generations', generationRequest()));
    notify('Generation queued locally');
  };
  const cancel = async () => {
    if (job) onJob(await api.post<GenerationJob>(`/generations/${job.id}/cancel`));
  };
  const toggleTag = (tag: string) =>
    setTags((current) =>
      current.includes(tag) ? current.filter((item) => item !== tag) : [...current, tag],
    );
  return (
    <div className="screen create-screen">
      <PageTitle
        eyebrow="Create image"
        title="Direct the scene."
        body="Compose the intent. Vanta handles the engine beneath it."
        actions={
          <>
            <div className="mode-switch" role="group" aria-label="Creation mode">
              <button
                className={mode === 'simple' ? 'active' : ''}
                onClick={() => setMode('simple')}
                aria-pressed={mode === 'simple'}
              >
                Simple
              </button>
              <button
                className={mode === 'studio' ? 'active' : ''}
                onClick={() => setMode('studio')}
                aria-pressed={mode === 'studio'}
              >
                Studio
              </button>
            </div>
            <Button onClick={() => void saveRecipe()} disabled={saving}>
              {saving ? 'Saving…' : 'Save recipe'}
            </Button>
            <Button
              variant="primary"
              onClick={() => void (isActiveJob(job) ? cancel() : generate())}
            >
              {isActiveJob(job) ? (
                <>
                  <X /> Cancel
                </>
              ) : (
                <>
                  <Zap /> Generate
                </>
              )}
            </Button>
          </>
        }
      />
      {job && (
        <JobProgressPanel
          job={job}
          onCancel={() => void cancel()}
          onDismiss={onDismissJob}
          onDetails={onJobDetails}
          {...(job.result_generation_id
            ? { onViewResult: () => onViewResult(job.result_generation_id as string) }
            : {})}
          onDiagnostics={onDiagnostics}
        />
      )}
      {!canGenerate && (
        <div className="capability-banner">
          <div>
            <Wrench />
            <span>
              <strong>Finish local setup to generate</strong>
              <small>
                {runtime?.state !== 'ready'
                  ? 'Install or repair the Local Generation Engine, then complete its health check.'
                  : 'Import and verify a compatible local checkpoint in Models & Engine.'}
              </small>
            </span>
          </div>
          <div>
            <Button variant="ghost" onClick={goEngine}>
              View details
            </Button>
            <Button onClick={goEngine}>
              {runtime?.state === 'ready' ? 'Import model' : 'Set up engine'}
            </Button>
          </div>
        </div>
      )}
      <div className="create-layout">
        <div className="create-canvas-column">
          <Panel className="preview-panel">
            <div className="preview-art">
              <div className="preview-noise" />
              <div className="preview-window" />
              <div className="preview-subject">
                <span />
              </div>
              <div className="preview-label">
                <small>Active character</small>
                <strong>
                  {data.characters.find((item) => item.id === characterId)?.name ??
                    'No character selected'}
                </strong>
                <span>Original adult · v0.3</span>
              </div>
              <div className="preview-index">01 / local study</div>
            </div>
            <footer>
              <span>
                <FolderLock /> Saved locally with reproducible metadata
              </span>
              <span>{isFlux || poseId ? '768 × 1024' : '832 × 1216'}</span>
            </footer>
          </Panel>
          <Panel className="prompt-panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">Direction</span>
                <h2>What should the frame feel like?</h2>
              </div>
              <span className="word-count">{prompt.length} / 2,000</span>
            </div>
            <label htmlFor="prompt-direction">Describe anything beyond the recipe</label>
            <textarea
              id="prompt-direction"
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              maxLength={2000}
            />
            <div className="tag-row">
              {['Cinematic', 'Film grain', '35mm', 'Low light', 'Shallow depth'].map((tag) => (
                <button
                  key={tag}
                  className={tags.includes(tag) ? 'tag active' : 'tag'}
                  onClick={() => toggleTag(tag)}
                  aria-pressed={tags.includes(tag)}
                >
                  {tags.includes(tag) && <Check />}
                  {tag}
                </button>
              ))}
              <button
                className="tag"
                onClick={() => {
                  const value = window.prompt('Add a short creative tag');
                  if (value?.trim() && !tags.includes(value.trim())) {
                    setTags((current) => [...current, value.trim()]);
                  }
                }}
              >
                <Plus /> Custom tag
              </button>
            </div>
          </Panel>
        </div>
        <div className="recipe-column">
          <Panel className="recipe-panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">Scene recipe</span>
                <h2>{recipeName}</h2>
              </div>
              <MoreHorizontal />
            </div>
            <div className="form-grid">
              <label>
                Saved recipe
                <select
                  value={recipeId}
                  onChange={(event) => {
                    const next = data.recipes.find((item) => item.id === event.target.value);
                    if (next) applyRecipe(next);
                    else setRecipeId('');
                  }}
                >
                  <option value="">New recipe</option>
                  {data.recipes.map((recipe) => (
                    <option key={recipe.id} value={recipe.id}>
                      {recipe.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Recipe name
                <input value={recipeName} onChange={(event) => setRecipeName(event.target.value)} />
              </label>
            </div>
            <label>
              Character
              <select
                value={characterId}
                onChange={(event) => setCharacterId(event.target.value)}
                disabled={!data.characters.length}
              >
                {data.characters.map((character) => (
                  <option key={character.id} value={character.id}>
                    {character.name} — default identity
                  </option>
                ))}
              </select>
            </label>
            <label>
              Model profile
              <select
                value={modelAlias}
                onChange={(event) => {
                  const next = event.target.value;
                  setModelAlias(next);
                  if (next === 'photoreal_max') {
                    setSteps(20);
                    setGuidance(3.5);
                    setSourceGenerationId(null);
                    setPoseId('');
                  } else {
                    setSteps(30);
                    setGuidance(5.5);
                  }
                }}
              >
                {selectableModels.map((item) => (
                  <option key={item.alias} value={item.alias}>
                    {item.display_name}
                  </option>
                ))}
              </select>
              <small>
                {isFlux
                  ? 'Maximum detail · native FLUX · 768 × 1024 hardware-safe profile'
                  : 'Balanced supports identity, pose, variations, and inpainting.'}
              </small>
            </label>
            <div className="form-grid">
              {categories.map((category) => (
                <label key={category}>
                  {category}
                  <select
                    value={selected[category]}
                    onChange={(event) =>
                      setSelected((current) => ({ ...current, [category]: event.target.value }))
                    }
                  >
                    <option value="">Vanta default</option>
                    {options(category).map((preset) => (
                      <option key={preset.id} value={preset.id}>
                        {preset.name}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
            {sourceGenerationId && !isFlux && (
              <div className="variation-control">
                <div className="section-rule">
                  <span>Controlled variation</span>
                </div>
                <p className="field-help">
                  Derivative of {sourceGenerationId}. The original remains untouched.
                </p>
                <label>
                  Variation goal
                  <select
                    value={variationMode}
                    onChange={(event) => {
                      const next = event.target.value;
                      setVariationMode(next);
                      setVariationPrompt(variationDefaults[next] ?? '');
                    }}
                  >
                    {variationModes.map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <small>{variationModes.find(([value]) => value === variationMode)?.[2]}</small>
                <label>
                  Change prompt
                  <textarea
                    value={variationPrompt}
                    onChange={(event) => setVariationPrompt(event.target.value)}
                    placeholder="Describe only what should change"
                  />
                </label>
                <label>
                  Denoise strength <span>{variationStrength.toFixed(2)}</span>
                  <input
                    type="range"
                    min={0.05}
                    max={0.95}
                    step={0.05}
                    value={variationStrength}
                    onChange={(event) => setVariationStrength(Number(event.target.value))}
                  />
                </label>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => {
                    setSourceGenerationId(null);
                    setVariationMode('general');
                    setVariationPrompt('');
                  }}
                >
                  <X /> Leave variation mode
                </Button>
              </div>
            )}
            <div className="pose-create-control">
              <label>
                Pose Control
                <select
                  value={poseId}
                  onChange={(event) => setPoseId(event.target.value)}
                  disabled={isFlux}
                >
                  <option value="">No saved pose</option>
                  {availablePoses.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                      {item.character_id ? ' — character pose' : ' — global'}
                    </option>
                  ))}
                </select>
              </label>
              {isFlux && (
                <p className="field-help">
                  Pose, identity, variations, and inpainting use the Balanced SDXL profile.
                </p>
              )}
              {poseId && (
                <>
                  <label>
                    Pose strength <span>{poseStrength.toFixed(2)}</span>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={poseStrength}
                      onChange={(event) => setPoseStrength(Number(event.target.value))}
                    />
                  </label>
                  <p className="field-help">12 GB safe canvas · 768 × 1024</p>
                </>
              )}
              <Button type="button" variant="ghost" onClick={goPoses}>
                <Plus /> Extract new pose
              </Button>
            </div>
            {mode === 'studio' && (
              <div className="studio-controls">
                <div className="section-rule">
                  <span>Studio controls</span>
                </div>
                <div className="form-grid">
                  <label>
                    Steps
                    <input
                      type="number"
                      value={steps}
                      min={1}
                      max={60}
                      onChange={(event) => setSteps(Number(event.target.value))}
                    />
                  </label>
                  <label>
                    Guidance
                    <input
                      type="number"
                      value={guidance}
                      min={1}
                      max={15}
                      step={0.5}
                      onChange={(event) => setGuidance(Number(event.target.value))}
                    />
                  </label>
                  <label>
                    Seed
                    <input
                      type="number"
                      value={seed}
                      min={0}
                      onChange={(event) => setSeed(Number(event.target.value))}
                    />
                  </label>
                  <label>
                    Sampler
                    <select
                      value={isFlux ? 'euler' : sampler}
                      disabled={isFlux}
                      onChange={(event) => setSampler(event.target.value)}
                    >
                      <option value="euler">Euler</option>
                      <option value="euler_ancestral">Euler ancestral</option>
                      <option value="dpmpp_2m">DPM++ 2M</option>
                      <option value="dpmpp_sde">DPM++ SDE</option>
                    </select>
                  </label>
                  <label>
                    Scheduler
                    <select
                      value={scheduler}
                      onChange={(event) => setScheduler(event.target.value)}
                      disabled={isFlux}
                    >
                      <option value="normal">Normal</option>
                      <option value="karras">Karras</option>
                      <option value="simple">Simple</option>
                    </select>
                  </label>
                  <label>
                    Width
                    <input
                      type="number"
                      min={512}
                      max={1536}
                      step={64}
                      value={width}
                      disabled={Boolean(poseId) || isFlux}
                      onChange={(event) => setWidth(Number(event.target.value))}
                    />
                  </label>
                  <label>
                    Height
                    <input
                      type="number"
                      min={512}
                      max={1536}
                      step={64}
                      value={height}
                      disabled={Boolean(poseId) || isFlux}
                      onChange={(event) => setHeight(Number(event.target.value))}
                    />
                  </label>
                </div>
                <div className="section-rule">
                  <span>Identity & LoRA stack</span>
                </div>
                <div className="form-grid">
                  <label>
                    Identity reference
                    <select
                      value={identityReferenceId}
                      onChange={(event) => setIdentityReferenceId(event.target.value)}
                      disabled={isFlux}
                    >
                      <option value="">Character primary reference</option>
                      {(
                        data.characters.find((item) => item.id === characterId)?.references ?? []
                      ).map((reference, index) => (
                        <option key={reference.id} value={reference.id}>
                          Reference {index + 1}
                          {reference.is_primary ? ' - primary' : ''}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Identity strength <span>{identityStrength.toFixed(2)}</span>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={identityStrength}
                      disabled={isFlux}
                      onChange={(event) => setIdentityStrength(Number(event.target.value))}
                    />
                  </label>
                </div>
                <div className="recipe-check-list">
                  {data.loras
                    .filter(
                      (item) => item.enabled && item.model_family === (isFlux ? 'FLUX' : 'SDXL'),
                    )
                    .map((item) => (
                      <div className="lora-stack-item" key={item.id}>
                        <label>
                          <input
                            type="checkbox"
                            checked={loraIds.includes(item.id)}
                            onChange={() =>
                              setLoraIds((current) =>
                                current.includes(item.id)
                                  ? current.filter((id) => id !== item.id)
                                  : [...current, item.id],
                              )
                            }
                          />
                          <span>
                            <strong>{item.name}</strong>
                            <small>{item.trigger_token || item.filename}</small>
                          </span>
                        </label>
                        {loraIds.includes(item.id) && (
                          <div className="form-grid">
                            <label>
                              Model weight {Number(loraStrengths[item.id] ?? 1).toFixed(2)}
                              <input
                                type="range"
                                min={0}
                                max={2}
                                step={0.05}
                                value={loraStrengths[item.id] ?? item.default_strength}
                                onChange={(event) =>
                                  setLoraStrengths((current) => ({
                                    ...current,
                                    [item.id]: Number(event.target.value),
                                  }))
                                }
                              />
                            </label>
                            <label>
                              Text weight {Number(loraClipStrengths[item.id] ?? 1).toFixed(2)}
                              <input
                                type="range"
                                min={0}
                                max={2}
                                step={0.05}
                                value={loraClipStrengths[item.id] ?? item.default_clip_strength}
                                onChange={(event) =>
                                  setLoraClipStrengths((current) => ({
                                    ...current,
                                    [item.id]: Number(event.target.value),
                                  }))
                                }
                              />
                            </label>
                          </div>
                        )}
                      </div>
                    ))}
                </div>
                <div className="section-rule">
                  <span>Saved video direction</span>
                </div>
                <div className="form-grid">
                  <label>
                    Video profile
                    <select
                      value={videoProfile}
                      onChange={(event) => setVideoProfile(event.target.value)}
                    >
                      <option value="safe">Safe</option>
                      <option value="balanced">Balanced</option>
                      <option value="quality">Quality</option>
                    </select>
                  </label>
                  <label>
                    Duration
                    <select
                      value={videoDuration}
                      onChange={(event) => setVideoDuration(Number(event.target.value))}
                    >
                      <option value={2}>2 seconds</option>
                      <option value={3}>3 seconds</option>
                      <option value={4}>4 seconds</option>
                    </select>
                  </label>
                </div>
                <label>
                  Motion prompt
                  <textarea
                    value={motionPrompt}
                    onChange={(event) => setMotionPrompt(event.target.value)}
                  />
                </label>
                <label>
                  Negative prompt
                  <textarea
                    value={negativePrompt}
                    onChange={(event) => setNegativePrompt(event.target.value)}
                  />
                </label>
                <div className="metadata-section">
                  <span className="eyebrow">Compiled prompt</span>
                  <p>
                    {[
                      generationRequest().character_identity,
                      generationRequest().wardrobe,
                      generationRequest().expression,
                      generationRequest().pose,
                      generationRequest().location,
                      generationRequest().lighting,
                      generationRequest().camera,
                      generationRequest().quality,
                      generationRequest().direction,
                      ...generationRequest().custom_tags,
                    ]
                      .filter(Boolean)
                      .join(', ') || 'Add a direction to generate.'}
                  </p>
                </div>
              </div>
            )}
            <div className="recipe-status">
              <div>
                <span>
                  <strong>Image engine</strong>
                  <small>Managed local ComfyUI runtime</small>
                </span>
                <StatusPill tone={runtime?.state === 'ready' ? 'ready' : 'warning'}>
                  {stateLabels[runtime?.state ?? 'not_installed']}
                </StatusPill>
              </div>
              <div>
                <span>
                  <strong>Model pack</strong>
                  <small>photoreal_balanced</small>
                </span>
                <StatusPill tone={model?.verified ? 'ready' : 'warning'}>
                  {model?.verified ? 'Verified' : 'Needs import'}
                </StatusPill>
              </div>
              <div>
                <span>
                  <strong>Quality profile</strong>
                  <small>Realistic — Balanced</small>
                </span>
                <StatusPill tone="accent">12 GB fit</StatusPill>
              </div>
            </div>
          </Panel>
          <Panel className="library-note">
            <Sparkles />
            <div>
              <h3>Your library, not a locked list</h3>
              <p>
                Every starter can become your own editable copy. Built-ins always remain
                recoverable.
              </p>
            </div>
            <ChevronRight />
          </Panel>
        </div>
      </div>
    </div>
  );
}

function CharactersScreen({
  items,
  loras,
  refresh,
  notify,
}: {
  items: CharacterRecord[];
  loras: LoraRecord[];
  refresh: () => Promise<void>;
  notify: (message: string) => void;
}) {
  const [editing, setEditing] = useState<CharacterRecord | 'new' | null>(null);
  const [working, setWorking] = useState(false);
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setWorking(true);
    const form = new FormData(event.currentTarget);
    const payload = {
      name: String(form.get('name')),
      identity_description: String(form.get('identity')),
      default_model_profile: String(form.get('model')),
      hair: String(form.get('hair')),
      eyes: String(form.get('eyes')),
      facial_features: String(form.get('facial_features')),
      distinguishing_features: String(form.get('distinguishing_features')),
      style_notes: String(form.get('style_notes')),
      body_notes: String(form.get('body_notes')),
      default_negative_prompt: String(form.get('default_negative_prompt')),
      reference_assets: editing === 'new' ? [] : (editing?.reference_assets ?? []),
    };
    try {
      if (editing === 'new') await api.post('/characters', payload);
      else if (editing) await api.put(`/characters/${editing.id}`, payload);
      notify(editing === 'new' ? 'Character created locally' : 'Character profile updated');
      setEditing(null);
      await refresh();
    } finally {
      setWorking(false);
    }
  };
  const archive = async (item: CharacterRecord) => {
    if (
      !window.confirm(
        `Archive ${item.name}? You can preserve and restore the profile from the local database.`,
      )
    )
      return;
    await api.delete(`/characters/${item.id}`);
    notify(`${item.name} archived`);
    await refresh();
  };
  const importReference = async (item: CharacterRecord) => {
    const sourcePath = await chooseLocalImageFile();
    if (!sourcePath) return;
    await api.post(`/characters/${item.id}/references`, { source_path: sourcePath });
    notify(`Reference saved to ${item.name}'s local library`);
    await refresh();
  };
  const importLora = async (item: CharacterRecord) => {
    const sourcePath = await chooseLocalLoraFile();
    if (!sourcePath) return;
    const filename =
      sourcePath
        .split(/[\\/]/)
        .pop()
        ?.replace(/\.safetensors$/i, '') ?? 'Local LoRA';
    const imported = await api.post<LoraRecord>('/loras/import', {
      source_path: sourcePath,
      name: filename,
      source_notes: 'Imported from a user-selected local file',
      license_notes: 'User-selected LoRA; redistribution license not reviewed by Vanta.',
    });
    await api.put(`/characters/${item.id}/loras`, {
      lora_id: imported.id,
      position: item.loras.length,
      strength: imported.default_strength,
      clip_strength: imported.default_clip_strength,
      enabled: true,
    });
    notify(`Verified local LoRA assigned to ${item.name}`);
    await refresh();
  };
  return (
    <div className="screen">
      <PageTitle
        eyebrow="Identity library"
        title="Originals, held consistently."
        body="Build reusable identities from references you own."
        actions={
          <Button variant="primary" onClick={() => setEditing('new')}>
            <Plus /> New character
          </Button>
        }
      />
      <p className="helper character-library-status">
        {loras.length
          ? `${loras.length} verified local LoRA${loras.length === 1 ? '' : 's'} available in this studio.`
          : 'Import a compatible local SDXL or FLUX LoRA from a character card when you are ready.'}
      </p>
      {items.length === 0 ? (
        <EmptyState
          title="No characters yet"
          body="Create an original adult character to begin directing scenes."
          action={
            <Button variant="primary" onClick={() => setEditing('new')}>
              Create character
            </Button>
          }
        />
      ) : (
        <div className="character-grid">
          {items.map((item, index) => (
            <Panel className="character-card" key={item.id}>
              <div className={`character-portrait character-portrait--${index % 3}`}>
                {item.references[0] ? (
                  <LocalReferenceImage
                    referenceId={item.references[0].id}
                    alt={`${item.name} reference`}
                  />
                ) : (
                  <div className="portrait-silhouette" />
                )}
                <span>Original · Adult</span>
              </div>
              <div className="character-card__body">
                <div>
                  <span className="eyebrow">Identity profile</span>
                  <h2>{item.name}</h2>
                </div>
                <p>{item.identity_description}</p>
                <div className="asset-strip">
                  {item.references.map((reference, assetIndex) => (
                    <span key={reference.id}>
                      <span>{String(assetIndex + 1).padStart(2, '0')}</span>
                      {reference.is_primary ? 'Primary reference' : `Reference ${assetIndex + 1}`}
                    </span>
                  ))}
                  {!item.references.length && (
                    <span className="asset-empty">
                      <Plus /> No references yet
                    </span>
                  )}
                </div>
                <div className="character-meta">
                  <span>
                    <Box /> {item.default_model_profile}
                  </span>
                  <span>
                    <Image /> {item.references.length} references
                  </span>
                  <span>
                    <Zap /> {item.loras.length} LoRAs
                  </span>
                </div>
                <footer>
                  <Button onClick={() => setEditing(item)}>
                    <Edit3 /> Edit profile
                  </Button>
                  <button
                    className="icon-button"
                    onClick={() => void importReference(item)}
                    aria-label={`Add reference to ${item.name}`}
                    title="Add local reference"
                  >
                    <Upload />
                  </button>
                  <button
                    className="icon-button"
                    onClick={() => void importLora(item)}
                    aria-label={`Import LoRA for ${item.name}`}
                    title="Import and assign a compatible local LoRA"
                  >
                    <Zap />
                  </button>
                  <button
                    className="icon-button"
                    onClick={() => void archive(item)}
                    aria-label={`Archive ${item.name}`}
                  >
                    <Archive />
                  </button>
                </footer>
              </div>
            </Panel>
          ))}
        </div>
      )}
      {editing && (
        <div className="modal-layer">
          <form className="modal" onSubmit={(event) => void save(event)}>
            <header>
              <div>
                <span className="eyebrow">
                  {editing === 'new' ? 'New original' : 'Edit identity'}
                </span>
                <h2>{editing === 'new' ? 'Create character' : editing.name}</h2>
              </div>
              <button
                type="button"
                className="icon-button"
                onClick={() => setEditing(null)}
                aria-label="Close"
              >
                <X />
              </button>
            </header>
            <label>
              Name
              <input
                name="name"
                defaultValue={editing === 'new' ? '' : editing.name}
                required
                autoFocus
              />
            </label>
            <label>
              Identity description
              <textarea
                name="identity"
                defaultValue={
                  editing === 'new'
                    ? 'Original adult character, age '
                    : editing.identity_description
                }
                required
              />
            </label>
            <p className="helper">
              Describe stable physical identity only. Use scene presets for wardrobe, expression,
              and setting.
            </p>
            <label>
              Default model profile
              <select
                name="model"
                defaultValue={
                  editing === 'new' ? 'photoreal_balanced' : editing.default_model_profile
                }
              >
                <option value="photoreal_balanced">Realistic — Balanced</option>
                <option value="preview_fast">Preview — Fast</option>
              </select>
            </label>
            <div className="form-grid">
              <label>
                Hair
                <input name="hair" defaultValue={editing === 'new' ? '' : editing.hair} />
              </label>
              <label>
                Eyes
                <input name="eyes" defaultValue={editing === 'new' ? '' : editing.eyes} />
              </label>
            </div>
            <label>
              Facial features
              <textarea
                name="facial_features"
                defaultValue={editing === 'new' ? '' : editing.facial_features}
              />
            </label>
            <label>
              Distinguishing features
              <textarea
                name="distinguishing_features"
                defaultValue={editing === 'new' ? '' : editing.distinguishing_features}
              />
            </label>
            <label>
              Style notes
              <textarea
                name="style_notes"
                defaultValue={editing === 'new' ? '' : editing.style_notes}
              />
            </label>
            <label>
              Body & proportion notes
              <textarea
                name="body_notes"
                defaultValue={editing === 'new' ? '' : editing.body_notes}
              />
            </label>
            <label>
              Default negative prompt
              <textarea
                name="default_negative_prompt"
                defaultValue={editing === 'new' ? '' : editing.default_negative_prompt}
              />
            </label>
            <div className="reference-drop">
              <Upload />
              <strong>Reference assets</strong>
              <span>Local placeholder · add owned portraits in Milestone 2</span>
            </div>
            <footer>
              <Button type="button" variant="ghost" onClick={() => setEditing(null)}>
                Cancel
              </Button>
              <Button variant="primary" type="submit" disabled={working}>
                {working ? 'Saving…' : 'Save character'}
              </Button>
            </footer>
          </form>
        </div>
      )}
    </div>
  );
}

function LocalReferenceImage({ referenceId, alt }: { referenceId: string; alt: string }) {
  return (
    <AuthenticatedImage
      className="character-reference-image"
      media={{ entity: 'character-reference', id: referenceId, variant: 'thumbnail' }}
      fallbackVariant="original"
      placeholderClassName="portrait-silhouette"
      alt={alt}
    />
  );
}

function LocalPoseImage({
  poseId,
  variant,
  alt,
}: {
  poseId: string;
  variant: 'source-thumbnail' | 'control-thumbnail';
  alt: string;
}) {
  return (
    <AuthenticatedImage
      media={{ entity: 'pose', id: poseId, variant }}
      fallbackVariant={variant === 'source-thumbnail' ? 'source' : 'control'}
      placeholderClassName="pose-image-placeholder"
      alt={alt}
    />
  );
}

function PoseLibraryScreen({
  items,
  characters,
  refresh,
  notify,
}: {
  items: PoseRecord[];
  characters: CharacterRecord[];
  refresh: () => Promise<void>;
  notify: (message: string) => void;
}) {
  const [query, setQuery] = useState('');
  const [editing, setEditing] = useState<PoseRecord | 'new' | null>(null);
  const [sourcePath, setSourcePath] = useState('');
  const [working, setWorking] = useState(false);
  const filtered = items.filter((item) =>
    `${item.name} ${item.tags.join(' ')} ${item.notes}`.toLowerCase().includes(query.toLowerCase()),
  );

  useEffect(() => {
    if (!items.some((item) => !['ready', 'failed'].includes(item.status))) return;
    const timer = window.setInterval(() => void refresh(), 900);
    return () => window.clearInterval(timer);
  }, [items, refresh]);

  useEffect(() => {
    if (!('__TAURI_INTERNALS__' in window)) return;
    let unlisten: (() => void) | undefined;
    void getCurrentWindow()
      .onDragDropEvent((event) => {
        if (event.payload.type === 'drop' && event.payload.paths[0]) {
          setSourcePath(event.payload.paths[0]);
          setEditing('new');
        }
      })
      .then((stop) => {
        unlisten = stop;
      });
    return () => unlisten?.();
  }, []);

  const chooseSource = async () => {
    const path = await chooseLocalImageFile();
    if (path) setSourcePath(path);
  };
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const payload = {
      name: String(form.get('name')),
      tags: String(form.get('tags'))
        .split(',')
        .map((tag) => tag.trim())
        .filter(Boolean),
      favorite: form.get('favorite') === 'on',
      notes: String(form.get('notes')),
      character_id: String(form.get('character_id') || '') || null,
      strength: Number(form.get('strength')),
    };
    setWorking(true);
    try {
      if (editing === 'new') {
        await api.post('/poses/import', { ...payload, source_path: sourcePath });
        notify('Pose extraction queued locally');
      } else if (editing) {
        await api.put(`/poses/${editing.id}`, payload);
        notify('Pose details saved');
      }
      setEditing(null);
      setSourcePath('');
      await refresh();
    } finally {
      setWorking(false);
    }
  };
  return (
    <div className="screen pose-library-screen">
      <PageTitle
        eyebrow="Pose library"
        title="Movement, held in place."
        body="Import references you have rights to use. Vanta extracts broad body structure locally."
        actions={
          <Button variant="primary" onClick={() => setEditing('new')}>
            <Plus /> Import pose
          </Button>
        }
      />
      <div className="library-toolbar">
        <div className="search-field">
          <Search />
          <input
            aria-label="Search poses"
            placeholder="Search names, notes, and tags"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      </div>
      {filtered.length === 0 ? (
        <EmptyState
          title="No poses yet"
          body="Import an owned reference image or drag one into Vanta to extract a reusable control pose."
          action={<Button onClick={() => setEditing('new')}>Import first pose</Button>}
        />
      ) : (
        <div className="pose-grid">
          {filtered.map((item) => (
            <Panel className="pose-card" key={item.id}>
              <div className="pose-comparison">
                <div>
                  <LocalPoseImage
                    poseId={item.id}
                    variant="source-thumbnail"
                    alt={`${item.name} original reference`}
                  />
                  <span>Original</span>
                </div>
                <div>
                  <LocalPoseImage
                    poseId={item.id}
                    variant="control-thumbnail"
                    alt={`${item.name} extracted pose control`}
                  />
                  <span>Control</span>
                </div>
              </div>
              <header>
                <div>
                  <span className="eyebrow">{item.scope}</span>
                  <h2>{item.name}</h2>
                  {item.character_id && (
                    <span className="pose-character-name">
                      {characters.find((character) => character.id === item.character_id)?.name ??
                        'Archived character'}
                    </span>
                  )}
                </div>
                <StatusPill
                  tone={
                    item.status === 'ready'
                      ? 'ready'
                      : item.status === 'failed'
                        ? 'danger'
                        : 'warning'
                  }
                >
                  {item.status === 'ready'
                    ? 'Ready'
                    : item.status === 'failed'
                      ? 'Needs attention'
                      : `${item.progress}%`}
                </StatusPill>
              </header>
              {!['ready', 'failed'].includes(item.status) && (
                <div className="progress">
                  <span style={{ width: `${item.progress}%` }} />
                </div>
              )}
              {item.error_message && <p className="inline-error">{item.error_message}</p>}
              <p>{item.notes || 'No notes'}</p>
              {item.favorite && (
                <span className="pose-favorite">
                  <Heart fill="currentColor" /> Favorite
                </span>
              )}
              <div className="tag-row">
                {item.tags.map((tag) => (
                  <span className="tag" key={tag}>
                    {tag}
                  </span>
                ))}
              </div>
              <footer>
                <Button variant="ghost" onClick={() => setEditing(item)}>
                  <Edit3 /> Edit
                </Button>
                <button
                  className="icon-button"
                  aria-label={`Duplicate ${item.name}`}
                  onClick={async () => {
                    await api.post(`/poses/${item.id}/duplicate`);
                    notify('Pose duplicated and queued for extraction');
                    await refresh();
                  }}
                >
                  <Copy />
                </button>
                <button
                  className="icon-button danger"
                  aria-label={`Delete ${item.name}`}
                  onClick={async () => {
                    if (!window.confirm(`Delete ${item.name}?`)) return;
                    await api.delete(`/poses/${item.id}`);
                    mediaCache.invalidateEntity('pose', item.id);
                    await refresh();
                    notify('Pose deleted');
                  }}
                >
                  <Trash2 />
                </button>
              </footer>
            </Panel>
          ))}
        </div>
      )}
      {editing && (
        <div className="modal-layer">
          <form className="modal modal--wide" onSubmit={(event) => void save(event)}>
            <header>
              <div>
                <span className="eyebrow">
                  {editing === 'new' ? 'Local extraction' : 'Edit saved pose'}
                </span>
                <h2>{editing === 'new' ? 'Import pose reference' : editing.name}</h2>
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label="Close"
                onClick={() => setEditing(null)}
              >
                <X />
              </button>
            </header>
            {editing === 'new' && (
              <div className="pose-drop-zone" onDragOver={(event) => event.preventDefault()}>
                <Upload />
                <strong>{sourcePath || 'Choose or drop an image'}</strong>
                <span>PNG, JPEG, or WebP · at least 256 px per side</span>
                <Button type="button" onClick={() => void chooseSource()}>
                  Choose image
                </Button>
              </div>
            )}
            <div className="form-grid">
              <label>
                Name
                <input name="name" required defaultValue={editing === 'new' ? '' : editing.name} />
              </label>
              <label>
                Tags
                <input
                  name="tags"
                  defaultValue={editing === 'new' ? '' : editing.tags.join(', ')}
                />
              </label>
              <label>
                Scope
                <select
                  name="character_id"
                  defaultValue={editing === 'new' ? '' : (editing.character_id ?? '')}
                >
                  <option value="">Global — available to every character</option>
                  {characters.map((character) => (
                    <option key={character.id} value={character.id}>
                      {character.name} — character only
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label>
              Notes
              <textarea name="notes" defaultValue={editing === 'new' ? '' : editing.notes} />
            </label>
            <label>
              Default strength
              <input
                name="strength"
                type="range"
                min={0}
                max={1}
                step={0.05}
                defaultValue={editing === 'new' ? 0.8 : editing.strength}
              />
            </label>
            <label className="checkbox-row">
              <input
                name="favorite"
                type="checkbox"
                defaultChecked={editing !== 'new' && editing.favorite}
              />{' '}
              Favorite
            </label>
            <footer>
              <Button type="button" variant="ghost" onClick={() => setEditing(null)}>
                Cancel
              </Button>
              <Button
                type="submit"
                variant="primary"
                disabled={working || (editing === 'new' && !sourcePath)}
              >
                {working ? 'Saving…' : editing === 'new' ? 'Extract pose' : 'Save pose'}
              </Button>
            </footer>
          </form>
        </div>
      )}
    </div>
  );
}

function LocalMotionMedia({
  motionId,
  variant,
  label,
}: {
  motionId: string;
  variant: 'preview' | 'thumbnail';
  label: string;
}) {
  return variant === 'thumbnail' ? (
    <AuthenticatedImage
      media={{ entity: 'motion', id: motionId, variant }}
      placeholderClassName="motion-placeholder"
      alt={label}
      loading="lazy"
    />
  ) : (
    <AuthenticatedVideo
      media={{ entity: 'motion', id: motionId, variant }}
      placeholderClassName="motion-placeholder"
      aria-label={label}
      controls
      playsInline
      preload="metadata"
    />
  );
}

function MotionLibraryScreen({
  items,
  refresh,
  notify,
}: {
  items: MotionAsset[];
  refresh: () => Promise<void>;
  notify: (message: string) => void;
}) {
  const [query, setQuery] = useState('');
  const [editing, setEditing] = useState<MotionAsset | 'new' | null>(null);
  const [sourcePath, setSourcePath] = useState('');
  const [working, setWorking] = useState(false);
  const [formError, setFormError] = useState('');
  const filtered = items.filter((item) =>
    `${item.name} ${item.metadata.broad_motion_prompt ?? ''}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );

  useEffect(() => {
    if (!items.some((item) => !['ready', 'failed'].includes(item.status))) return;
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => window.clearInterval(timer);
  }, [items, refresh]);

  useEffect(() => {
    if (!('__TAURI_INTERNALS__' in window)) return;
    let unlisten: (() => void) | undefined;
    void getCurrentWindow()
      .onDragDropEvent((event) => {
        if (event.payload.type !== 'drop') return;
        const path = event.payload.paths.find((candidate) =>
          /\.(mp4|mov|webm|mkv)$/i.test(candidate),
        );
        if (path) {
          setSourcePath(path);
          setEditing('new');
          setFormError('');
        }
      })
      .then((stop) => {
        unlisten = stop;
      });
    return () => unlisten?.();
  }, []);

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setWorking(true);
    setFormError('');
    const form = new FormData(event.currentTarget);
    const payload = {
      name: String(form.get('name')),
      start_seconds: Number(form.get('start_seconds')),
      end_seconds: Number(form.get('end_seconds')),
      fit_mode: String(form.get('fit_mode')),
      smoothing: Number(form.get('smoothing')),
      strength: Number(form.get('strength')),
    };
    try {
      if (editing === 'new') {
        await api.post('/motion-assets', {
          ...payload,
          source_path: sourcePath,
          rights_confirmed: form.get('rights_confirmed') === 'on',
        });
        notify('Reference Motion extraction queued locally');
      } else if (editing) {
        await api.put(`/motion-assets/${editing.id}`, payload);
        notify('Reference Motion updated and queued for re-extraction');
      }
      setEditing(null);
      setSourcePath('');
      await refresh();
    } catch (caught) {
      setFormError(caught instanceof Error ? caught.message : 'Motion import could not start.');
    } finally {
      setWorking(false);
    }
  };

  return (
    <div className="screen motion-library-screen">
      <PageTitle
        eyebrow="Reference Motion"
        title="Borrow movement, never identity."
        body="Import an owned source up to two minutes, then select a four-second-or-shorter motion segment. Vanta extracts broad pose movement locally and excludes face, voice, branding, and reference-person identity."
        actions={
          <Button variant="primary" onClick={() => setEditing('new')}>
            <Plus /> Import motion
          </Button>
        }
      />
      <div className="library-toolbar">
        <div className="search-field">
          <Search />
          <input
            aria-label="Search motion references"
            placeholder="Search motion names and movement"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      </div>
      {filtered.length === 0 ? (
        <EmptyState
          title="No motion references yet"
          body="Import an owned MP4, MOV, WebM, or MKV to create a reusable, identity-safe movement guide."
          action={<Button onClick={() => setEditing('new')}>Import first motion</Button>}
        />
      ) : (
        <div className="motion-grid">
          {filtered.map((item) => (
            <Panel className="motion-card" key={item.id}>
              <div className="motion-preview">
                {item.status === 'ready' ? (
                  <LocalMotionMedia
                    motionId={item.id}
                    variant="preview"
                    label={`${item.name} extracted pose movement preview`}
                  />
                ) : (
                  <div className="motion-placeholder">
                    <Film />
                  </div>
                )}
              </div>
              <header>
                <div>
                  <span className="eyebrow">
                    {(item.end_seconds - item.start_seconds).toFixed(1)}s trim
                  </span>
                  <h2>{item.name}</h2>
                </div>
                <StatusPill
                  tone={
                    item.status === 'ready'
                      ? 'ready'
                      : item.status === 'failed'
                        ? 'danger'
                        : 'warning'
                  }
                >
                  {item.status === 'ready'
                    ? 'Ready'
                    : item.status === 'failed'
                      ? 'Needs attention'
                      : `${stateLabels[item.status] ?? item.status} ${item.progress}%`}
                </StatusPill>
              </header>
              {!['ready', 'failed'].includes(item.status) && (
                <div className="progress" role="progressbar" aria-valuenow={item.progress}>
                  <span style={{ width: `${item.progress}%` }} />
                </div>
              )}
              {item.error_message && <p className="inline-error">{item.error_message}</p>}
              <p>
                {item.metadata.broad_motion_prompt ?? 'Extracting broad body movement locally.'}
              </p>
              <small className="motion-policy">
                No face, audio, identity, or branding transfer
              </small>
              <footer>
                <Button variant="ghost" onClick={() => setEditing(item)}>
                  <Edit3 /> Edit & re-extract
                </Button>
                <button
                  className="icon-button danger"
                  aria-label={`Delete ${item.name}`}
                  onClick={async () => {
                    if (!window.confirm(`Delete ${item.name}?`)) return;
                    await api.delete(`/motion-assets/${item.id}`);
                    mediaCache.invalidateEntity('motion', item.id);
                    await refresh();
                    notify('Motion reference deleted');
                  }}
                >
                  <Trash2 />
                </button>
              </footer>
            </Panel>
          ))}
        </div>
      )}
      {editing && (
        <div className="modal-layer">
          <form className="modal modal--wide motion-form" onSubmit={(event) => void save(event)}>
            <header>
              <div>
                <span className="eyebrow">Identity-safe local extraction</span>
                <h2>{editing === 'new' ? 'Import motion reference' : editing.name}</h2>
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label="Close"
                onClick={() => setEditing(null)}
              >
                <X />
              </button>
            </header>
            {editing === 'new' && (
              <div className="pose-drop-zone">
                <Film />
                <strong>{sourcePath || 'Choose or drop a video'}</strong>
                <span>
                  MP4, MOV, WebM, or MKV · sources up to two minutes stay inside Vanta storage
                </span>
                <Button
                  type="button"
                  onClick={async () => {
                    const path = await chooseLocalVideoFile();
                    if (path) setSourcePath(path);
                  }}
                >
                  Choose video
                </Button>
              </div>
            )}
            <div className="form-grid">
              <label>
                Name
                <input name="name" required defaultValue={editing === 'new' ? '' : editing.name} />
              </label>
              <label>
                Fit
                <select
                  name="fit_mode"
                  defaultValue={editing === 'new' ? 'crop' : editing.fit_mode}
                >
                  <option value="crop">Crop to square</option>
                  <option value="fit">Fit with letterbox</option>
                </select>
              </label>
              <label>
                Trim start (seconds)
                <input
                  name="start_seconds"
                  type="number"
                  min={0}
                  max={120}
                  step={0.1}
                  required
                  defaultValue={editing === 'new' ? 0 : editing.start_seconds}
                />
              </label>
              <label>
                Trim end (seconds)
                <input
                  name="end_seconds"
                  type="number"
                  min={0.1}
                  max={120}
                  step={0.1}
                  required
                  defaultValue={editing === 'new' ? 2 : editing.end_seconds}
                />
                <small>Maximum four-second selection.</small>
              </label>
            </div>
            <label>
              Temporal smoothing
              <input
                name="smoothing"
                type="range"
                min={0}
                max={1}
                step={0.05}
                defaultValue={editing === 'new' ? 0.5 : editing.smoothing}
              />
            </label>
            <label>
              Default motion strength
              <input
                name="strength"
                type="range"
                min={0}
                max={1}
                step={0.05}
                defaultValue={editing === 'new' ? 0.65 : editing.strength}
              />
            </label>
            {editing === 'new' && (
              <label className="checkbox-row rights-confirmation">
                <input name="rights_confirmed" type="checkbox" required />I have the rights and
                consent needed to use this motion reference.
              </label>
            )}
            <p className="field-help">
              Only broad pose movement is retained. Face landmarks are disabled and audio is never
              extracted.
            </p>
            {formError && (
              <p className="inline-error" role="alert">
                {formError}
              </p>
            )}
            <footer>
              <Button type="button" variant="ghost" onClick={() => setEditing(null)}>
                Cancel
              </Button>
              <Button
                type="submit"
                variant="primary"
                disabled={working || (editing === 'new' && !sourcePath)}
              >
                {working
                  ? 'Queueing…'
                  : editing === 'new'
                    ? 'Extract movement'
                    : 'Save & re-extract'}
              </Button>
            </footer>
          </form>
        </div>
      )}
    </div>
  );
}

const presetCategories = [
  'all',
  'identity_modifier',
  'wardrobe',
  'expression',
  'pose',
  'location',
  'lighting',
  'camera',
  'quality',
  'negative',
  'motion',
];
function LocalTrainingImage({
  imageId,
  alt,
  variant = 'thumbnail',
}: {
  imageId: string;
  alt: string;
  variant?: 'image' | 'thumbnail';
}) {
  return (
    <AuthenticatedImage
      media={{
        entity: 'training-image',
        id: imageId,
        variant: variant === 'image' ? 'original' : variant,
      }}
      fallbackVariant={variant === 'thumbnail' ? 'original' : undefined}
      placeholderClassName="training-image-placeholder"
      alt={alt}
    />
  );
}

function TrainingValidationImage({ checkpointId }: { checkpointId: string }) {
  return (
    <AuthenticatedImage
      media={{ entity: 'training-validation', id: checkpointId, variant: 'sample' }}
      placeholderClassName="training-image-placeholder"
      alt="Local checkpoint validation sample"
    />
  );
}

function TrainingScreen({
  data,
  refresh,
  notify,
  goEngine,
}: {
  data: AppData;
  refresh: () => Promise<void>;
  notify: (message: string) => void;
  goEngine: () => void;
}) {
  const [selectedId, setSelectedId] = useState(data.trainingDatasets[0]?.id ?? '');
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState('');
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [profile, setProfile] = useState<'safe_12gb' | 'balanced_12gb'>('safe_12gb');
  const [epochs, setEpochs] = useState(4);
  const [validationPrompt, setValidationPrompt] = useState('');
  const [installCharacterId, setInstallCharacterId] = useState('');
  const [runFilter, setRunFilter] = useState<'current' | 'completed' | 'failed' | 'all'>('all');
  const [runDatasetFilter, setRunDatasetFilter] = useState(data.trainingDatasets[0]?.id ?? 'all');
  const [technicalDetails, setTechnicalDetails] = useState<
    Record<string, { content: string; truncated: boolean }>
  >({});
  const [loadingDetails, setLoadingDetails] = useState('');
  const selected =
    data.trainingDatasets.find((item) => item.id === selectedId) ?? data.trainingDatasets[0];
  const trainerReady =
    data.components.find((item) => item.id === 'lora-training')?.state === 'ready';
  const captionReady = data.components.find((item) => item.id === 'captioning')?.state === 'ready';
  const activeRun = data.trainingRuns.find((run) =>
    ['queued', 'preparing', 'training', 'cancelling'].includes(run.status),
  );
  const visibleRuns = data.trainingRuns.filter((run) => {
    if (runDatasetFilter !== 'all' && run.dataset_id !== runDatasetFilter) return false;
    if (runFilter === 'current')
      return ['queued', 'preparing', 'training', 'cancelling'].includes(run.status);
    if (runFilter === 'completed') return run.status === 'completed';
    if (runFilter === 'failed') return run.status === 'failed';
    return true;
  });

  useEffect(() => {
    if (selected && !selectedId) setSelectedId(selected.id);
    if (selected?.character_id) setInstallCharacterId(selected.character_id);
  }, [selected, selectedId]);

  const createDataset = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy('create');
    try {
      const dataset = await api.post<TrainingDataset>('/training/datasets', {
        name: form.get('name'),
        character_id: form.get('character_id') || null,
        trigger_token: form.get('trigger_token'),
        model_alias: form.get('model_alias'),
        notes: form.get('notes'),
      });
      setSelectedId(dataset.id);
      setCreating(false);
      notify('Private local training dataset created');
      await refresh();
    } finally {
      setBusy('');
    }
  };

  const importImages = async () => {
    if (!selected || !rightsConfirmed) return;
    const sourcePaths = await chooseLocalTrainingImages();
    if (!sourcePaths.length) return;
    setBusy('import');
    try {
      const result = await api.post<{ accepted: string[]; rejected: unknown[] }>(
        `/training/datasets/${selected.id}/images`,
        { source_paths: sourcePaths, rights_confirmed: true },
      );
      notify(
        `${result.accepted.length} owned image${result.accepted.length === 1 ? '' : 's'} checked and imported${result.rejected.length ? ` · ${result.rejected.length} rejected` : ''}`,
      );
      await refresh();
    } finally {
      setBusy('');
    }
  };

  const autoCaption = async () => {
    if (!selected) return;
    setBusy('caption');
    try {
      await api.post(`/training/datasets/${selected.id}/caption`);
      notify('Local captions generated · review every caption before training');
      await refresh();
    } finally {
      setBusy('');
    }
  };

  const startTraining = async () => {
    if (!selected) return;
    setBusy('train');
    try {
      await api.post('/training/runs', {
        dataset_id: selected.id,
        profile,
        epochs,
        validation_prompt: validationPrompt,
      });
      notify('Local LoRA training queued on this GPU');
      await refresh();
    } finally {
      setBusy('');
    }
  };

  const profileConfig = selected?.profiles[profile] ?? {
    display_name: profile === 'safe_12gb' ? 'Safe 12 GB' : 'Balanced 12 GB',
    resolution: profile === 'safe_12gb' ? 512 : 768,
    rank: profile === 'safe_12gb' ? 4 : 8,
    repeats: profile === 'safe_12gb' ? 4 : 6,
    vram_gb: profile === 'safe_12gb' ? 10.5 : 11.7,
    disk_gb: profile === 'safe_12gb' ? 2.2 : 3.8,
  };
  const estimatedSteps = selected
    ? Math.max(
        1,
        Math.ceil(
          (selected.image_count * profileConfig.repeats * epochs) /
            (profile === 'balanced_12gb' ? 2 : 1),
        ),
      )
    : 0;
  const estimatedMinutes = Math.ceil(
    (estimatedSteps * (profile === 'balanced_12gb' ? 28 : 18) + 240) / 60,
  );

  return (
    <div className="screen training-screen">
      <PageTitle
        eyebrow="Local LoRA training"
        title="Teach an original character, privately."
        body="Build a reviewed dataset, train with a hardware-safe profile, compare checkpoints, then install the chosen LoRA into its character."
        actions={
          <Button onClick={() => setCreating(true)}>
            <Plus /> New dataset
          </Button>
        }
      />

      <div className="training-readiness" aria-label="Training capability readiness">
        <StatusPill tone={trainerReady ? 'ready' : 'warning'}>
          Trainer {trainerReady ? 'ready' : 'setup needed'}
        </StatusPill>
        <StatusPill tone={captionReady ? 'ready' : 'warning'}>
          Captioning {captionReady ? 'ready' : 'setup needed'}
        </StatusPill>
        <span>sd-scripts 0.10.5 · local CUDA · no cloud upload</span>
      </div>

      {!data.trainingDatasets.length ? (
        <EmptyState
          title="No training datasets yet"
          body="Create a private dataset for an original character, then import only images you own or can use."
          action={<Button onClick={() => setCreating(true)}>Create dataset</Button>}
        />
      ) : (
        <div className="training-workspace">
          <aside className="training-dataset-list" aria-label="Training datasets">
            {data.trainingDatasets.map((dataset) => (
              <button
                type="button"
                key={dataset.id}
                className={selected?.id === dataset.id ? 'active' : ''}
                onClick={() => {
                  setSelectedId(dataset.id);
                  setRunDatasetFilter(dataset.id);
                }}
              >
                <span>{dataset.name}</span>
                <small>
                  {dataset.image_count} images · {dataset.trigger_token}
                </small>
              </button>
            ))}
          </aside>

          {selected && (
            <div className="training-detail">
              <Panel className="training-dataset-header">
                <div>
                  <span className="eyebrow">Dataset</span>
                  <h2>{selected.name}</h2>
                  <p>
                    Trigger <code>{selected.trigger_token}</code> · {selected.model_alias}
                  </p>
                </div>
                <div className="training-import-actions">
                  <label className="rights-check">
                    <input
                      type="checkbox"
                      checked={rightsConfirmed}
                      onChange={(event) => setRightsConfirmed(event.target.checked)}
                    />
                    I own or have permission to train on these images
                  </label>
                  <div>
                    <Button
                      onClick={() => void importImages()}
                      disabled={!rightsConfirmed || busy === 'import'}
                    >
                      <Upload /> Import images
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={() => void autoCaption()}
                      disabled={!captionReady || !selected.image_count || busy === 'caption'}
                    >
                      <Sparkles /> Auto-caption locally
                    </Button>
                  </div>
                </div>
              </Panel>

              {selected.images.length ? (
                <div className="training-image-grid">
                  {selected.images.map((image) => (
                    <Panel className="training-image-card" key={image.id}>
                      <div className="training-image-frame">
                        <LocalTrainingImage imageId={image.id} alt={image.original_name} />
                        <span>
                          {image.width} × {image.height}
                        </span>
                      </div>
                      <div className="training-warning-row">
                        {image.warnings.length ? (
                          image.warnings.map((warning) => (
                            <StatusPill key={warning} tone="warning">
                              {warning.replaceAll('_', ' ')}
                            </StatusPill>
                          ))
                        ) : (
                          <StatusPill tone="ready">Quality check passed</StatusPill>
                        )}
                      </div>
                      <label>
                        Reviewed caption
                        <textarea
                          defaultValue={image.caption}
                          onBlur={(event) => {
                            if (event.target.value.trim() !== image.caption)
                              void api
                                .put(`/training/images/${image.id}/caption`, {
                                  caption: event.target.value.trim(),
                                })
                                .then(() => notify('Caption saved locally'));
                          }}
                        />
                      </label>
                      <footer>
                        <small>Blur score {image.blur_score.toFixed(1)}</small>
                        <Button
                          variant="ghost"
                          onClick={async () => {
                            await api.delete(`/training/images/${image.id}`);
                            mediaCache.invalidateEntity('training-image', image.id);
                            await refresh();
                          }}
                        >
                          <Trash2 /> Remove
                        </Button>
                      </footer>
                    </Panel>
                  ))}
                </div>
              ) : (
                <EmptyState
                  title="This dataset is empty"
                  body="Import several varied, sharp images. Vanta will flag exact and near duplicates, low resolution, blur, and multiple subjects."
                />
              )}

              <Panel className="training-profile-panel">
                <div className="section-heading">
                  <div>
                    <span className="eyebrow">Hardware-safe setup</span>
                    <h2>Training profile</h2>
                  </div>
                  <p>Estimates adapt to this dataset and remain visible before the GPU starts.</p>
                </div>
                <div className="training-profile-options">
                  {(['safe_12gb', 'balanced_12gb'] as const).map((id) => {
                    const item = selected.profiles[id];
                    return (
                      <label className={profile === id ? 'selected' : ''} key={id}>
                        <input
                          type="radio"
                          name="training-profile"
                          value={id}
                          checked={profile === id}
                          onChange={() => setProfile(id)}
                        />
                        <strong>{item.display_name}</strong>
                        <span>
                          {item.resolution}px · rank {item.rank} · about {item.vram_gb} GB VRAM
                        </span>
                      </label>
                    );
                  })}
                </div>
                <div className="training-run-form">
                  <label>
                    Epochs
                    <input
                      type="number"
                      min={1}
                      max={40}
                      value={epochs}
                      onChange={(event) => setEpochs(Number(event.target.value))}
                    />
                  </label>
                  <label className="training-validation-prompt">
                    Validation prompt
                    <input
                      value={validationPrompt}
                      onChange={(event) => setValidationPrompt(event.target.value)}
                      placeholder={`portrait photograph of ${selected.trigger_token}`}
                    />
                  </label>
                  <dl className="training-estimates">
                    <div>
                      <dt>Estimated time</dt>
                      <dd>~{estimatedMinutes} min</dd>
                    </div>
                    <div>
                      <dt>Steps</dt>
                      <dd>{estimatedSteps}</dd>
                    </div>
                    <div>
                      <dt>Working disk</dt>
                      <dd>~{profileConfig.disk_gb} GB</dd>
                    </div>
                  </dl>
                  <Button
                    variant="primary"
                    onClick={() => void startTraining()}
                    disabled={
                      !trainerReady ||
                      !selected.image_count ||
                      Boolean(activeRun) ||
                      busy === 'train'
                    }
                  >
                    <Play /> Start local training
                  </Button>
                </div>
              </Panel>
            </div>
          )}
        </div>
      )}

      <section className="training-runs-section">
        <div className="section-heading">
          <div>
            <span className="eyebrow">Persistent local runs</span>
            <h2>Progress & checkpoints</h2>
          </div>
          <p>
            Epoch state, checkpoint files, validation samples, cancellation, and resume stay on
            disk.
          </p>
        </div>
        <div className="training-run-filters" aria-label="Filter training history">
          <label>
            Run state
            <select
              value={runFilter}
              onChange={(event) => setRunFilter(event.target.value as typeof runFilter)}
            >
              <option value="all">Recent runs</option>
              <option value="current">Current</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
            </select>
          </label>
          <label>
            Dataset
            <select
              value={runDatasetFilter}
              onChange={(event) => setRunDatasetFilter(event.target.value)}
            >
              <option value="all">All datasets</option>
              {data.trainingDatasets.map((dataset) => (
                <option key={dataset.id} value={dataset.id}>
                  {dataset.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        {visibleRuns.length ? (
          <div className="training-run-list">
            {visibleRuns.map((run) => {
              const dataset = data.trainingDatasets.find((item) => item.id === run.dataset_id);
              const runDetails = technicalDetails[run.id];
              return (
                <Panel className="training-run-card" key={run.id}>
                  <header>
                    <div>
                      <h3>{dataset?.name ?? 'Local dataset'}</h3>
                      <p>
                        {run.estimates.profile} · epoch {run.current_epoch}/{run.total_epochs} ·
                        step {run.current_step}/{run.total_steps}
                      </p>
                    </div>
                    <StatusPill tone={stateTone(run.status)}>
                      {stateLabels[run.status] ?? run.status}
                    </StatusPill>
                  </header>
                  <div className="progress" aria-label={`${run.progress}% trained`}>
                    <span style={{ width: `${run.progress}%` }} />
                  </div>
                  <div className="training-run-meta">
                    <span>{run.progress}%</span>
                    <span>{formatDuration(run.elapsed_seconds)} elapsed</span>
                    <span>
                      {run.eta_seconds != null
                        ? `${Math.ceil(run.eta_seconds / 60)} min remaining`
                        : `${run.estimates.vram_gb} GB estimated VRAM`}
                    </span>
                  </div>
                  {run.failure && (
                    <div className="training-failure" role="alert">
                      <span className="eyebrow">{run.failure.category.replaceAll('_', ' ')}</span>
                      <strong>{run.failure.title}</strong>
                      <p>{run.failure.explanation}</p>
                      <small>{run.failure.recommended_recovery}</small>
                    </div>
                  )}
                  {runDetails && (
                    <details className="training-technical-details" open>
                      <summary>Sanitized trainer details</summary>
                      <pre>{runDetails.content}</pre>
                      {runDetails.truncated && (
                        <small>
                          The oldest output was omitted; the complete local log remains on disk.
                        </small>
                      )}
                    </details>
                  )}
                  <div className="training-checkpoints">
                    {run.checkpoints.map((checkpoint) => (
                      <article
                        className={checkpoint.selected ? 'selected' : ''}
                        key={checkpoint.id}
                      >
                        <TrainingValidationImage checkpointId={checkpoint.id} />
                        <div>
                          <strong>Epoch {checkpoint.epoch}</strong>
                          <small>
                            {(checkpoint.file_size / 1024 / 1024).toFixed(1)} MB ·{' '}
                            {checkpoint.sha256.slice(0, 10)}…
                          </small>
                        </div>
                        <Button
                          variant="ghost"
                          onClick={async () => {
                            await api.post(
                              `/training/runs/${run.id}/checkpoints/${checkpoint.id}/select`,
                            );
                            await refresh();
                          }}
                        >
                          {checkpoint.selected ? <Check /> : <Star />} Select
                        </Button>
                      </article>
                    ))}
                  </div>
                  <footer>
                    {['queued', 'preparing', 'training', 'cancelling'].includes(run.status) && (
                      <Button onClick={() => void api.post(`/training/runs/${run.id}/cancel`)}>
                        Cancel safely
                      </Button>
                    )}
                    {['failed', 'cancelled'].includes(run.status) && run.resume_state_path && (
                      <Button
                        onClick={async () => {
                          await api.post(`/training/runs/${run.id}/resume`);
                          await refresh();
                        }}
                      >
                        <RotateCcw /> Resume saved state
                      </Button>
                    )}
                    {['failed', 'cancelled'].includes(run.status) && (
                      <Button
                        onClick={async () => {
                          await api.post(`/training/runs/${run.id}/retry`);
                          notify(
                            'A fresh local training run was queued; the prior run was preserved',
                          );
                          await refresh();
                        }}
                      >
                        <RotateCcw /> Retry from start
                      </Button>
                    )}
                    {run.status === 'failed' && (
                      <>
                        <Button
                          disabled={loadingDetails === run.id}
                          onClick={async () => {
                            setLoadingDetails(run.id);
                            try {
                              const details = await api.get<{
                                technical_details: string;
                                truncated: boolean;
                              }>(`/training/runs/${run.id}/failure-details`);
                              setTechnicalDetails((current) => ({
                                ...current,
                                [run.id]: {
                                  content: details.technical_details,
                                  truncated: details.truncated,
                                },
                              }));
                            } finally {
                              setLoadingDetails('');
                            }
                          }}
                        >
                          <Info />
                          {loadingDetails === run.id
                            ? 'Loading details…'
                            : 'Open technical details'}
                        </Button>
                        <Button onClick={goEngine}>
                          <Gauge /> Open diagnostics
                        </Button>
                      </>
                    )}
                    {run.status === 'completed' &&
                      run.checkpoints.length > 0 &&
                      !run.installed_lora_id && (
                        <>
                          <select
                            aria-label="Character for trained LoRA"
                            value={installCharacterId || dataset?.character_id || ''}
                            onChange={(event) => setInstallCharacterId(event.target.value)}
                          >
                            <option value="">Choose character…</option>
                            {data.characters.map((character) => (
                              <option value={character.id} key={character.id}>
                                {character.name}
                              </option>
                            ))}
                          </select>
                          <Button
                            variant="primary"
                            disabled={!installCharacterId && !dataset?.character_id}
                            onClick={async () => {
                              const checkpoint =
                                run.checkpoints.find((item) => item.selected) ??
                                run.checkpoints[run.checkpoints.length - 1];
                              if (!checkpoint) return;
                              await api.post(`/training/runs/${run.id}/install`, {
                                checkpoint_id: checkpoint.id,
                                name: `${dataset?.name ?? 'Vanta'} trained LoRA`,
                                character_id: installCharacterId || dataset?.character_id,
                                strength: 0.8,
                              });
                              notify('Selected trained LoRA installed into the character');
                              await refresh();
                            }}
                          >
                            <Zap /> Install into character
                          </Button>
                        </>
                      )}
                    {run.installed_lora_id && (
                      <StatusPill tone="ready">Installed in character</StatusPill>
                    )}
                  </footer>
                </Panel>
              );
            })}
          </div>
        ) : data.trainingRuns.length ? (
          <EmptyState
            title="No runs match these filters"
            body="Choose another run state or dataset. No history has been removed."
          />
        ) : (
          <EmptyState
            title="No training runs"
            body="Completed, cancelled, failed, and resumable runs will remain here with honest state."
          />
        )}
      </section>

      <Drawer
        open={creating}
        title="New private training dataset"
        onClose={() => setCreating(false)}
      >
        <form className="drawer-form" onSubmit={(event) => void createDataset(event)}>
          <label>
            Dataset name
            <input name="name" required placeholder="Mara editorial identity" />
          </label>
          <label>
            Original character
            <select name="character_id" defaultValue="">
              <option value="">Choose later</option>
              {data.characters.map((character) => (
                <option key={character.id} value={character.id}>
                  {character.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Unique trigger token
            <input
              name="trigger_token"
              required
              pattern="[A-Za-z][A-Za-z0-9_-]+"
              placeholder="maraVanta"
            />
            <small>Use an invented token that is not a real person’s name.</small>
          </label>
          <label>
            SDXL base profile
            <select name="model_alias" defaultValue="photoreal_balanced">
              <option value="photoreal_balanced">Realistic — Balanced</option>
              <option value="preview_fast">Preview — Fast</option>
            </select>
          </label>
          <label>
            Notes
            <textarea name="notes" placeholder="Rights, source, and intended style notes" />
          </label>
          <footer>
            <Button type="button" variant="ghost" onClick={() => setCreating(false)}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" disabled={busy === 'create'}>
              Create local dataset
            </Button>
          </footer>
        </form>
      </Drawer>
    </div>
  );
}

function PresetsScreen({
  items,
  recipes,
  characters,
  refresh,
  notify,
}: {
  items: PresetRecord[];
  recipes: RecipeRecord[];
  characters: CharacterRecord[];
  refresh: () => Promise<void>;
  notify: (message: string) => void;
}) {
  const [library, setLibrary] = useState<'presets' | 'recipes'>('presets');
  const [category, setCategory] = useState('all');
  const [query, setQuery] = useState('');
  const [editing, setEditing] = useState<PresetRecord | 'new' | null>(null);
  const [editingRecipe, setEditingRecipe] = useState<RecipeRecord | 'new' | null>(null);
  const filtered = items.filter(
    (item) =>
      (category === 'all' || item.category === category) &&
      `${item.name} ${item.tags.join(' ')}`.toLowerCase().includes(query.toLowerCase()),
  );
  const filteredRecipes = recipes.filter((item) =>
    `${item.name} ${item.tags.join(' ')}`.toLowerCase().includes(query.toLowerCase()),
  );
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const payload = {
      category: String(form.get('category')),
      name: String(form.get('name')),
      prompt: String(form.get('prompt')),
      negative_prompt: String(form.get('negative')),
      tags: String(form.get('tags'))
        .split(',')
        .map((tag) => tag.trim())
        .filter(Boolean),
      favorite: editing !== 'new' && editing ? editing.favorite : false,
      scope: String(form.get('scope')),
      scope_id:
        String(form.get('scope')) === 'character'
          ? String(form.get('character_scope'))
          : String(form.get('scope')) === 'project'
            ? String(form.get('project_scope'))
            : null,
    };
    if (editing === 'new') await api.post('/presets', payload);
    else if (editing) await api.put(`/presets/${editing.id}`, payload);
    notify(
      editing !== 'new' && editing?.origin === 'builtin'
        ? 'Editable user copy created; built-in preserved'
        : 'Preset saved',
    );
    setEditing(null);
    await refresh();
  };
  const updateFavorite = async (item: PresetRecord) => {
    await api.put(`/presets/${item.id}`, {
      category: item.category,
      name: item.name,
      prompt: item.prompt,
      negative_prompt: item.negative_prompt,
      tags: item.tags,
      favorite: !item.favorite,
      scope: item.scope,
      scope_id: item.scope_id,
    });
    notify(
      item.origin === 'builtin'
        ? 'Favorite saved on a new user-owned copy'
        : item.favorite
          ? 'Removed from favorites'
          : 'Added to favorites',
    );
    await refresh();
  };
  const recipeInput = (item: RecipeRecord, overrides: Partial<RecipeRecord> = {}) => ({
    name: item.name,
    character_id: item.character_id,
    freeform_prompt: item.freeform_prompt,
    negative_prompt: item.negative_prompt,
    model_profile: item.model_profile,
    preset_ids: item.preset_ids,
    scope: item.scope,
    scope_id: item.scope_id,
    favorite: item.favorite,
    tags: item.tags,
    model_family: item.model_family,
    model_file: item.model_file,
    lora_stack: item.lora_stack,
    identity_settings: item.identity_settings,
    pose_settings: item.pose_settings,
    variation_settings: item.variation_settings,
    video_settings: item.video_settings,
    generation_settings: item.generation_settings,
    ...overrides,
  });
  const saveRecipeLibrary = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const scope = String(form.get('scope')) as RecipeRecord['scope'];
    const characterId = String(form.get('character_id')) || null;
    const base = editingRecipe === 'new' ? null : editingRecipe;
    const payload = base
      ? recipeInput(base, {
          name: String(form.get('name')),
          character_id: characterId,
          freeform_prompt: String(form.get('prompt')),
          negative_prompt: String(form.get('negative')),
          model_profile: String(form.get('model_profile')) as RecipeRecord['model_profile'],
          model_family: String(form.get('model_profile')) === 'photoreal_max' ? 'FLUX' : 'SDXL',
          scope,
          scope_id:
            scope === 'character'
              ? String(form.get('character_scope'))
              : scope === 'project'
                ? String(form.get('project_scope'))
                : null,
          tags: String(form.get('tags'))
            .split(',')
            .map((tag) => tag.trim())
            .filter(Boolean),
        })
      : {
          name: String(form.get('name')),
          character_id: characterId,
          freeform_prompt: String(form.get('prompt')),
          negative_prompt: String(form.get('negative')),
          model_profile: String(form.get('model_profile')),
          preset_ids: [],
          scope,
          scope_id:
            scope === 'character'
              ? String(form.get('character_scope'))
              : scope === 'project'
                ? String(form.get('project_scope'))
                : null,
          favorite: false,
          tags: String(form.get('tags'))
            .split(',')
            .map((tag) => tag.trim())
            .filter(Boolean),
          model_family: String(form.get('model_profile')) === 'photoreal_max' ? 'FLUX' : 'SDXL',
          model_file: '',
          lora_stack: [],
          identity_settings: {},
          pose_settings: {},
          variation_settings: {},
          video_settings: {},
          generation_settings: {},
        };
    if (base) await api.put(`/recipes/${base.id}`, payload);
    else await api.post('/recipes', payload);
    setEditingRecipe(null);
    notify(base ? 'Recipe updated' : 'Recipe created');
    await refresh();
  };
  const duplicate = async (item: PresetRecord) => {
    await api.post(`/presets/${item.id}/duplicate`);
    notify('Preset duplicated into your library');
    await refresh();
  };
  const remove = async (item: PresetRecord) => {
    if (!window.confirm(`Delete ${item.name}?`)) return;
    await api.delete(`/presets/${item.id}`);
    notify('User preset deleted');
    await refresh();
  };
  const exportJson = async () => {
    const payload = await api.get('/presets-export');
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'vanta-presets.json';
    anchor.click();
    URL.revokeObjectURL(url);
    notify('Preset library exported');
  };
  const importJson = (file: File) => {
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        await api.post('/presets-import', JSON.parse(String(reader.result)));
        notify('Presets imported');
        await refresh();
      } catch {
        notify('Import failed: choose a Vanta preset export');
      }
    };
    reader.readAsText(file);
  };
  const exportRecipes = async () => {
    const payload = await api.get('/recipes-export');
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'vanta-recipes.json';
    anchor.click();
    URL.revokeObjectURL(url);
    notify('Recipe library exported');
  };
  const importRecipes = (file: File) => {
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        await api.post('/recipes-import', JSON.parse(String(reader.result)));
        notify('Recipes imported');
        await refresh();
      } catch {
        notify('Import failed: choose a Vanta recipe export');
      }
    };
    reader.readAsText(file);
  };
  return (
    <div className="screen">
      <PageTitle
        eyebrow="Creative library"
        title={
          library === 'presets' ? 'Presets that become yours.' : 'Complete recipes, remembered.'
        }
        body={
          library === 'presets'
            ? 'Start curated. Duplicate, tag, and reshape anything without losing the original.'
            : 'Model, LoRAs, identity, pose, variation, video, and generation settings stay together.'
        }
        actions={
          <>
            <label className="v-button v-button--secondary file-button">
              <FileUp /> Import
              <input
                type="file"
                accept="application/json"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) (library === 'presets' ? importJson : importRecipes)(file);
                }}
              />
            </label>
            <Button onClick={() => void (library === 'presets' ? exportJson() : exportRecipes())}>
              <FileDown /> Export
            </Button>
            <Button
              variant="primary"
              onClick={() => (library === 'presets' ? setEditing('new') : setEditingRecipe('new'))}
            >
              <Plus /> New {library === 'presets' ? 'preset' : 'recipe'}
            </Button>
          </>
        }
      />
      <div className="mode-switch library-switch" role="group" aria-label="Creative library type">
        <button
          className={library === 'presets' ? 'active' : ''}
          onClick={() => setLibrary('presets')}
        >
          Presets
        </button>
        <button
          className={library === 'recipes' ? 'active' : ''}
          onClick={() => setLibrary('recipes')}
        >
          Recipes
        </button>
      </div>
      <div className="library-toolbar">
        <div className="search-field">
          <Search />
          <input
            aria-label={`Search ${library}`}
            placeholder={`Search ${library} by name or tag`}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        {library === 'presets' && (
          <div className="category-tabs" role="tablist" aria-label="Preset categories">
            {presetCategories.map((item) => (
              <button
                key={item}
                role="tab"
                aria-selected={category === item}
                className={category === item ? 'active' : ''}
                onClick={() => setCategory(item)}
              >
                {item.replace('_', ' ')}
              </button>
            ))}
          </div>
        )}
        {library === 'presets' && (
          <Button
            variant="ghost"
            onClick={async () => {
              await api.post('/presets/restore-builtins');
              notify('Built-in presets restored');
              await refresh();
            }}
          >
            <RotateCcw /> Restore built-ins
          </Button>
        )}
      </div>
      {library === 'recipes' ? (
        filteredRecipes.length === 0 ? (
          <EmptyState
            title="No recipes match"
            body="Create a recipe here or save the current composition from Create."
            action={<Button onClick={() => setEditingRecipe('new')}>New recipe</Button>}
          />
        ) : (
          <div className="preset-grid">
            {filteredRecipes.map((recipe) => (
              <Panel className="preset-card recipe-card" key={recipe.id}>
                <header>
                  <span className="preset-origin user">{recipe.scope} recipe</span>
                  <button
                    className={`icon-button ${recipe.favorite ? 'favorite' : ''}`}
                    aria-label={`${recipe.favorite ? 'Unfavorite' : 'Favorite'} ${recipe.name}`}
                    onClick={async () => {
                      await api.put(
                        `/recipes/${recipe.id}`,
                        recipeInput(recipe, { favorite: !recipe.favorite }),
                      );
                      await refresh();
                    }}
                  >
                    <Heart fill={recipe.favorite ? 'currentColor' : 'none'} />
                  </button>
                </header>
                <span className="eyebrow">
                  {recipe.model_family} · {recipe.model_profile}
                </span>
                <h2>{recipe.name}</h2>
                <p>{recipe.freeform_prompt || 'Preset-led composition'}</p>
                <div className="tag-row">
                  {recipe.tags.map((tag) => (
                    <span className="tag" key={tag}>
                      {tag}
                    </span>
                  ))}
                </div>
                <small>
                  {recipe.preset_ids.length} presets · {recipe.lora_stack.length} LoRAs ·{' '}
                  {String(recipe.generation_settings.width ?? 'default')} ×{' '}
                  {String(recipe.generation_settings.height ?? 'default')}
                </small>
                <footer>
                  <Button variant="ghost" onClick={() => setEditingRecipe(recipe)}>
                    <Edit3 /> Edit
                  </Button>
                  <button
                    className="icon-button"
                    aria-label={`Duplicate ${recipe.name}`}
                    onClick={async () => {
                      await api.post(`/recipes/${recipe.id}/duplicate`);
                      notify('Recipe duplicated');
                      await refresh();
                    }}
                  >
                    <Copy />
                  </button>
                  <button
                    className="icon-button danger"
                    aria-label={`Delete ${recipe.name}`}
                    onClick={async () => {
                      if (!window.confirm(`Delete ${recipe.name}?`)) return;
                      await api.delete(`/recipes/${recipe.id}`);
                      notify('Recipe deleted');
                      await refresh();
                    }}
                  >
                    <Trash2 />
                  </button>
                </footer>
              </Panel>
            ))}
          </div>
        )
      ) : filtered.length === 0 ? (
        <EmptyState
          title="No presets match"
          body="Clear the search or create a preset in this category."
          action={
            <Button
              onClick={() => {
                setQuery('');
                setCategory('all');
              }}
            >
              Clear filters
            </Button>
          }
        />
      ) : (
        <div className="preset-grid">
          {filtered.map((item) => (
            <Panel className="preset-card" key={item.id}>
              <header>
                <span className={`preset-origin ${item.origin}`}>
                  {item.origin === 'builtin' ? 'Vanta built-in' : 'Your preset'}
                </span>
                <button
                  className={`icon-button ${item.favorite ? 'favorite' : ''}`}
                  aria-label={`${item.favorite ? 'Unfavorite' : 'Favorite'} ${item.name}`}
                  onClick={() => void updateFavorite(item)}
                >
                  <Heart fill={item.favorite ? 'currentColor' : 'none'} />
                </button>
              </header>
              <span className="eyebrow">{item.category.replace('_', ' ')}</span>
              <h2>{item.name}</h2>
              <p>{item.prompt}</p>
              <div className="tag-row">
                {item.tags.map((tag) => (
                  <span className="tag" key={tag}>
                    {tag}
                  </span>
                ))}
              </div>
              <footer>
                <Button variant="ghost" onClick={() => setEditing(item)}>
                  <Edit3 /> {item.origin === 'builtin' ? 'Edit a copy' : 'Edit'}
                </Button>
                <button
                  className="icon-button"
                  onClick={() => void duplicate(item)}
                  aria-label={`Duplicate ${item.name}`}
                >
                  <Copy />
                </button>
                {item.origin === 'user' && (
                  <button
                    className="icon-button danger"
                    onClick={() => void remove(item)}
                    aria-label={`Delete ${item.name}`}
                  >
                    <Trash2 />
                  </button>
                )}
              </footer>
            </Panel>
          ))}
        </div>
      )}
      {editing && (
        <div className="modal-layer">
          <form className="modal modal--wide" onSubmit={(event) => void save(event)}>
            <header>
              <div>
                <span className="eyebrow">
                  {editing === 'new'
                    ? 'Add to library'
                    : editing.origin === 'builtin'
                      ? 'Create an editable copy'
                      : 'Edit preset'}
                </span>
                <h2>{editing === 'new' ? 'New preset' : editing.name}</h2>
              </div>
              <button
                type="button"
                className="icon-button"
                onClick={() => setEditing(null)}
                aria-label="Close"
              >
                <X />
              </button>
            </header>
            {editing !== 'new' && editing.origin === 'builtin' && (
              <div className="copy-notice">
                <Copy />
                <span>
                  <strong>The built-in stays untouched.</strong> Saving creates a user-owned copy
                  you can rename or delete.
                </span>
              </div>
            )}
            <div className="form-grid">
              <label>
                Name
                <input
                  name="name"
                  defaultValue={editing === 'new' ? '' : editing.name}
                  required
                  autoFocus
                />
              </label>
              <label>
                Category
                <select
                  name="category"
                  defaultValue={editing === 'new' ? 'wardrobe' : editing.category}
                >
                  {presetCategories.slice(1).map((item) => (
                    <option key={item}>{item}</option>
                  ))}
                </select>
              </label>
            </div>
            <label>
              Prompt
              <textarea
                name="prompt"
                defaultValue={editing === 'new' ? '' : editing.prompt}
                required
              />
            </label>
            <label>
              Negative prompt
              <textarea
                name="negative"
                defaultValue={editing === 'new' ? '' : editing.negative_prompt}
              />
            </label>
            <label>
              Tags
              <input
                name="tags"
                defaultValue={editing === 'new' ? '' : editing.tags.join(', ')}
                placeholder="editorial, moody, indoor"
              />
            </label>
            <div className="form-grid">
              <label>
                Scope
                <select name="scope" defaultValue={editing === 'new' ? 'global' : editing.scope}>
                  <option value="global">Global</option>
                  <option value="character">Character</option>
                  <option value="project">Project</option>
                </select>
              </label>
              <label>
                Character scope
                <select
                  name="character_scope"
                  defaultValue={editing === 'new' ? '' : (editing.scope_id ?? '')}
                >
                  <option value="">Choose a character</option>
                  {characters.map((character) => (
                    <option key={character.id} value={character.id}>
                      {character.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label>
              Project scope name
              <input
                name="project_scope"
                defaultValue={
                  editing !== 'new' && editing.scope === 'project' ? (editing.scope_id ?? '') : ''
                }
                placeholder="Campaign or project name"
              />
            </label>
            <footer>
              <Button type="button" variant="ghost" onClick={() => setEditing(null)}>
                Cancel
              </Button>
              <Button variant="primary" type="submit">
                Save {editing !== 'new' && editing.origin === 'builtin' ? 'as copy' : 'preset'}
              </Button>
            </footer>
          </form>
        </div>
      )}
      {editingRecipe && (
        <div className="modal-layer">
          <form className="modal modal--wide" onSubmit={(event) => void saveRecipeLibrary(event)}>
            <header>
              <div>
                <span className="eyebrow">
                  {editingRecipe === 'new' ? 'New recipe' : 'Edit recipe'}
                </span>
                <h2>
                  {editingRecipe === 'new' ? 'Save a complete composition' : editingRecipe.name}
                </h2>
              </div>
              <button
                type="button"
                className="icon-button"
                onClick={() => setEditingRecipe(null)}
                aria-label="Close recipe editor"
              >
                <X />
              </button>
            </header>
            <div className="form-grid">
              <label>
                Name
                <input
                  name="name"
                  required
                  autoFocus
                  defaultValue={editingRecipe === 'new' ? '' : editingRecipe.name}
                />
              </label>
              <label>
                Model profile
                <select
                  name="model_profile"
                  defaultValue={
                    editingRecipe === 'new' ? 'photoreal_balanced' : editingRecipe.model_profile
                  }
                >
                  <option value="photoreal_balanced">Realistic - Balanced</option>
                  <option value="preview_fast">Preview - Fast</option>
                  <option value="photoreal_max">Realistic - Maximum (FLUX)</option>
                </select>
              </label>
            </div>
            <label>
              Custom positive prompt
              <textarea
                name="prompt"
                defaultValue={editingRecipe === 'new' ? '' : editingRecipe.freeform_prompt}
              />
            </label>
            <label>
              Custom negative prompt
              <textarea
                name="negative"
                defaultValue={editingRecipe === 'new' ? '' : editingRecipe.negative_prompt}
              />
            </label>
            <div className="form-grid">
              <label>
                Character
                <select
                  name="character_id"
                  defaultValue={editingRecipe === 'new' ? '' : (editingRecipe.character_id ?? '')}
                >
                  <option value="">No character</option>
                  {characters.map((character) => (
                    <option key={character.id} value={character.id}>
                      {character.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Scope
                <select
                  name="scope"
                  defaultValue={editingRecipe === 'new' ? 'global' : editingRecipe.scope}
                >
                  <option value="global">Global</option>
                  <option value="character">Character</option>
                  <option value="project">Project</option>
                </select>
              </label>
              <label>
                Character scope
                <select
                  name="character_scope"
                  defaultValue={editingRecipe === 'new' ? '' : (editingRecipe.scope_id ?? '')}
                >
                  <option value="">Choose a character</option>
                  {characters.map((character) => (
                    <option key={character.id} value={character.id}>
                      {character.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Project scope name
                <input
                  name="project_scope"
                  defaultValue={
                    editingRecipe !== 'new' && editingRecipe.scope === 'project'
                      ? (editingRecipe.scope_id ?? '')
                      : ''
                  }
                />
              </label>
            </div>
            <label>
              Tags
              <input
                name="tags"
                defaultValue={editingRecipe === 'new' ? '' : editingRecipe.tags.join(', ')}
              />
            </label>
            {editingRecipe !== 'new' && (
              <div className="copy-notice">
                <Info />
                <span>
                  Advanced LoRA, identity, pose, variation, video, and generation settings are
                  preserved here. Open this recipe in Create to tune them visually.
                </span>
              </div>
            )}
            <footer>
              <Button type="button" variant="ghost" onClick={() => setEditingRecipe(null)}>
                Cancel
              </Button>
              <Button type="submit" variant="primary">
                Save recipe
              </Button>
            </footer>
          </form>
        </div>
      )}
    </div>
  );
}

function LocalGenerationImage({
  generationId,
  alt,
  thumbnail = false,
  mediaType = 'image',
}: {
  generationId: string;
  alt: string;
  thumbnail?: boolean;
  mediaType?: 'image' | 'video';
}) {
  const variant = thumbnail ? (mediaType === 'video' ? 'poster' : 'thumbnail') : 'original';
  return (
    <AuthenticatedImage
      className="generation-image"
      media={{ entity: 'generation', id: generationId, variant }}
      fallbackVariant={thumbnail && mediaType === 'image' ? 'original' : undefined}
      placeholderClassName="generated-study"
      alt={alt}
    />
  );
}

function LocalGenerationVideo({ generationId, label }: { generationId: string; label: string }) {
  return (
    <AuthenticatedVideo
      className="generation-video"
      media={{ entity: 'generation', id: generationId, variant: 'video' }}
      placeholderClassName="generated-study"
      aria-label={label}
      controls
      playsInline
      preload="metadata"
    />
  );
}

function VideoWorkspace({
  source,
  motion,
  videoReady,
  refresh,
  notify,
  onClose,
  onJob,
  onDiagnostics,
}: {
  source: GenerationRecord;
  motion: MotionAsset[];
  videoReady: boolean;
  refresh: () => Promise<void>;
  notify: (message: string) => void;
  onClose: () => void;
  onJob: (job: GenerationJob) => void;
  onDiagnostics: () => void;
}) {
  const [motionPrompt, setMotionPrompt] = useState(
    'subtle natural breathing, a gentle shift of posture, restrained cinematic camera movement',
  );
  const [negativePrompt, setNegativePrompt] = useState(
    'text, watermark, logo, identity change, face distortion, sudden camera shake',
  );
  const [profile, setProfile] = useState<'safe' | 'balanced' | 'quality'>('safe');
  const [durationProfile, setDurationProfile] = useState<
    'safe' | 'standard' | 'extended' | 'custom'
  >('safe');
  const [duration, setDuration] = useState(2);
  const [capabilities, setCapabilities] = useState<VideoCapabilities | null>(null);
  const [motionAssetId, setMotionAssetId] = useState('');
  const [strength, setStrength] = useState(0.65);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [error, setError] = useState('');
  const readyMotion = motion.filter((item) => item.status === 'ready');
  const active = job && !['completed', 'failed', 'cancelled'].includes(job.status);
  const durationEstimate =
    capabilities?.profiles.find((item) => item.id === durationProfile) ??
    capabilities?.profiles.find((item) => item.duration_seconds === duration) ??
    capabilities?.profiles[0];

  useEffect(() => {
    void api
      .get<VideoCapabilities>(`/videos/capabilities?quality_profile=${profile}`)
      .then(setCapabilities);
  }, [profile]);

  useEffect(() => {
    if (!job || !active) return;
    const timer = window.setInterval(() => {
      void api.get<GenerationJob>(`/generations/${job.id}`).then(async (next) => {
        setJob(next);
        onJob(next);
        if (next.status === 'completed') {
          notify('Local video saved to Gallery');
          await refresh();
        }
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [active, job, notify, onJob, refresh]);

  const submit = async () => {
    setError('');
    if (!videoReady) {
      setError('Install and verify Image-to-Video in Models & Engine first.');
      return;
    }
    try {
      const next = await api.post<GenerationJob>('/videos', {
        source_generation_id: source.id,
        motion_prompt: motionPrompt,
        negative_prompt: negativePrompt,
        profile,
        duration_profile: durationProfile,
        duration_seconds: duration,
        seed: Math.floor(Math.random() * 2_000_000_000),
        motion_asset_id: motionAssetId || null,
        motion_strength: strength,
      });
      setJob(next);
      onJob(next);
      notify('Image-to-video queued locally');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Local video could not start.');
    }
  };

  return (
    <div
      className="inpaint-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Image-to-video workspace"
    >
      <div className="video-workspace">
        <header>
          <div>
            <span className="eyebrow">Native local image-to-video</span>
            <h2>Give this frame a pulse.</h2>
            <p>
              Hardware-safe clips are rendered locally. Longer work is assembled from short,
              controllable segments.
            </p>
          </div>
          <Button variant="ghost" onClick={onClose}>
            <X /> Close
          </Button>
        </header>
        <div className="video-workspace__layout">
          <section className="video-source-stage">
            {job?.status === 'completed' && job.result_generation_id ? (
              <LocalGenerationVideo
                generationId={job.result_generation_id}
                label="Completed local generated video"
              />
            ) : (
              <LocalGenerationImage generationId={source.id} alt="Source still for local video" />
            )}
            <div className="video-source-caption">
              <span>{job?.status === 'completed' ? 'Rendered motion' : 'First frame'}</span>
              <strong>
                {source.width} × {source.height}
              </strong>
            </div>
          </section>
          <aside className="video-settings">
            <label>
              Movement direction
              <textarea
                value={motionPrompt}
                onChange={(event) => setMotionPrompt(event.target.value)}
              />
              <small>
                Describe body, fabric, hair, environment, and camera movement—not a new identity.
              </small>
            </label>
            <label>
              Avoid
              <textarea
                value={negativePrompt}
                onChange={(event) => setNegativePrompt(event.target.value)}
              />
            </label>
            <div className="form-grid">
              <label>
                Quality profile
                <select
                  value={profile}
                  onChange={(event) => setProfile(event.target.value as typeof profile)}
                >
                  <option value="safe">Safe · 512 × 768</option>
                  <option value="balanced">Balanced · 576 × 768</option>
                  <option value="quality">Quality · 640 × 832</option>
                </select>
              </label>
              <label>
                Duration profile
                <select
                  value={durationProfile}
                  onChange={(event) => {
                    const next = event.target.value as typeof durationProfile;
                    setDurationProfile(next);
                    if (next === 'safe') setDuration(2);
                    if (next === 'standard') setDuration(4);
                    if (next === 'extended') setDuration(6);
                  }}
                >
                  <option value="safe">Safe · 2 seconds</option>
                  <option value="standard">Standard · 4 seconds</option>
                  <option value="extended" disabled={!capabilities?.extended_verified}>
                    Extended · 6–8 seconds{' '}
                    {capabilities?.extended_verified ? '' : '· not verified on this hardware'}
                  </option>
                  <option value="custom">Custom · within verified limit</option>
                </select>
              </label>
            </div>
            {(durationProfile === 'custom' || durationProfile === 'extended') && (
              <label>
                Duration <span>{duration} seconds</span>
                <input
                  type="range"
                  min={durationProfile === 'extended' ? 6 : 2}
                  max={durationProfile === 'extended' ? 8 : (capabilities?.max_custom_seconds ?? 4)}
                  step={1}
                  value={duration}
                  onChange={(event) => setDuration(Number(event.target.value))}
                />
              </label>
            )}
            {durationEstimate && (
              <dl className="video-duration-estimates">
                <div>
                  <dt>Frames</dt>
                  <dd>{duration * 24 + 1}</dd>
                </div>
                <div>
                  <dt>Expected time</dt>
                  <dd>
                    ~
                    {formatDuration(
                      Math.round(
                        (durationEstimate.expected_generation_seconds /
                          durationEstimate.duration_seconds) *
                          duration,
                      ),
                    )}
                  </dd>
                </div>
                <div>
                  <dt>VRAM / RAM</dt>
                  <dd>
                    ~{durationEstimate.estimated_vram_gb} / {durationEstimate.estimated_ram_gb} GB
                  </dd>
                </div>
                <div>
                  <dt>Working disk</dt>
                  <dd>
                    ~
                    {Math.round(
                      (durationEstimate.estimated_disk_mb / durationEstimate.duration_seconds) *
                        duration,
                    )}{' '}
                    MB
                  </dd>
                </div>
              </dl>
            )}
            <p className="video-duration-warning">
              Longer clips become substantially slower and less predictable. Sequence mode keeps
              each pass inside the verified hardware envelope.
            </p>
            <label>
              Reference Motion · optional
              <select
                value={motionAssetId}
                onChange={(event) => setMotionAssetId(event.target.value)}
              >
                <option value="">Prompt direction only</option>
                {readyMotion.map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
              <small>
                Only the saved broad movement description is applied—never the reference person’s
                face, voice, or branding.
              </small>
            </label>
            {motionAssetId && (
              <label>
                Motion strength <span>{strength.toFixed(2)}</span>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={strength}
                  onChange={(event) => setStrength(Number(event.target.value))}
                />
              </label>
            )}
            {job && (
              <JobProgressPanel
                job={job}
                compact
                onCancel={() => {
                  void api.post<GenerationJob>(`/generations/${job.id}/cancel`).then((next) => {
                    setJob(next);
                    onJob(next);
                  });
                }}
                onDiagnostics={onDiagnostics}
              />
            )}
            {error && (
              <p className="inline-error" role="alert">
                {error}
              </p>
            )}
            <Button
              variant="primary"
              disabled={!motionPrompt.trim() || job?.status === 'completed'}
              onClick={() =>
                void (active && job ? api.post(`/generations/${job.id}/cancel`) : submit())
              }
            >
              {active ? (
                <>
                  <X /> Cancel render
                </>
              ) : (
                <>
                  <Play /> {job?.status === 'completed' ? 'Saved to Gallery' : 'Render local video'}
                </>
              )}
            </Button>
          </aside>
        </div>
      </div>
    </div>
  );
}

function VideoSequenceWorkspace({
  source,
  motion,
  videoReady,
  refresh,
  notify,
  onClose,
  onJob,
  onDiagnostics,
}: {
  source: GenerationRecord;
  motion: MotionAsset[];
  videoReady: boolean;
  refresh: () => Promise<void>;
  notify: (message: string) => void;
  onClose: () => void;
  onJob: (job: GenerationJob) => void;
  onDiagnostics: () => void;
}) {
  const creating = useRef(false);
  const [sequence, setSequence] = useState<VideoSequence | null>(null);
  const [motionPrompt, setMotionPrompt] = useState(
    'continue the restrained natural movement while preserving identity, styling, and camera mood',
  );
  const [negativePrompt, setNegativePrompt] = useState(
    'text, watermark, logo, identity change, face distortion, sudden camera shake',
  );
  const [profile, setProfile] = useState<'safe' | 'balanced' | 'quality'>('safe');
  const [durationProfile, setDurationProfile] = useState<'safe' | 'standard'>('safe');
  const [duration, setDuration] = useState(2);
  const [motionAssetId, setMotionAssetId] = useState('');
  const [capabilities, setCapabilities] = useState<VideoCapabilities | null>(null);
  const [continuationSourceId, setContinuationSourceId] = useState<string | null>(null);
  const [continuationTimes, setContinuationTimes] = useState<Record<string, number>>({});
  const [excludedSegmentIds, setExcludedSegmentIds] = useState<string[]>([]);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const readyMotion = motion.filter((item) => item.status === 'ready');

  useEffect(() => {
    if (creating.current) return;
    creating.current = true;
    void api
      .post<VideoSequence>('/video-sequences', {
        name: source.media_type === 'video' ? 'Extended clip' : 'Character motion sequence',
        source_generation_id: source.id,
      })
      .then(setSequence)
      .catch((caught) =>
        setError(caught instanceof Error ? caught.message : 'The sequence could not be created.'),
      );
  }, [source.id, source.media_type]);

  useEffect(() => {
    void api
      .get<VideoCapabilities>(`/videos/capabilities?quality_profile=${profile}`)
      .then(setCapabilities);
  }, [profile]);

  useEffect(() => {
    if (!sequence || !sequence.segments.some((item) => !terminalJobStates.includes(item.status)))
      return;
    const timer = window.setInterval(() => {
      void api.get<VideoSequence>(`/video-sequences/${sequence.id}`).then(async (next) => {
        setSequence(next);
        const completedNow = next.segments.some(
          (item) =>
            item.status === 'completed' &&
            !sequence.segments.some(
              (previous) => previous.id === item.id && previous.status === 'completed',
            ),
        );
        if (completedNow) {
          notify('Sequence segment saved with its continuation frame');
          await refresh();
        }
      });
    }, 1100);
    return () => window.clearInterval(timer);
  }, [notify, refresh, sequence]);

  const addSegment = async () => {
    if (!sequence || !videoReady) return;
    setBusy('segment');
    setError('');
    try {
      const next = await api.post<VideoSequence>(`/video-sequences/${sequence.id}/segments`, {
        source_generation_id: continuationSourceId,
        motion_prompt: motionPrompt,
        negative_prompt: negativePrompt,
        profile,
        duration_profile: durationProfile,
        duration_seconds: duration,
        seed: Math.floor(Math.random() * 2_000_000_000),
        motion_asset_id: motionAssetId || null,
        motion_strength: 0.65,
      });
      setSequence(next);
      setContinuationSourceId(null);
      const jobId = next.segments.at(-1)?.job_id;
      if (jobId) onJob(await api.get<GenerationJob>(`/generations/${jobId}`));
      notify('Hardware-safe sequence segment queued locally');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The segment could not be queued.');
    } finally {
      setBusy('');
    }
  };

  const reorder = async (index: number, offset: number) => {
    if (!sequence) return;
    const target = index + offset;
    if (target < 0 || target >= sequence.segments.length) return;
    const ids = sequence.segments.map((item) => item.id);
    const [moved] = ids.splice(index, 1);
    if (!moved) return;
    ids.splice(target, 0, moved);
    setSequence(
      await api.put<VideoSequence>(`/video-sequences/${sequence.id}/order`, {
        segment_ids: ids,
      }),
    );
  };

  const selectContinuation = async (segmentId: string, generationId: string) => {
    setBusy(`continuation-${segmentId}`);
    try {
      const frame = await api.post<GenerationRecord>(`/videos/${generationId}/continuation-frame`, {
        timestamp_seconds: continuationTimes[segmentId] ?? 1,
      });
      setContinuationSourceId(frame.id);
      notify('Selected frame will begin the next segment');
      await refresh();
    } finally {
      setBusy('');
    }
  };

  const completedSegments = sequence?.segments.filter((item) => item.status === 'completed') ?? [];
  const selectedSegments = completedSegments.filter(
    (item) => !excludedSegmentIds.includes(item.id),
  );
  return (
    <div className="inpaint-overlay" role="dialog" aria-modal="true" aria-label="Video sequence">
      <div className="video-sequence-workspace">
        <header>
          <div>
            <span className="eyebrow">Hardware-safe sequence</span>
            <h2>Build a longer story, one controlled segment at a time.</h2>
            <p>
              Each segment keeps its own motion direction and feeds its final—or selected—frame into
              the next pass.
            </p>
          </div>
          <Button variant="ghost" onClick={onClose}>
            <X /> Close
          </Button>
        </header>
        {!sequence && !error && <LoadingView />}
        {sequence && (
          <div className="video-sequence-layout">
            <section className="video-segment-timeline">
              <article className="video-segment-card video-segment-card--source">
                {source.media_type === 'video' ? (
                  <LocalGenerationVideo generationId={source.id} label="Sequence source video" />
                ) : (
                  <LocalGenerationImage generationId={source.id} alt="Sequence source image" />
                )}
                <div>
                  <span className="eyebrow">Sequence source</span>
                  <strong>
                    {source.media_type === 'video' ? 'Existing generated clip' : 'First frame'}
                  </strong>
                </div>
              </article>
              {sequence.segments.map((segment, index) => (
                <article className="video-segment-card" key={segment.id}>
                  {segment.generation_id ? (
                    <LocalGenerationVideo
                      generationId={segment.generation_id}
                      label={`Sequence segment ${index + 1}`}
                    />
                  ) : (
                    <div className="video-segment-pending">
                      <Film />
                      <span>{stateLabels[segment.status] ?? segment.status}</span>
                    </div>
                  )}
                  <div className="video-segment-card__body">
                    <div>
                      <span className="eyebrow">Segment {index + 1}</span>
                      <strong>
                        {segment.duration_seconds}s · {segment.quality_profile}
                      </strong>
                    </div>
                    <p>{segment.motion_prompt}</p>
                    {segment.status === 'failed' && (
                      <p className="inline-error">{jobFailureSummary(segment.metadata.error)}</p>
                    )}
                    {segment.status === 'completed' && segment.generation_id && (
                      <div className="continuation-frame-control">
                        <label className="checkbox-row">
                          <input
                            type="checkbox"
                            checked={!excludedSegmentIds.includes(segment.id)}
                            onChange={(event) =>
                              setExcludedSegmentIds((current) =>
                                event.target.checked
                                  ? current.filter((id) => id !== segment.id)
                                  : [...current, segment.id],
                              )
                            }
                          />
                          Include in joined MP4
                        </label>
                        <label>
                          Selected continuation frame ·{' '}
                          {(continuationTimes[segment.id] ?? 1).toFixed(1)}s
                          <input
                            type="range"
                            min={0}
                            max={Math.max(0.1, segment.duration_seconds - 0.1)}
                            step={0.1}
                            value={continuationTimes[segment.id] ?? 1}
                            onChange={(event) =>
                              setContinuationTimes((current) => ({
                                ...current,
                                [segment.id]: Number(event.target.value),
                              }))
                            }
                          />
                        </label>
                        <Button
                          disabled={busy === `continuation-${segment.id}`}
                          onClick={() =>
                            void selectContinuation(segment.id, segment.generation_id as string)
                          }
                        >
                          Use selected frame next
                        </Button>
                      </div>
                    )}
                    <footer>
                      <Button
                        variant="ghost"
                        disabled={index === 0}
                        onClick={() => void reorder(index, -1)}
                        aria-label={`Move segment ${index + 1} earlier`}
                      >
                        Earlier
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={index === sequence.segments.length - 1}
                        onClick={() => void reorder(index, 1)}
                        aria-label={`Move segment ${index + 1} later`}
                      >
                        Later
                      </Button>
                      <Button
                        variant="ghost"
                        onClick={async () => {
                          setSequence(
                            await api.delete<VideoSequence>(
                              `/video-sequences/${sequence.id}/segments/${segment.id}`,
                            ),
                          );
                        }}
                      >
                        <Trash2 /> Remove
                      </Button>
                    </footer>
                  </div>
                </article>
              ))}
              {sequence.final_generation_id && (
                <article className="video-segment-card video-segment-card--final">
                  <LocalGenerationVideo
                    generationId={sequence.final_generation_id}
                    label="Joined final sequence"
                  />
                  <div>
                    <span className="eyebrow">Joined result</span>
                    <strong>Saved to Gallery with segment metadata</strong>
                  </div>
                </article>
              )}
            </section>
            <aside className="video-sequence-settings">
              <span className="eyebrow">Next segment</span>
              <h3>
                {continuationSourceId ? 'Selected frame ready' : 'Continue from the final frame'}
              </h3>
              <label>
                Motion direction
                <textarea
                  value={motionPrompt}
                  onChange={(event) => setMotionPrompt(event.target.value)}
                />
              </label>
              <label>
                Avoid
                <textarea
                  value={negativePrompt}
                  onChange={(event) => setNegativePrompt(event.target.value)}
                />
              </label>
              <div className="form-grid">
                <label>
                  Quality
                  <select
                    value={profile}
                    onChange={(event) => setProfile(event.target.value as typeof profile)}
                  >
                    <option value="safe">Safe · 512 × 768</option>
                    <option value="balanced">Balanced · 576 × 768</option>
                    <option value="quality">Quality · 640 × 832</option>
                  </select>
                </label>
                <label>
                  Duration
                  <select
                    value={durationProfile}
                    onChange={(event) => {
                      const next = event.target.value as typeof durationProfile;
                      setDurationProfile(next);
                      setDuration(next === 'safe' ? 2 : 4);
                    }}
                  >
                    <option value="safe">Safe · 2 seconds</option>
                    <option value="standard">Standard · 4 seconds</option>
                  </select>
                </label>
              </div>
              <label>
                Reference Motion · optional
                <select
                  value={motionAssetId}
                  onChange={(event) => setMotionAssetId(event.target.value)}
                >
                  <option value="">Prompt direction only</option>
                  {readyMotion.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <p className="video-duration-warning">
                {capabilities?.historical_samples
                  ? 'Time estimates use recent renders on this computer.'
                  : 'First-run estimates are conservative. Longer clips become substantially slower.'}
              </p>
              {error && <p className="inline-error">{error}</p>}
              <Button
                variant="primary"
                disabled={
                  !videoReady ||
                  !motionPrompt.trim() ||
                  busy === 'segment' ||
                  sequence.status === 'rendering'
                }
                onClick={() => void addSegment()}
              >
                <Plus /> Add and render segment
              </Button>
              <Button
                disabled={!selectedSegments.length || sequence.status === 'rendering'}
                onClick={async () => {
                  setBusy('join');
                  try {
                    const next = await api.post<VideoSequence>(
                      `/video-sequences/${sequence.id}/join`,
                      {
                        segment_ids: selectedSegments.map((item) => item.id),
                      },
                    );
                    setSequence(next);
                    await refresh();
                    notify('Selected segments joined into one local MP4');
                  } finally {
                    setBusy('');
                  }
                }}
              >
                <Film /> {busy === 'join' ? 'Joining…' : 'Join selected segments'}
              </Button>
              <Button variant="ghost" onClick={onDiagnostics}>
                <Gauge /> Open diagnostics
              </Button>
            </aside>
          </div>
        )}
        {error && !sequence && <p className="inline-error">{error}</p>}
      </div>
    </div>
  );
}

function LocalInpaintMask({ generationId }: { generationId: string }) {
  return (
    <AuthenticatedImage
      className="inpaint-mask-preview"
      media={{ entity: 'generation', id: generationId, variant: 'mask' }}
      alt="Persisted inpaint mask"
    />
  );
}

function InpaintWorkspace({
  source,
  refresh,
  notify,
  onClose,
  onJob,
  onDiagnostics,
}: {
  source: GenerationRecord;
  refresh: () => Promise<void>;
  notify: (message: string) => void;
  onClose: () => void;
  onJob: (job: GenerationJob) => void;
  onDiagnostics: () => void;
}) {
  const maskRef = useRef<HTMLCanvasElement>(null);
  const drawingRef = useRef(false);
  const lastPointRef = useRef<{ x: number; y: number } | null>(null);
  const panDragRef = useRef<{
    clientX: number;
    clientY: number;
    x: number;
    y: number;
  } | null>(null);
  const [sourceUrl, setSourceUrl] = useState('');
  const [tool, setTool] = useState<'brush' | 'eraser' | 'pan'>('brush');
  const [brushSize, setBrushSize] = useState(72);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [regionPrompt, setRegionPrompt] = useState(
    'tailored deep rose blazer with realistic fabric folds and editorial detail',
  );
  const [regionNegativePrompt, setRegionNegativePrompt] = useState(
    'text, watermark, malformed fabric, extra limbs, distorted anatomy',
  );
  const [strength, setStrength] = useState(0.62);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [resultGenerationId, setResultGenerationId] = useState('');
  const [error, setError] = useState('');
  const active = Boolean(job && !['completed', 'failed', 'cancelled'].includes(job.status));

  useEffect(() => {
    let mounted = true;
    void mediaCache
      .get({ entity: 'generation', id: source.id, variant: 'original' })
      .then((url) => {
        if (mounted) setSourceUrl(url);
      });
    return () => {
      mounted = false;
    };
  }, [source.id]);

  useEffect(() => {
    if (!active || !job) return;
    const timer = window.setInterval(() => {
      void api.get<GenerationJob>(`/generations/${job.id}`).then(async (next) => {
        setJob(next);
        onJob(next);
        if (next.status === 'completed' && next.result_generation_id) {
          setResultGenerationId(next.result_generation_id);
          await refresh();
          notify('Inpaint derivative saved locally; the original is unchanged');
        }
        if (next.status === 'failed') setError(next.error_message ?? 'The local inpaint failed');
      });
    }, 900);
    return () => window.clearInterval(timer);
  }, [active, job, notify, onJob, refresh]);

  const canvasPoint = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const canvas = maskRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) / rect.width) * canvas.width,
      y: ((event.clientY - rect.top) / rect.height) * canvas.height,
    };
  };
  const drawSegment = (from: { x: number; y: number }, to: { x: number; y: number }) => {
    const canvas = maskRef.current;
    const context = canvas?.getContext('2d');
    if (!context) return;
    context.save();
    context.globalCompositeOperation = tool === 'eraser' ? 'destination-out' : 'source-over';
    context.strokeStyle = '#ffffff';
    context.lineCap = 'round';
    context.lineJoin = 'round';
    context.lineWidth = brushSize;
    context.beginPath();
    context.moveTo(from.x, from.y);
    context.lineTo(to.x, to.y);
    context.stroke();
    context.restore();
  };
  const pointerDown = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    if (tool === 'pan') {
      panDragRef.current = { clientX: event.clientX, clientY: event.clientY, ...pan };
      return;
    }
    drawingRef.current = true;
    const point = canvasPoint(event);
    lastPointRef.current = point;
    drawSegment(point, point);
  };
  const pointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    if (tool === 'pan' && panDragRef.current) {
      setPan({
        x: panDragRef.current.x + event.clientX - panDragRef.current.clientX,
        y: panDragRef.current.y + event.clientY - panDragRef.current.clientY,
      });
      return;
    }
    if (!drawingRef.current || !lastPointRef.current) return;
    const point = canvasPoint(event);
    drawSegment(lastPointRef.current, point);
    lastPointRef.current = point;
  };
  const pointerUp = () => {
    drawingRef.current = false;
    lastPointRef.current = null;
    panDragRef.current = null;
  };
  const clearMask = () => {
    const canvas = maskRef.current;
    canvas?.getContext('2d')?.clearRect(0, 0, canvas.width, canvas.height);
  };
  const invertMask = () => {
    const canvas = maskRef.current;
    const context = canvas?.getContext('2d');
    if (!canvas || !context) return;
    const previous = document.createElement('canvas');
    previous.width = canvas.width;
    previous.height = canvas.height;
    previous.getContext('2d')?.drawImage(canvas, 0, 0);
    context.save();
    context.globalCompositeOperation = 'source-over';
    context.fillStyle = '#ffffff';
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.globalCompositeOperation = 'destination-out';
    context.drawImage(previous, 0, 0);
    context.restore();
  };
  const exportMask = () => {
    const canvas = maskRef.current;
    if (!canvas) throw new Error('The mask canvas is not ready');
    const output = document.createElement('canvas');
    output.width = canvas.width;
    output.height = canvas.height;
    const context = output.getContext('2d');
    if (!context) throw new Error('The mask canvas is unavailable');
    context.fillStyle = '#000000';
    context.fillRect(0, 0, output.width, output.height);
    context.drawImage(canvas, 0, 0);
    return output.toDataURL('image/png');
  };
  const submit = async () => {
    setError('');
    if (!regionPrompt.trim()) {
      setError('Describe what should appear inside the painted region.');
      return;
    }
    try {
      const next = await api.post<GenerationJob>('/generations', {
        operation: 'inpaint',
        source_generation_id: source.id,
        character_id: source.character_id ?? null,
        recipe_id: source.recipe_id ?? null,
        direction: regionPrompt,
        region_prompt: regionPrompt,
        region_negative_prompt: regionNegativePrompt,
        inpaint_mask_data_url: exportMask(),
        inpaint_strength: strength,
        model_alias: source.model_alias,
        seed: Math.floor(Math.random() * 2_000_000_000),
        width: 512,
        height: 512,
        steps: 25,
        guidance: 5.5,
      });
      setJob(next);
      onJob(next);
      notify('Local inpaint queued');
    } catch (nextError) {
      setError(
        nextError instanceof Error ? nextError.message : 'The local inpaint could not start',
      );
    }
  };

  return (
    <div className="inpaint-overlay" role="dialog" aria-modal="true" aria-label="Inpaint editor">
      <div className="inpaint-workspace">
        <header>
          <div>
            <span className="eyebrow">Full-resolution local editor</span>
            <h2>Paint only what should change.</h2>
            <p>The saved original is never overwritten.</p>
          </div>
          <Button variant="ghost" onClick={onClose} aria-label="Close inpaint editor">
            <X /> Close
          </Button>
        </header>
        {resultGenerationId ? (
          <div className="inpaint-comparison">
            <figure>
              <LocalGenerationImage generationId={source.id} alt="Original before inpaint" />
              <figcaption>Before · preserved original</figcaption>
            </figure>
            <figure>
              <LocalGenerationImage
                generationId={resultGenerationId}
                alt="Derivative after inpaint"
              />
              <figcaption>After · local derivative</figcaption>
            </figure>
          </div>
        ) : (
          <div className="inpaint-layout">
            <section className="inpaint-canvas-panel">
              <div className="inpaint-toolbar" aria-label="Mask tools">
                <Button
                  variant={tool === 'brush' ? 'primary' : 'ghost'}
                  onClick={() => setTool('brush')}
                  aria-pressed={tool === 'brush'}
                >
                  <Paintbrush /> Brush
                </Button>
                <Button
                  variant={tool === 'eraser' ? 'primary' : 'ghost'}
                  onClick={() => setTool('eraser')}
                  aria-pressed={tool === 'eraser'}
                >
                  <Eraser /> Eraser
                </Button>
                <Button
                  variant={tool === 'pan' ? 'primary' : 'ghost'}
                  onClick={() => setTool('pan')}
                  aria-pressed={tool === 'pan'}
                >
                  <Move /> Pan
                </Button>
                <label>
                  Brush {brushSize}px
                  <input
                    type="range"
                    min={12}
                    max={240}
                    value={brushSize}
                    onChange={(event) => setBrushSize(Number(event.target.value))}
                  />
                </label>
                <Button variant="ghost" onClick={clearMask}>
                  Clear
                </Button>
                <Button variant="ghost" onClick={invertMask}>
                  Invert
                </Button>
              </div>
              <div className="inpaint-viewport">
                <div
                  className="inpaint-stage"
                  style={{
                    aspectRatio: `${source.width} / ${source.height}`,
                    width: `min(100%, ${(68 * source.width) / source.height}vh)`,
                    transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
                  }}
                >
                  {sourceUrl && <img src={sourceUrl} alt="Source to edit" draggable={false} />}
                  <canvas
                    ref={maskRef}
                    className={`inpaint-mask inpaint-mask--${tool}`}
                    width={source.width}
                    height={source.height}
                    onPointerDown={pointerDown}
                    onPointerMove={pointerMove}
                    onPointerUp={pointerUp}
                    onPointerCancel={pointerUp}
                    aria-label="Painted inpaint mask overlay"
                  />
                </div>
              </div>
              <div className="inpaint-zoom">
                <Scan />
                <label>
                  Zoom {Math.round(zoom * 100)}%
                  <input
                    type="range"
                    min={0.5}
                    max={3}
                    step={0.1}
                    value={zoom}
                    onChange={(event) => setZoom(Number(event.target.value))}
                  />
                </label>
                <Button
                  variant="ghost"
                  onClick={() => {
                    setZoom(1);
                    setPan({ x: 0, y: 0 });
                  }}
                >
                  Fit
                </Button>
              </div>
            </section>
            <aside className="inpaint-settings">
              <label>
                Region prompt
                <textarea
                  value={regionPrompt}
                  onChange={(event) => setRegionPrompt(event.target.value)}
                />
              </label>
              <label>
                Region negative prompt
                <textarea
                  value={regionNegativePrompt}
                  onChange={(event) => setRegionNegativePrompt(event.target.value)}
                />
              </label>
              <label>
                Denoise strength <span>{strength.toFixed(2)}</span>
                <input
                  type="range"
                  min={0.05}
                  max={1}
                  step={0.05}
                  value={strength}
                  onChange={(event) => setStrength(Number(event.target.value))}
                />
              </label>
              <p className="field-help">
                Vanta composites the generated region over the untouched source pixels outside the
                mask.
              </p>
              {job && (
                <JobProgressPanel
                  job={job}
                  compact
                  onCancel={() => {
                    void api.post<GenerationJob>(`/generations/${job.id}/cancel`).then((next) => {
                      setJob(next);
                      onJob(next);
                    });
                  }}
                  onDiagnostics={onDiagnostics}
                />
              )}
              {error && <p className="error-text">{error}</p>}
              <Button
                variant="primary"
                disabled={!sourceUrl}
                onClick={() =>
                  void (active && job ? api.post(`/generations/${job.id}/cancel`) : submit())
                }
              >
                {active ? (
                  <>
                    <X /> Cancel inpaint
                  </>
                ) : (
                  <>
                    <Sparkles /> Generate masked edit
                  </>
                )}
              </Button>
            </aside>
          </div>
        )}
      </div>
    </div>
  );
}

function GalleryScreen({
  items,
  motion,
  videoReady,
  editingReady,
  notify,
  refresh,
  onGenerateSimilar,
  onCreateVariation,
  onUpscale,
  initialSelectionId,
  onSelectionHandled,
  onJob,
  onDiagnostics,
}: {
  items: GenerationRecord[];
  motion: MotionAsset[];
  videoReady: boolean;
  editingReady: boolean;
  notify: (message: string) => void;
  refresh: () => Promise<void>;
  onGenerateSimilar: (draft: Record<string, unknown>) => void;
  onCreateVariation: (generation: GenerationRecord) => void;
  onUpscale: (generation: GenerationRecord) => Promise<void>;
  initialSelectionId: string | null;
  onSelectionHandled: () => void;
  onJob: (job: GenerationJob) => void;
  onDiagnostics: () => void;
}) {
  const [filter, setFilter] = useState('all');
  const [selected, setSelected] = useState<GenerationRecord | null>(null);
  const [inpaintSource, setInpaintSource] = useState<GenerationRecord | null>(null);
  const [videoSource, setVideoSource] = useState<GenerationRecord | null>(null);
  const [sequenceSource, setSequenceSource] = useState<GenerationRecord | null>(null);
  const filtered = filter === 'all' ? items : items.filter((item) => item.model_alias === filter);
  useEffect(() => {
    if (!initialSelectionId) return;
    const generation = items.find((item) => item.id === initialSelectionId);
    if (generation) setSelected(generation);
    onSelectionHandled();
  }, [initialSelectionId, items, onSelectionHandled]);
  return (
    <div className="screen gallery-screen">
      {inpaintSource && (
        <InpaintWorkspace
          source={inpaintSource}
          refresh={refresh}
          notify={notify}
          onClose={() => setInpaintSource(null)}
          onJob={onJob}
          onDiagnostics={onDiagnostics}
        />
      )}
      {videoSource && (
        <VideoWorkspace
          source={videoSource}
          motion={motion}
          videoReady={videoReady}
          refresh={refresh}
          notify={notify}
          onClose={() => setVideoSource(null)}
          onJob={onJob}
          onDiagnostics={onDiagnostics}
        />
      )}
      {sequenceSource && (
        <VideoSequenceWorkspace
          source={sequenceSource}
          motion={motion}
          videoReady={videoReady}
          refresh={refresh}
          notify={notify}
          onClose={() => setSequenceSource(null)}
          onJob={onJob}
          onDiagnostics={onDiagnostics}
        />
      )}
      <PageTitle
        eyebrow="Local archive"
        title="Every frame remembers."
        body="Prompt, seed, recipe, profile, and disclosure metadata travel with the result."
        actions={
          <div className="filter-select">
            <SlidersHorizontal />
            <select
              aria-label="Filter gallery"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            >
              <option value="all">All generations</option>
              <option value="photoreal_max">Realistic — Maximum</option>
              <option value="video_ltx_2b">Local video — LTXV</option>
              <option value="photoreal_balanced">Realistic — Balanced</option>
              <option value="preview_fast">Preview — Fast</option>
            </select>
          </div>
        }
      />
      {filtered.length === 0 ? (
        <EmptyState
          title="Nothing in this view"
          body="Your generated images will appear here without leaving this device."
        />
      ) : (
        <div className="gallery-grid">
          {filtered.map((item, index) => (
            <button
              className={`generation-tile generation-tile--${index}`}
              key={item.id}
              onClick={() => setSelected(item)}
            >
              <div className="generated-study">
                <LocalGenerationImage
                  generationId={item.id}
                  alt={`Generated image ${item.id}`}
                  thumbnail
                  mediaType={item.media_type}
                />
                <span className="disclosure">
                  {item.media_type === 'video' ? <Film /> : <Sparkles />} AI-created{' '}
                  {item.media_type}
                </span>
                <span className="tile-seed">#{item.seed}</span>
              </div>
              <div>
                <strong>{item.metadata.recipe ?? 'Local generation'}</strong>
                <span>
                  {item.width} × {item.height} · {item.model_alias}
                  {item.media_type === 'video' && item.metadata.duration_seconds
                    ? ` · ${item.metadata.duration_seconds}s`
                    : ''}
                </span>
              </div>
            </button>
          ))}
        </div>
      )}
      <Drawer
        open={selected !== null}
        title={selected?.metadata.recipe ?? ''}
        onClose={() => setSelected(null)}
      >
        {selected && (
          <div className="metadata-drawer">
            <div className="drawer-preview generated-study">
              {selected.media_type === 'video' ? (
                <LocalGenerationVideo generationId={selected.id} label="Selected generated video" />
              ) : (
                <LocalGenerationImage generationId={selected.id} alt="Selected generated image" />
              )}
            </div>
            <div className="metadata-section">
              <span className="eyebrow">Reproducible metadata</span>
              <dl>
                <div>
                  <dt>Media</dt>
                  <dd>{selected.media_type === 'video' ? 'Generated video' : 'Generated image'}</dd>
                </div>
                <div>
                  <dt>Model profile</dt>
                  <dd>{selected.model_alias}</dd>
                </div>
                <div>
                  <dt>Seed</dt>
                  <dd>{selected.seed}</dd>
                </div>
                <div>
                  <dt>Canvas</dt>
                  <dd>
                    {selected.width} × {selected.height}
                  </dd>
                </div>
                <div>
                  <dt>Steps / guidance</dt>
                  <dd>
                    {selected.metadata.steps} / {selected.metadata.guidance}
                  </dd>
                </div>
                <div>
                  <dt>LoRA stack</dt>
                  <dd>
                    {selected.metadata.loras?.length
                      ? selected.metadata.loras.map((lora) => lora.name).join(', ')
                      : 'None'}
                  </dd>
                </div>
                <div>
                  <dt>Pose control</dt>
                  <dd>
                    {selected.metadata.pose_control
                      ? `${selected.metadata.pose_control.name} · ${selected.metadata.pose_control.strength.toFixed(2)}`
                      : 'None'}
                  </dd>
                </div>
                <div>
                  <dt>Workflow</dt>
                  <dd>{selected.metadata.workflow_version ?? 'Legacy local workflow'}</dd>
                </div>
                <div>
                  <dt>Derivative source</dt>
                  <dd>{selected.metadata.derivative_of ?? 'Original generation'}</dd>
                </div>
                {selected.metadata.variation_mode && selected.metadata.derivative_of && (
                  <div>
                    <dt>Variation goal</dt>
                    <dd>
                      {selected.metadata.variation_mode.replaceAll('_', ' ')} · denoise{' '}
                      {selected.metadata.variation_strength?.toFixed(2)}
                    </dd>
                  </div>
                )}
                {selected.metadata.inpaint && (
                  <div>
                    <dt>Inpaint preservation</dt>
                    <dd>
                      Outside-mask composite · denoise{' '}
                      {selected.metadata.inpaint.denoise_strength.toFixed(2)}
                    </dd>
                  </div>
                )}
                {selected.media_type === 'video' && (
                  <div>
                    <dt>Playback</dt>
                    <dd>
                      {selected.metadata.duration_seconds}s · {selected.metadata.fps} fps ·{' '}
                      {selected.metadata.frame_count} frames
                    </dd>
                  </div>
                )}
                {selected.metadata.motion_reference && (
                  <div>
                    <dt>Reference Motion</dt>
                    <dd>{selected.metadata.motion_reference.name} · broad movement only</dd>
                  </div>
                )}
                <div>
                  <dt>Disclosure</dt>
                  <dd>
                    {selected.metadata.disclosure ? 'AI-created metadata attached' : 'Not attached'}
                  </dd>
                </div>
              </dl>
            </div>
            <div className="metadata-section">
              <span className="eyebrow">Prompt</span>
              <p>{selected.prompt}</p>
            </div>
            {selected.metadata.inpaint && (
              <div className="metadata-section">
                <span className="eyebrow">Persisted edit mask</span>
                <LocalInpaintMask generationId={selected.id} />
                <p>{selected.metadata.inpaint.region_prompt}</p>
                <small>SHA-256 {selected.metadata.inpaint.mask_sha256}</small>
              </div>
            )}
            <div className="gallery-file-actions" aria-label="Managed file actions">
              <Button
                variant="ghost"
                onClick={() =>
                  void openManagedMedia({
                    entity: 'generation',
                    id: selected.id,
                    variant: selected.media_type === 'video' ? 'video' : 'original',
                  })
                }
              >
                <Play /> Open {selected.media_type === 'video' ? 'video' : 'file'}
              </Button>
              <Button
                variant="ghost"
                onClick={() =>
                  void revealManagedMedia({
                    entity: 'generation',
                    id: selected.id,
                    variant: selected.media_type === 'video' ? 'video' : 'original',
                  })
                }
              >
                <FolderLock /> Show in folder
              </Button>
              <Button
                variant="ghost"
                onClick={async () => {
                  const destination = await saveManagedMediaCopy({
                    entity: 'generation',
                    id: selected.id,
                    variant: selected.media_type === 'video' ? 'video' : 'original',
                  });
                  notify(`Saved a copy to ${destination}`);
                }}
              >
                <FileDown /> Save a copy
              </Button>
              <Button
                variant="ghost"
                onClick={async () => {
                  await copyManagedMediaPath({
                    entity: 'generation',
                    id: selected.id,
                    variant: selected.media_type === 'video' ? 'video' : 'original',
                  });
                  notify('Managed file path copied to the clipboard');
                }}
              >
                <Copy /> Copy file path
              </Button>
            </div>
            {selected.media_type === 'image' && (
              <>
                <Button
                  variant="primary"
                  onClick={async () => {
                    const draft = await api.get<Record<string, unknown>>(
                      `/generations/${selected.id}/similar`,
                    );
                    onGenerateSimilar(draft);
                    notify('Original settings restored in Create');
                  }}
                >
                  <Copy /> Generate similar
                </Button>
                <Button
                  variant="ghost"
                  disabled={!videoReady}
                  title={
                    videoReady
                      ? 'Animate this image locally'
                      : 'Install Image-to-Video in Models & Engine first'
                  }
                  onClick={() => {
                    setVideoSource(selected);
                    setSelected(null);
                  }}
                >
                  <Film /> Animate image
                </Button>
                <Button variant="ghost" onClick={() => onCreateVariation(selected)}>
                  <Sparkles /> Create variation
                </Button>
                <Button
                  variant="ghost"
                  disabled={!editingReady}
                  title={
                    editingReady
                      ? 'Paint a local edit mask'
                      : 'Start and verify Image Editing in Models & Engine first'
                  }
                  onClick={() => {
                    setInpaintSource(selected);
                    setSelected(null);
                  }}
                >
                  <Paintbrush /> Inpaint selected region
                </Button>
                <Button variant="ghost" onClick={() => void onUpscale(selected)}>
                  <Sparkles /> Upscale 2×
                </Button>
              </>
            )}
            <Button
              variant="ghost"
              disabled={!videoReady}
              onClick={() => {
                setSequenceSource(selected);
                setSelected(null);
              }}
            >
              <Film /> {selected.media_type === 'video' ? 'Extend clip' : 'Create sequence'}
            </Button>
            <Button
              variant="ghost"
              onClick={async () => {
                await api.delete(`/generations/${selected.id}`);
                mediaCache.invalidateEntity('generation', selected.id);
                setSelected(null);
                await refresh();
                notify('Generation removed from your local library');
              }}
            >
              <Trash2 /> Delete from library
            </Button>
          </div>
        )}
      </Drawer>
    </div>
  );
}

function EngineScreen({
  data,
  refresh,
  notify,
  diagnosticsRequest,
}: {
  data: AppData;
  refresh: () => Promise<void>;
  notify: (message: string) => void;
  diagnosticsRequest: number;
}) {
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [busy, setBusy] = useState('');
  useEffect(() => {
    if (!diagnosticsRequest) return;
    void api.get<Diagnostics>('/engine/diagnostics').then(setDiagnostics);
  }, [diagnosticsRequest]);
  const componentAction = async (item: EngineComponent, action: string) => {
    setBusy(item.id);
    try {
      await api.post(`/engine/components/${item.id}/${action}`);
      notify(`${item.display_name}: ${action.replace('_', ' ')}`);
      await refresh();
    } finally {
      setBusy('');
    }
  };
  const packAction = async (item: ModelPack, action: string) => {
    setBusy(item.id);
    try {
      await api.post(`/engine/model-packs/${item.id}/${action}`);
      notify(`${item.display_name}: ${action.replace('_', ' ')}`);
      await refresh();
    } finally {
      setBusy('');
    }
  };
  const importLocalModel = async (
    alias: 'photoreal_balanced' | 'preview_fast' | 'photoreal_max' = 'photoreal_balanced',
  ) => {
    const sourcePath = await chooseLocalModelFile();
    if (!sourcePath) return;
    setBusy('model-import');
    try {
      await api.post('/engine/models/import', {
        source_path: sourcePath,
        alias,
        license_notes: '',
      });
      notify(
        alias === 'photoreal_max'
          ? 'Self-contained FLUX checkpoint imported and verified'
          : 'Local SDXL checkpoint imported and verified',
      );
      await refresh();
    } finally {
      setBusy('');
    }
  };
  const importUpscaler = async (alias: 'realesrgan_x2plus' | 'ultrasharp_x4') => {
    const sourcePath = await chooseLocalUpscalerFile();
    if (!sourcePath) return;
    setBusy(`upscaler-${alias}`);
    try {
      await api.post('/engine/upscalers/import', {
        source_path: sourcePath,
        alias,
        license_notes: '',
      });
      notify(
        alias === 'realesrgan_x2plus'
          ? '2× local upscaler imported'
          : 'Optional 4× profile imported',
      );
      await refresh();
    } finally {
      setBusy('');
    }
  };
  const importLocalLora = async () => {
    const sourcePath = await chooseLocalLoraFile();
    if (!sourcePath) return;
    const filename = sourcePath.split(/[\\/]/).pop() ?? 'Local LoRA';
    setBusy('lora-import');
    try {
      await api.post('/loras/import', {
        source_path: sourcePath,
        name: filename.replace(/\.safetensors$/i, ''),
        source_notes: 'Imported from a user-selected local file',
        license_notes: '',
        trigger_token: '',
        default_strength: 1,
        default_clip_strength: 1,
      });
      notify('Local LoRA imported and verified');
      await refresh();
    } finally {
      setBusy('');
    }
  };
  return (
    <div className="screen engine-screen">
      <PageTitle
        eyebrow="Models & engine"
        title="Power, without the machinery."
        body="Capabilities are managed, verified, and repaired by Vanta. Large models remain your choice."
        actions={
          <>
            <Button onClick={() => void importLocalModel()} disabled={busy === 'model-import'}>
              <Upload /> Import local model
            </Button>
            <Button onClick={() => void importLocalLora()} disabled={busy === 'lora-import'}>
              <Plus /> Import LoRA
            </Button>
            <Button onClick={async () => setDiagnostics(await api.get('/engine/diagnostics'))}>
              <Info /> Diagnostics
            </Button>
          </>
        }
      />
      <Panel className="hardware-strip">
        <div className="gpu-mark">
          <Gauge />
        </div>
        <div>
          <span className="eyebrow">Detected local hardware</span>
          <h2>{data.hardware.gpu_name}</h2>
        </div>
        <dl>
          <div>
            <dt>VRAM</dt>
            <dd>{data.hardware.vram_gb} GB</dd>
          </div>
          <div>
            <dt>Memory</dt>
            <dd>{data.hardware.ram_gb} GB</dd>
          </div>
          <div>
            <dt>Free disk</dt>
            <dd>{data.hardware.free_disk_gb} GB</dd>
          </div>
        </dl>
        <StatusPill
          tone={data.packs.some((item) => item.is_default && item.verified) ? 'ready' : 'warning'}
        >
          {data.packs.some((item) => item.is_default && item.verified)
            ? 'Balanced verified'
            : 'Setup needed'}
        </StatusPill>
      </Panel>
      <section className="engine-section">
        <div className="section-heading">
          <div>
            <span className="eyebrow">Required capabilities</span>
            <h2>Core engine</h2>
          </div>
          <p>Installed from reviewed Vanta manifests. No arbitrary scripts are executed.</p>
        </div>
        <div className="capability-grid">
          {data.components.map((item) => (
            <Panel className="capability-card" key={item.id}>
              <header>
                <div className={`capability-icon capability-icon--${stateTone(item.state)}`}>
                  {item.state === 'ready' ? (
                    <Check />
                  ) : item.state === 'repair_needed' ? (
                    <Wrench />
                  ) : (
                    <Download />
                  )}
                </div>
                <StatusPill tone={stateTone(item.state)}>
                  {stateLabels[item.state] ?? item.state}
                </StatusPill>
              </header>
              <h3>{item.display_name}</h3>
              <p>{item.last_health_message}</p>
              <details className="component-provenance">
                <summary>Version & provenance</summary>
                <dl>
                  <div>
                    <dt>Version</dt>
                    <dd>{item.version}</dd>
                  </div>
                  <div>
                    <dt>Revision</dt>
                    <dd>{item.revision}</dd>
                  </div>
                  <div>
                    <dt>License</dt>
                    <dd>{item.license.name}</dd>
                  </div>
                  {item.sha256 && (
                    <div>
                      <dt>SHA-256</dt>
                      <dd className="hash-value">{item.sha256}</dd>
                    </div>
                  )}
                  {item.source && (
                    <div>
                      <dt>Source</dt>
                      <dd>{item.source}</dd>
                    </div>
                  )}
                </dl>
              </details>
              <div className="capability-list">
                {item.capabilities.map((capability) => (
                  <span key={capability}>
                    <Check />
                    {capability}
                  </span>
                ))}
              </div>
              {item.state === 'installing' && (
                <div className="progress" aria-label={`${item.progress}% installed`}>
                  <span style={{ width: `${item.progress}%` }} />
                </div>
              )}
              <footer>
                {item.state === 'unsupported' ? (
                  <Button disabled>Coming later</Button>
                ) : item.state === 'ready' ? (
                  <>
                    <Button
                      onClick={() => void componentAction(item, 'verify')}
                      disabled={busy === item.id}
                    >
                      <ShieldCheck /> Verify
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={() => void componentAction(item, 'update')}
                      disabled={busy === item.id}
                    >
                      Update
                    </Button>
                    {item.id === 'workflow-runtime' && (
                      <>
                        <Button
                          variant="ghost"
                          onClick={() => void componentAction(item, 'restart')}
                          disabled={busy === item.id}
                        >
                          Restart
                        </Button>
                        <Button
                          variant="ghost"
                          onClick={() => void componentAction(item, 'pause')}
                          disabled={busy === item.id}
                        >
                          Pause
                        </Button>
                      </>
                    )}
                    {[
                      'workflow-runtime',
                      'pose-control',
                      'identity-lock',
                      'lora-training',
                      'captioning',
                    ].includes(item.id) && (
                      <Button
                        variant="ghost"
                        onClick={() => {
                          if (
                            window.confirm(
                              `Remove ${item.display_name}? User projects and media remain.`,
                            )
                          ) {
                            void componentAction(item, 'remove');
                          }
                        }}
                        disabled={busy === item.id}
                      >
                        Remove
                      </Button>
                    )}
                  </>
                ) : (
                  <Button
                    variant={item.state === 'repair_needed' ? 'primary' : 'secondary'}
                    onClick={() =>
                      void componentAction(
                        item,
                        ['stopped', 'paused'].includes(item.state)
                          ? 'resume'
                          : item.state === 'repair_needed'
                            ? 'repair'
                            : item.state === 'installing'
                              ? 'cancel'
                              : 'install',
                      )
                    }
                    disabled={busy === item.id}
                  >
                    {['stopped', 'paused'].includes(item.state) ? (
                      <>
                        <Play /> Resume {item.display_name}
                      </>
                    ) : item.state === 'repair_needed' ? (
                      <>
                        <Wrench /> Repair {item.display_name}
                      </>
                    ) : item.state === 'installing' ? (
                      'Cancel'
                    ) : (
                      <>Install {item.display_name}</>
                    )}
                  </Button>
                )}
              </footer>
            </Panel>
          ))}
        </div>
      </section>
      <section className="engine-section">
        <div className="section-heading">
          <div>
            <span className="eyebrow">Selectable downloads</span>
            <h2>Model packs</h2>
          </div>
          <p>License, source, hash, hardware fit, and disk use stay visible before installation.</p>
        </div>
        <div className="model-list">
          {data.packs.map((item) => (
            <Panel
              className={`model-card ${item.is_default ? 'model-card--default' : ''}`}
              key={item.id}
            >
              <div className="model-card__lead">
                <div className="model-family">{item.model_family}</div>
                <div>
                  <div className="model-title">
                    <h3>{item.display_name}</h3>
                    {item.recommended && (
                      <span className="recommended">
                        <Star /> Recommended for 12 GB
                      </span>
                    )}
                    {item.is_default && <StatusPill tone="ready">Default</StatusPill>}
                  </div>
                  <p>{item.capabilities.join(' · ')}</p>
                  <div className="model-meta">
                    <span>
                      <Database /> {item.disk_gb} GB
                    </span>
                    <span>
                      <Gauge /> {item.hardware.minimum_vram_gb} GB minimum ·{' '}
                      {item.hardware.recommended_vram_gb} GB ideal
                    </span>
                    <span>
                      <ShieldCheck /> {item.license.name}
                    </span>
                    {item.filename && (
                      <span>
                        <Box /> {item.filename}
                      </span>
                    )}
                  </div>
                  {item.sha256 && <p className="hash-value">SHA-256 {item.sha256}</p>}
                  {item.source_information && <p>{item.source_information}</p>}
                </div>
              </div>
              <div className="model-actions">
                {item.state === 'installing' && (
                  <div className="progress">
                    <span style={{ width: `${item.progress}%` }} />
                  </div>
                )}
                {!item.installed &&
                  ['photoreal_balanced', 'preview_fast', 'photoreal_max'].includes(item.alias) && (
                    <Button
                      onClick={() =>
                        void importLocalModel(
                          item.alias as 'photoreal_balanced' | 'preview_fast' | 'photoreal_max',
                        )
                      }
                      disabled={busy === 'model-import'}
                    >
                      <Upload /> Import local {item.alias === 'photoreal_max' ? 'FLUX' : 'model'}
                    </Button>
                  )}
                {!item.installed &&
                  !['photoreal_balanced', 'preview_fast', 'photoreal_max'].includes(item.alias) &&
                  (item.alias === 'identity_plus_face_sdxl' || item.alias === 'video_ltx_2b' ? (
                    <Button
                      onClick={() => void packAction(item, 'install')}
                      disabled={busy === item.id}
                    >
                      <Download /> Install reviewed pack
                    </Button>
                  ) : item.alias === 'realesrgan_x2plus' || item.alias === 'ultrasharp_x4' ? (
                    <Button
                      onClick={() =>
                        void importUpscaler(item.alias as 'realesrgan_x2plus' | 'ultrasharp_x4')
                      }
                      disabled={busy === `upscaler-${item.alias}`}
                    >
                      <Upload /> Import local pack
                    </Button>
                  ) : item.alias === 'pose_xinsir_sdxl' ? (
                    <Button
                      onClick={() => void packAction(item, 'install')}
                      disabled={busy === item.id}
                    >
                      <Download /> Install reviewed pack
                    </Button>
                  ) : (
                    <Button disabled>Coming later</Button>
                  ))}
                {item.installed && !item.is_default && (
                  <Button
                    variant="primary"
                    onClick={() => void packAction(item, 'set_default')}
                    disabled={busy === item.id}
                  >
                    Switch default
                  </Button>
                )}
                {item.installed && (
                  <Button
                    variant="ghost"
                    onClick={() => void packAction(item, 'verify')}
                    disabled={busy === item.id}
                  >
                    Verify
                  </Button>
                )}
                {item.installed && !item.is_default && (
                  <Button
                    variant="ghost"
                    onClick={() => void packAction(item, 'remove')}
                    disabled={busy === item.id}
                  >
                    Remove
                  </Button>
                )}
              </div>
            </Panel>
          ))}
        </div>
      </section>
      <section className="engine-section">
        <div className="section-heading">
          <div>
            <span className="eyebrow">Reusable adapters</span>
            <h2>Imported & trained LoRAs</h2>
          </div>
          <p>
            Every adapter retains its family, trigger, source notes, license notes, size and hash.
          </p>
        </div>
        {data.loras.length ? (
          <div className="model-list">
            {data.loras.map((item) => (
              <Panel className="model-card" key={item.id}>
                <div className="model-card__lead">
                  <div className="model-family">{item.model_family}</div>
                  <div>
                    <div className="model-title">
                      <h3>{item.name}</h3>
                    </div>
                    <p>{item.trigger_token || 'No trigger token recorded'}</p>
                    <div className="model-meta">
                      <span>
                        <Database /> {(item.file_size / 1_000_000).toFixed(1)} MB
                      </span>
                      <span>
                        <ShieldCheck /> {item.license_notes || 'User-provided license notes'}
                      </span>
                    </div>
                    <p className="hash-value">SHA-256 {item.sha256}</p>
                  </div>
                </div>
                <div className="model-actions">
                  <StatusPill tone={item.verification_state === 'ready' ? 'ready' : 'danger'}>
                    {stateLabels[item.verification_state] ?? item.verification_state}
                  </StatusPill>
                  <Button
                    variant="ghost"
                    onClick={async () => {
                      await api.post(`/loras/${item.id}/verify`);
                      notify(`${item.name} verified`);
                      await refresh();
                    }}
                  >
                    Verify
                  </Button>
                  {item.verification_state === 'repair_needed' && (
                    <Button
                      variant="primary"
                      onClick={async () => {
                        await api.post(`/loras/${item.id}/repair`);
                        notify(`${item.name} repaired from its verified original`);
                        await refresh();
                      }}
                    >
                      Repair
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    onClick={async () => {
                      if (
                        !window.confirm(
                          `Remove ${item.name}? Character assignments will also be removed.`,
                        )
                      )
                        return;
                      await api.delete(`/loras/${item.id}`);
                      notify(`${item.name} removed`);
                      await refresh();
                    }}
                  >
                    Remove
                  </Button>
                </div>
              </Panel>
            ))}
          </div>
        ) : (
          <EmptyState
            title="No LoRAs installed"
            body="Import an owned compatible SDXL or FLUX LoRA, or train one locally."
          />
        )}
      </section>
      <Drawer
        open={diagnostics !== null}
        title="Local diagnostics"
        onClose={() => setDiagnostics(null)}
      >
        {diagnostics && (
          <div className="diagnostics">
            <div className="diagnostic-summary">
              <ShieldCheck />
              <p>{diagnostics.summary}</p>
            </div>
            <h3>Human-readable checks</h3>
            {diagnostics.messages.map((message) => (
              <div className="diagnostic-line" key={message}>
                <Check />
                {message}
              </div>
            ))}
            <details>
              <summary>Raw local logs</summary>
              <pre>{diagnostics.raw_logs.join('\n')}</pre>
            </details>
            <Button onClick={() => void exportDiagnostics()}>
              <FileDown /> Export support bundle
            </Button>
            <Button onClick={() => void openLocalPath('logs')}>
              <FolderLock /> Open logs
            </Button>
            {diagnostics.system && (
              <dl className="runtime-diagnostics">
                {Object.entries(diagnostics.system).map(([key, value]) => (
                  <div key={key}>
                    <dt>{key.replaceAll('_', ' ')}</dt>
                    <dd>{String(value ?? 'Not running')}</dd>
                  </div>
                ))}
              </dl>
            )}
          </div>
        )}
      </Drawer>
    </div>
  );
}

function SettingsScreen({
  settings,
  hardware,
  refresh,
  notify,
}: {
  settings: SettingsRecord;
  hardware: AppData['hardware'];
  refresh: () => Promise<void>;
  notify: (message: string) => void;
}) {
  const [storage, setStorage] = useState<StorageInfo | null>(null);
  const [mediaRepair, setMediaRepair] = useState<{
    records_scanned: number;
    ready_files: number;
    regenerated_derivatives: number;
    normalized_paths: number;
    missing_originals: unknown[];
    invalid_files: unknown[];
  } | null>(null);
  const [repairingMedia, setRepairingMedia] = useState(false);
  useEffect(() => {
    let active = true;
    const loadStorage = async () => {
      try {
        const next = await getStorageInfo();
        if (active) setStorage(next);
      } catch {
        // Storage controls are native-only; development web preview keeps Settings usable.
      }
    };
    void loadStorage();
    const interval = window.setInterval(() => void loadStorage(), 1000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, []);
  const update = async (key: string, value: string) => {
    await api.put(`/settings/${key}`, { value });
    notify('Setting saved locally');
    await refresh();
  };
  return (
    <div className="screen settings-screen">
      <PageTitle
        eyebrow="Settings"
        title="Your studio stays yours."
        body="Paths, startup, and creative defaults — with no account or cloud layer."
      />
      <div className="settings-layout">
        <div>
          <Panel className="settings-panel">
            <div className="settings-heading">
              <FolderLock />
              <div>
                <h2>Local paths</h2>
                <p>Vanta-owned data remains visible and recoverable.</p>
              </div>
            </div>
            <div className="path-list">
              <label>
                Studio data
                <div>
                  <input value={settings.paths.data} readOnly />
                  <Button
                    aria-label="Open studio data folder"
                    onClick={() => void openLocalPath('data')}
                  >
                    <MoreHorizontal />
                  </Button>
                </div>
              </label>
              <label>
                Model packs
                <div>
                  <input value={settings.paths.models} readOnly />
                  <Button
                    aria-label="Open model pack folder"
                    onClick={() => void openLocalPath('models')}
                  >
                    <MoreHorizontal />
                  </Button>
                </div>
              </label>
              <label>
                Database
                <div>
                  <input value={settings.paths.database} readOnly />
                  <Button
                    aria-label="Show database location"
                    onClick={() => void openLocalPath('database')}
                  >
                    <MoreHorizontal />
                  </Button>
                </div>
              </label>
            </div>
            <div className="recovery-note">
              <Info />
              <span>
                <strong>Recovery behavior</strong> Back up the studio data folder. Numbered
                migrations preserve the database across upgrades; built-in presets can be restored
                independently.
              </span>
            </div>
          </Panel>
          <Panel className="settings-panel">
            <div className="settings-heading">
              <Database />
              <div>
                <h2>Storage</h2>
                <p>Local cache and generated media allocation.</p>
              </div>
            </div>
            <div className="storage-legend">
              <span>
                <i />
                Vanta-managed files
              </span>
              <span>{hardware.free_disk_gb.toFixed(1)} GB free</span>
            </div>
            {storage && (
              <div className="media-repair-summary" role="status">
                <strong>
                  {formatBytes(storage.current_bytes)} across {storage.current_files} files
                </strong>
                <span>Studio data: {storage.current_root}</span>
                {storage.destination && (
                  <span>
                    {storage.phase} · {storage.copied_files} / {storage.total_files} files ·{' '}
                    {formatBytes(storage.copied_bytes)} / {formatBytes(storage.total_bytes)}
                    {storage.eta_seconds != null
                      ? ` · ~${formatDuration(storage.eta_seconds)} remaining`
                      : ''}
                  </span>
                )}
                {storage.last_error && <span className="inline-error">{storage.last_error}</span>}
              </div>
            )}
            <div className="gallery-file-actions">
              <Button onClick={() => void openLocalPath('data')}>
                <FolderLock /> Open storage folder
              </Button>
              <Button
                variant="ghost"
                onClick={async () => {
                  const destination = await chooseStorageLocation();
                  if (!destination) return;
                  if (
                    !window.confirm(
                      `Move studio data to ${destination}? Vanta will copy, verify, switch only after a healthy restart, and keep the original until you remove it yourself.`,
                    )
                  )
                    return;
                  setStorage(await startStorageMove(destination));
                  notify(
                    'Safe studio-data move started; Vanta will keep the original until verification succeeds.',
                  );
                }}
                disabled={storage?.can_cancel}
              >
                <Move /> Move existing studio data
              </Button>
              {storage?.can_cancel && (
                <Button variant="ghost" onClick={() => void cancelStorageMove().then(setStorage)}>
                  <X /> Cancel before switch
                </Button>
              )}
              {storage?.redirected_target && (
                <Button
                  variant="ghost"
                  onClick={() => void adoptRedirectedStorage().then(setStorage)}
                >
                  <Check /> Adopt existing redirected location
                </Button>
              )}
              <Button
                variant="ghost"
                onClick={async () => {
                  const folder = await chooseStorageLocation();
                  if (!folder) return;
                  setStorage(await setDefaultExportFolder(folder));
                  notify('Default export folder saved locally');
                }}
              >
                <FileDown /> Default export folder
              </Button>
            </div>
            <div className="recovery-note">
              <Info />
              <span>
                <strong>Application binaries and studio data are separate.</strong> Moving storage
                includes models, media, SQLite, training, logs, and managed runtime assets. The
                small bootstrap record stays local so upgrades and Repair Installation keep using
                the selected root.
                {storage?.default_export_folder
                  ? ` Exports open in ${storage.default_export_folder} by default.`
                  : ''}
              </span>
            </div>
            <Button
              disabled={repairingMedia}
              onClick={async () => {
                setRepairingMedia(true);
                try {
                  const report = await api.post<typeof mediaRepair>('/media/repair');
                  if (report) setMediaRepair(report);
                  mediaCache.clear();
                  await refresh();
                  notify(
                    report
                      ? `Media checked · ${report.ready_files} valid files · ${report.regenerated_derivatives} derivatives repaired`
                      : 'Media library checked',
                  );
                } finally {
                  setRepairingMedia(false);
                }
              }}
            >
              <RotateCcw /> {repairingMedia ? 'Checking media…' : 'Repair media library'}
            </Button>
            {mediaRepair && (
              <div className="media-repair-summary" role="status">
                <strong>{mediaRepair.records_scanned} records checked</strong>
                <span>
                  {mediaRepair.normalized_paths} paths normalized ·{' '}
                  {mediaRepair.regenerated_derivatives} previews restored
                </span>
                <span>
                  {mediaRepair.missing_originals.length} originals missing ·{' '}
                  {mediaRepair.invalid_files.length} invalid files
                </span>
              </div>
            )}
          </Panel>
        </div>
        <div>
          <Panel className="settings-panel">
            <div className="settings-heading">
              <SlidersHorizontal />
              <div>
                <h2>Studio behavior</h2>
                <p>Choose how Vanta opens and starts.</p>
              </div>
            </div>
            <div className="setting-row">
              <div>
                <strong>Default creation mode</strong>
                <span>Simple keeps advanced engine controls out of the way.</span>
              </div>
              <div className="mode-switch" role="group" aria-label="Default creation mode">
                <button
                  className={settings.values.default_mode !== 'studio' ? 'active' : ''}
                  aria-pressed={settings.values.default_mode !== 'studio'}
                  onClick={() => void update('default_mode', 'simple')}
                >
                  Simple
                </button>
                <button
                  className={settings.values.default_mode === 'studio' ? 'active' : ''}
                  aria-pressed={settings.values.default_mode === 'studio'}
                  onClick={() => void update('default_mode', 'studio')}
                >
                  Studio
                </button>
              </div>
            </div>
            <div className="setting-row">
              <div>
                <strong>Start engine with Vanta</strong>
                <span>Launch the hidden local engine only when the desktop app opens.</span>
              </div>
              <button
                role="switch"
                aria-checked={settings.values.engine_autostart !== 'false'}
                className={`toggle ${settings.values.engine_autostart !== 'false' ? 'active' : ''}`}
                onClick={() =>
                  void update(
                    'engine_autostart',
                    settings.values.engine_autostart !== 'false' ? 'false' : 'true',
                  )
                }
              >
                <span />
              </button>
            </div>
          </Panel>
          <Panel className="privacy-panel">
            <div className="privacy-seal">
              <ShieldCheck />
            </div>
            <span className="eyebrow">Privacy statement</span>
            <h2>No account. No telemetry. No cloud dependency.</h2>
            <p>
              Prompts, references, generated media, characters, and training data stay on this
              computer unless you choose to export them. Vanta binds its local services to 127.0.0.1
              and contains no inference API controls.
            </p>
            <div className="privacy-facts">
              <span>
                <Check /> Local service only
              </span>
              <span>
                <Check /> No analytics SDK
              </span>
              <span>
                <Check /> No paid API fallback
              </span>
            </div>
          </Panel>
          <Panel className="settings-panel about-panel">
            <div className="settings-heading">
              <Info />
              <div>
                <h2>About Vanta</h2>
                <p>V1.0.1 repair · desktop 0.1.1 · local orchestrator 0.1.1</p>
              </div>
            </div>
            <dl className="about-facts">
              <div>
                <dt>Architecture</dt>
                <dd>Tauri · React · FastAPI · SQLite · managed ComfyUI</dd>
              </div>
              <div>
                <dt>Networking</dt>
                <dd>Authenticated loopback only · 127.0.0.1</dd>
              </div>
              <div>
                <dt>Data ownership</dt>
                <dd>User-owned local files with numbered upgrade migrations</dd>
              </div>
            </dl>
            <Button onClick={() => void openLocalPath('logs')}>
              <FileDown /> Open local logs
            </Button>
          </Panel>
        </div>
      </div>
    </div>
  );
}
