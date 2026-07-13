import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react';
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
  FileDown,
  FileUp,
  FolderLock,
  Gauge,
  Grid3X3,
  Heart,
  Image,
  Info,
  Menu,
  Minus,
  MoreHorizontal,
  Plus,
  RotateCcw,
  Search,
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
  chooseLocalImageFile,
  chooseLocalLoraFile,
  chooseLocalModelFile,
  exportDiagnostics,
  getLocalServiceInfo,
  repairApplicationRuntime,
  restartLocalService,
  type LocalServiceInfo,
} from './api';
import type {
  CharacterRecord,
  Diagnostics,
  EngineComponent,
  GenerationJob,
  GenerationRecord,
  LoraRecord,
  ModelPack,
  PresetRecord,
  SettingsRecord,
} from './types';

type Screen = 'create' | 'characters' | 'presets' | 'gallery' | 'engine' | 'settings';
type AppData = {
  characters: CharacterRecord[];
  presets: PresetRecord[];
  gallery: GenerationRecord[];
  components: EngineComponent[];
  packs: ModelPack[];
  loras: LoraRecord[];
  hardware: { gpu_name: string; vram_gb: number; ram_gb: number; free_disk_gb: number };
  settings: SettingsRecord;
};

const navItems: { id: Screen; label: string; icon: typeof Sparkles }[] = [
  { id: 'create', label: 'Create', icon: Sparkles },
  { id: 'characters', label: 'Characters', icon: CircleUserRound },
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
  crashed: 'Crashed',
};
const stateTone = (state: string): 'ready' | 'warning' | 'danger' | 'neutral' =>
  state === 'ready'
    ? 'ready'
    : state === 'repair_needed'
      ? 'danger'
      : ['installing', 'update_available', 'paused'].includes(state)
        ? 'warning'
        : 'neutral';

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

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [characters, presets, gallery, components, modelResponse, settings, loras] =
        await Promise.all([
          api.get<CharacterRecord[]>('/characters'),
          api.get<PresetRecord[]>('/presets'),
          api.get<GenerationRecord[]>('/gallery'),
          api.get<EngineComponent[]>('/engine/components'),
          api.get<{ hardware: AppData['hardware']; packs: ModelPack[] }>('/engine/model-packs'),
          api.get<SettingsRecord>('/settings'),
          api.get<LoraRecord[]>('/loras'),
        ]);
      setData({
        characters,
        presets,
        gallery,
        components,
        packs: modelResponse.packs,
        hardware: modelResponse.hardware,
        settings,
        loras,
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
                  initialDraft={generationDraft}
                  onDraftUsed={() => setGenerationDraft(null)}
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
              {screen === 'presets' && (
                <PresetsScreen items={data.presets} refresh={load} notify={notify} />
              )}
              {screen === 'gallery' && (
                <GalleryScreen
                  items={data.gallery}
                  notify={notify}
                  refresh={load}
                  onGenerateSimilar={(draft) => {
                    setGenerationDraft(draft);
                    navigate('create');
                  }}
                />
              )}
              {screen === 'engine' && <EngineScreen data={data} refresh={load} notify={notify} />}
              {screen === 'settings' && (
                <SettingsScreen settings={data.settings} refresh={load} notify={notify} />
              )}
            </>
          )}
        </main>
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
  initialDraft,
  onDraftUsed,
}: {
  data: AppData;
  refresh: () => Promise<void>;
  notify: (message: string) => void;
  goEngine: () => void;
  initialDraft: Record<string, unknown> | null;
  onDraftUsed: () => void;
}) {
  const [mode, setMode] = useState<'simple' | 'studio'>('simple');
  const [prompt, setPrompt] = useState(
    'Moody Y2K bedroom portrait, intimate indie editorial mood, low phone-camera angle, black bedspread scattered with fashion magazines and a vinyl record.',
  );
  const [tags, setTags] = useState(['Cinematic', 'Film grain']);
  const [saving, setSaving] = useState(false);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [characterId, setCharacterId] = useState(data.characters[0]?.id ?? '');
  const [steps, setSteps] = useState(30);
  const [guidance, setGuidance] = useState(5.5);
  const [seed, setSeed] = useState(() => Math.floor(Math.random() * 2_000_000_000));
  const [negativePrompt, setNegativePrompt] = useState(
    'low quality, malformed hands, artificial skin texture',
  );
  const runtime = data.components.find((item) => item.id === 'workflow-runtime');
  const model =
    data.packs.find((item) => item.is_default) ??
    data.packs.find((item) => item.alias === 'photoreal_balanced');
  const canGenerate = runtime?.state === 'ready' && Boolean(model?.installed && model.verified);
  const categories = [
    'wardrobe',
    'expression',
    'pose',
    'location',
    'lighting',
    'camera',
    'quality',
  ];
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
    setSeed(Number(initialDraft.seed ?? seed));
    setNegativePrompt(String(initialDraft.negative_prompt ?? negativePrompt));
    onDraftUsed();
  }, [initialDraft]);
  const presetText = (category: string) =>
    options(category).find((item) => item.id === selected[category])?.prompt ?? '';
  const generationRequest = () => ({
    character_id: characterId || null,
    recipe_id: null,
    character_identity:
      data.characters.find((item) => item.id === characterId)?.identity_description ?? '',
    wardrobe: presetText('wardrobe'),
    expression: presetText('expression'),
    pose: presetText('pose'),
    location: presetText('location'),
    lighting: presetText('lighting'),
    camera: presetText('camera'),
    quality: presetText('quality'),
    direction: prompt,
    custom_tags: tags,
    negative_prompt: negativePrompt,
    model_alias: 'photoreal_balanced',
    seed,
    width: 832,
    height: 1216,
    steps,
    guidance,
  });
  const saveRecipe = async () => {
    setSaving(true);
    try {
      await api.post('/recipes', {
        name: 'Y2K Bedroom Study',
        character_id: characterId || null,
        freeform_prompt: prompt,
        model_profile: 'photoreal_balanced',
        preset_ids: Object.values(selected).filter(Boolean),
      });
      notify('Recipe saved to your local library');
    } finally {
      setSaving(false);
    }
  };
  const generate = async () => {
    if (!canGenerate) {
      goEngine();
      return;
    }
    setJob(await api.post<GenerationJob>('/generations', generationRequest()));
    notify('Generation queued locally');
  };
  const cancel = async () => {
    if (job) setJob(await api.post<GenerationJob>(`/generations/${job.id}/cancel`));
  };
  useEffect(() => {
    if (!job || ['completed', 'cancelled', 'failed'].includes(job.status)) return;
    const timer = window.setInterval(() => {
      void api.get<GenerationJob>(`/generations/${job.id}`).then(async (next) => {
        setJob(next);
        if (next.status === 'completed') {
          await refresh();
          notify('Real image saved to your local Gallery');
        }
      });
    }, 900);
    return () => window.clearInterval(timer);
  }, [job, notify, refresh]);
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
            <Button variant="primary" onClick={() => void (job ? cancel() : generate())}>
              {job && !['completed', 'cancelled', 'failed'].includes(job.status) ? (
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
      {!canGenerate && (
        <div className="capability-banner">
          <div>
            <Wrench />
            <span>
              <strong>Finish local setup to generate</strong>
              <small>
                {runtime?.state !== 'ready'
                  ? 'Install or repair the Local Generation Engine, then complete its health check.'
                  : 'Import and verify a compatible local SDXL checkpoint in Models & Engine.'}
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
                <strong>{data.characters[0]?.name ?? 'No character selected'}</strong>
                <span>Original adult · v0.3</span>
              </div>
              <div className="preview-index">01 / local study</div>
            </div>
            <footer>
              <span>
                <FolderLock /> Saved locally with reproducible metadata
              </span>
              <span>832 × 1216</span>
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
              <button className="tag">
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
                <h2>Y2K Bedroom Study</h2>
              </div>
              <MoreHorizontal />
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
                    <select>
                      <option>DPM++ 2M Karras</option>
                    </select>
                  </label>
                </div>
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
    notify(`Verified SDXL LoRA assigned to ${item.name}`);
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
          : 'Import a compatible local SDXL LoRA from a character card when you are ready.'}
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
                    title="Import and assign a local SDXL LoRA"
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
  const [url, setUrl] = useState('');
  useEffect(() => {
    let active = true;
    let objectUrl = '';
    void api.referenceImageUrl(referenceId).then((next) => {
      objectUrl = next;
      if (active) setUrl(next);
    });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [referenceId]);
  return url ? (
    <img className="character-reference-image" src={url} alt={alt} />
  ) : (
    <div className="portrait-silhouette" />
  );
}

const presetCategories = [
  'all',
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
function PresetsScreen({
  items,
  refresh,
  notify,
}: {
  items: PresetRecord[];
  refresh: () => Promise<void>;
  notify: (message: string) => void;
}) {
  const [category, setCategory] = useState('all');
  const [query, setQuery] = useState('');
  const [editing, setEditing] = useState<PresetRecord | 'new' | null>(null);
  const filtered = items.filter(
    (item) =>
      (category === 'all' || item.category === category) &&
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
      scope: 'global',
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
  return (
    <div className="screen">
      <PageTitle
        eyebrow="Creative library"
        title="Presets that become yours."
        body="Start curated. Duplicate, tag, and reshape anything without losing the original."
        actions={
          <>
            <label className="v-button v-button--secondary file-button">
              <FileUp /> Import
              <input
                type="file"
                accept="application/json"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) importJson(file);
                }}
              />
            </label>
            <Button onClick={() => void exportJson()}>
              <FileDown /> Export
            </Button>
            <Button variant="primary" onClick={() => setEditing('new')}>
              <Plus /> New preset
            </Button>
          </>
        }
      />
      <div className="library-toolbar">
        <div className="search-field">
          <Search />
          <input
            aria-label="Search presets"
            placeholder="Search names and tags"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
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
      </div>
      {filtered.length === 0 ? (
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
    </div>
  );
}

function LocalGenerationImage({ generationId, alt }: { generationId: string; alt: string }) {
  const [url, setUrl] = useState('');
  useEffect(() => {
    let active = true;
    let objectUrl = '';
    void api.imageUrl(generationId).then((next) => {
      objectUrl = next;
      if (active) setUrl(next);
    });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [generationId]);
  return url ? (
    <img className="generation-image" src={url} alt={alt} />
  ) : (
    <div className="generated-study" aria-label="Loading local image" />
  );
}

function GalleryScreen({
  items,
  notify,
  refresh,
  onGenerateSimilar,
}: {
  items: GenerationRecord[];
  notify: (message: string) => void;
  refresh: () => Promise<void>;
  onGenerateSimilar: (draft: Record<string, unknown>) => void;
}) {
  const [filter, setFilter] = useState('all');
  const [selected, setSelected] = useState<GenerationRecord | null>(null);
  const filtered = filter === 'all' ? items : items.filter((item) => item.model_alias === filter);
  return (
    <div className="screen gallery-screen">
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
                <LocalGenerationImage generationId={item.id} alt={`Generated image ${item.id}`} />
                <span className="disclosure">
                  <Sparkles /> AI-created
                </span>
                <span className="tile-seed">#{item.seed}</span>
              </div>
              <div>
                <strong>{item.metadata.recipe ?? 'Local generation'}</strong>
                <span>
                  {item.width} × {item.height} · {item.model_alias}
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
              <LocalGenerationImage generationId={selected.id} alt="Selected generated image" />
            </div>
            <div className="metadata-section">
              <span className="eyebrow">Reproducible metadata</span>
              <dl>
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
              onClick={async () => {
                await api.delete(`/generations/${selected.id}`);
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
}: {
  data: AppData;
  refresh: () => Promise<void>;
  notify: (message: string) => void;
}) {
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [busy, setBusy] = useState('');
  const componentAction = async (item: EngineComponent, action: string) => {
    setBusy(item.id);
    try {
      await api.post(`/engine/components/${item.id}/${action}`);
      notify(`${action === 'repair' ? 'Repair' : 'Installation'} started for ${item.display_name}`);
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
  const importLocalModel = async () => {
    const sourcePath = await chooseLocalModelFile();
    if (!sourcePath) return;
    setBusy('model-import');
    try {
      await api.post('/engine/models/import', {
        source_path: sourcePath,
        alias: 'photoreal_balanced',
        license_notes: '',
      });
      notify('Local SDXL checkpoint imported and verified');
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
                  <Button
                    onClick={() => void componentAction(item, 'health_check')}
                    disabled={busy === item.id}
                  >
                    <ShieldCheck /> Health check
                  </Button>
                ) : (
                  <Button
                    variant={item.state === 'repair_needed' ? 'primary' : 'secondary'}
                    onClick={() =>
                      void componentAction(
                        item,
                        item.state === 'repair_needed'
                          ? 'repair'
                          : item.state === 'installing'
                            ? 'cancel'
                            : 'install',
                      )
                    }
                    disabled={busy === item.id}
                  >
                    {item.state === 'repair_needed' ? (
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
                  </div>
                </div>
              </div>
              <div className="model-actions">
                {item.state === 'installing' && (
                  <div className="progress">
                    <span style={{ width: `${item.progress}%` }} />
                  </div>
                )}
                {!item.installed && item.alias === 'photoreal_balanced' && (
                  <Button
                    onClick={() => void importLocalModel()}
                    disabled={busy === 'model-import'}
                  >
                    <Upload /> Import local model
                  </Button>
                )}
                {!item.installed && item.alias !== 'photoreal_balanced' && (
                  <Button disabled>Coming later</Button>
                )}
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
              </div>
            </Panel>
          ))}
        </div>
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
          </div>
        )}
      </Drawer>
    </div>
  );
}

function SettingsScreen({
  settings,
  refresh,
  notify,
}: {
  settings: SettingsRecord;
  refresh: () => Promise<void>;
  notify: (message: string) => void;
}) {
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
                  <Button aria-label="Open studio data folder">
                    <MoreHorizontal />
                  </Button>
                </div>
              </label>
              <label>
                Model packs
                <div>
                  <input value={settings.paths.models} readOnly />
                  <Button aria-label="Open model pack folder">
                    <MoreHorizontal />
                  </Button>
                </div>
              </label>
              <label>
                Database
                <div>
                  <input value={settings.paths.database} readOnly />
                  <Button aria-label="Show database location">
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
            <div className="storage-bar">
              <span style={{ width: '34%' }} />
            </div>
            <div className="storage-legend">
              <span>
                <i />
                Vanta assets · 31.4 GB
              </span>
              <span>186 GB free</span>
            </div>
            <Button>Review local storage</Button>
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
              <div className="mode-switch">
                <button
                  className={settings.values.default_mode !== 'studio' ? 'active' : ''}
                  onClick={() => void update('default_mode', 'simple')}
                >
                  Simple
                </button>
                <button
                  className={settings.values.default_mode === 'studio' ? 'active' : ''}
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
        </div>
      </div>
    </div>
  );
}
