/**
 * Shared reactive state — the Svelte 5 runes equivalent of SwiftUI's AppState.
 * Only what's needed for Phase 2 lives here; more state lands phase by phase.
 */
import {
  activateAdapter as apiActivateAdapter,
  activateProjectAdapter as apiActivateProjectAdapter,
  deleteConversation as apiDelete,
  fetchActiveModelDetail,
  fetchAdapters,
  fetchConversations,
  fetchLocalModels,
  fetchProjects as apiFetchProjects,
  fetchSystemStats,
  fetchVoiceConfig as apiFetchVoiceConfig,
  patchConversation as apiPatch,
  searchConversations as apiSearch,
  unloadModel as apiUnload,
  type Adapter,
  type ConversationMeta,
  type LocalModel,
  type Project,
  type VoiceConfig,
} from './api.client';

export type PartnerMode = 'default' | 'critique' | 'teach';

export type Capability = 'general' | 'reason' | 'code';

export interface CapabilityInfo {
  id: Capability;
  label: string;
  icon: string; // inline SVG path fragment used by the tab renderer
}

export const CAPABILITIES: readonly CapabilityInfo[] = [
  { id: 'general', label: 'General', icon: 'sparkles' },
  { id: 'reason',  label: 'Thinking', icon: 'brain' },
  { id: 'code',    label: 'Code',     icon: 'code' },
];

function appStore() {
  let selectedCapability = $state<Capability>('general');
  let contextWindow      = $state<number>(32768);
  let localModels        = $state<LocalModel[]>([]);
  let activeModelName    = $state<string | null>(null);
  // LoRA adapter layered on the active model, if any. `null` = base
  // model only. `activateBusy` gates the UI while the 2–3s server
  // restart runs so the user doesn't click through stale state.
  let activeAdapter      = $state<string | null>(null);
  let adapters           = $state<Adapter[]>([]);
  let activateBusy       = $state<boolean>(false);
  // Name of the model currently being loaded (or null). Shared between
  // ManageModelsView's load button and ChatView's composer so the
  // composer can show a "loading…" banner instead of eating keystrokes.
  let loadingModel       = $state<string | null>(null);
  let conversations      = $state<ConversationMeta[]>([]);
  let searchResults      = $state<ConversationMeta[]>([]);
  let searchQuery        = $state<string>('');
  let activeConvId       = $state<string | null>(null);
  // Bumped every time activeConvId is set, so ChatView can re-fire its
  // loader even when clicking the already-active row or starting a new
  // conversation twice in a row (both resolve to `activeConvId = null`).
  let convSelectNonce    = $state<number>(0);
  let loading            = $state<boolean>(false);
  let errorMsg           = $state<string | null>(null);
  // Research Partner — project list + which project is "active" for the
  // current chat session. Active project biases retrieval + scopes the
  // Workspace panel. Partner mode swaps the system-prompt overlay.
  let projects           = $state<Project[]>([]);
  let activeProjectId    = $state<string | null>(null);
  let partnerMode        = $state<PartnerMode>('default');
  // Voice mode — mirrors Swift AppState's voice block. The recorder
  // lifecycle lives in ChatView (it owns the `VoiceRecorder`); this
  // store only holds flags the rest of the UI needs to react to.
  let isVoiceMode        = $state<boolean>(false);
  let isTranscribing     = $state<boolean>(false);
  let voiceAudioLevel    = $state<number>(0);   // 0–1 RMS, for waveform
  let voiceError         = $state<string | null>(null);
  let voiceConfig        = $state<VoiceConfig | null>(null);
  let showVoiceSetup     = $state<boolean>(false);
  // System RAM (GB) — sidebar footer reads these so the user has a
  // glanceable parity with Activity Monitor's "Memory Used" figure.
  let ramUsedGb          = $state<number | null>(null);
  let ramTotalGb         = $state<number | null>(null);

  async function refresh(): Promise<void> {
    loading = true;
    errorMsg = null;
    try {
      const [models, active, convs, projs] = await Promise.all([
        fetchLocalModels(),
        fetchActiveModelDetail(),
        fetchConversations(),
        apiFetchProjects().catch(() => [] as Project[]),
      ]);
      localModels = models;
      activeModelName = active?.name ?? null;
      activeAdapter = active?.adapter ?? null;
      conversations = convs;
      projects = projs;
      // Kick off adapter discovery for the active model so the picker is
      // ready the moment the user opens Manage Models.
      if (active?.name) void refreshAdapters(active.name);
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  async function refreshAdapters(modelName: string): Promise<void> {
    try {
      adapters = await fetchAdapters(modelName);
    } catch {
      adapters = [];
    }
  }

  /** Activate a LoRA adapter on the given model (or clear by passing null).
   *  Shows a busy state during the ~2–3 s mlx_lm.server restart. */
  async function activateAdapter(
    modelName: string, adapterName: string | null,
  ): Promise<void> {
    activateBusy = true;
    try {
      await apiActivateAdapter(modelName, adapterName);
      activeAdapter = adapterName;
      // Re-poll /api/models/active to pick up the new running state after
      // the server comes back up.
      const detail = await fetchActiveModelDetail();
      if (detail) {
        activeModelName = detail.name;
        activeAdapter = detail.adapter;
      }
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
      throw e;
    } finally {
      activateBusy = false;
    }
  }

  async function refreshProjects(): Promise<void> {
    try {
      projects = await apiFetchProjects();
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
    }
  }

  function setActiveProject(id: string | null): void {
    activeProjectId = id;
    if (id === null) partnerMode = 'default';
    // When the user focuses a project, apply its bound adapter to the
    // currently loaded model. Fire-and-forget: the server handles the
    // "no adapter bound → deactivate" case, and errors surface via
    // errorMsg without blocking the switch.
    if (id) {
      void apiActivateProjectAdapter(id)
        .then(async () => {
          const detail = await fetchActiveModelDetail();
          if (detail) activeAdapter = detail.adapter;
        })
        .catch((e: unknown) => {
          errorMsg = e instanceof Error ? e.message : String(e);
        });
    }
  }

  function setPartnerMode(mode: PartnerMode): void {
    partnerMode = mode;
  }

  async function refreshSystemStats(): Promise<void> {
    try {
      const s = await fetchSystemStats();
      ramUsedGb = s.ram_used_gb;
      ramTotalGb = s.ram_total_gb;
    } catch {
      // Transient — let the next poll retry.
    }
  }

  async function refreshVoiceConfig(): Promise<void> {
    try {
      voiceConfig = await apiFetchVoiceConfig();
    } catch (e) {
      voiceError = e instanceof Error ? e.message : String(e);
    }
  }

  /** Returns true if both STT and TTS models are downloaded. */
  function voiceReady(): boolean {
    if (!voiceConfig) return false;
    const stt = voiceConfig.models.find((m) => m.model_type === 'stt' && m.downloaded);
    const tts = voiceConfig.models.find((m) => m.model_type === 'tts' && m.downloaded);
    return stt != null && tts != null;
  }

  function setVoiceMode(on: boolean): void { isVoiceMode = on; }
  function setTranscribing(on: boolean): void { isTranscribing = on; }
  function setVoiceAudioLevel(v: number): void { voiceAudioLevel = v; }
  function setVoiceError(msg: string | null): void { voiceError = msg; }
  function setShowVoiceSetup(on: boolean): void { showVoiceSetup = on; }

  async function search(q: string): Promise<void> {
    searchQuery = q;
    if (!q.trim()) { searchResults = []; return; }
    try { searchResults = await apiSearch(q); }
    catch (e) { errorMsg = e instanceof Error ? e.message : String(e); }
  }

  async function deleteConv(id: string): Promise<void> {
    try {
      await apiDelete(id);
      conversations = conversations.filter((c) => c.id !== id);
      searchResults = searchResults.filter((c) => c.id !== id);
      if (activeConvId === id) activeConvId = null;
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
    }
  }

  async function unload(): Promise<void> {
    try { await apiUnload(); activeModelName = null; }
    catch (e) { errorMsg = e instanceof Error ? e.message : String(e); }
  }

  function newConversation(): void {
    activeConvId = null;
    convSelectNonce++;
  }

  function selectConversation(id: string): void {
    activeConvId = id;
    convSelectNonce++;
  }

  /**
   * Adopt an id that was just created by the active chat view — sets the
   * highlight on the sidebar without bumping the nonce (the ChatView
   * already holds the state, so a reload would clobber its history).
   */
  function adoptActiveConv(id: string): void {
    activeConvId = id;
  }

  async function toggleStar(id: string): Promise<void> {
    const conv = conversations.find((c) => c.id === id);
    const next = !conv?.starred;
    try {
      await apiPatch(id, { starred: next });
      conversations = conversations.map((c) =>
        c.id === id ? { ...c, starred: next } : c,
      );
      searchResults = searchResults.map((c) =>
        c.id === id ? { ...c, starred: next } : c,
      );
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
    }
  }

  async function setFolder(id: string, folder: string | null): Promise<void> {
    const normalised = folder && folder.trim() ? folder.trim() : null;
    try {
      await apiPatch(id, { folder: normalised });
      conversations = conversations.map((c) =>
        c.id === id ? { ...c, folder: normalised } : c,
      );
      searchResults = searchResults.map((c) =>
        c.id === id ? { ...c, folder: normalised } : c,
      );
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
    }
  }

  // Expose state as getters so consumers stay reactive across boundaries.
  return {
    get selectedCapability() { return selectedCapability; },
    set selectedCapability(v) { selectedCapability = v; },
    get contextWindow() { return contextWindow; },
    set contextWindow(v) { contextWindow = v; },
    get localModels() { return localModels; },
    get activeModelName() { return activeModelName; },
    set activeModelName(v) { activeModelName = v; },
    get activeAdapter() { return activeAdapter; },
    get adapters() { return adapters; },
    get activateBusy() { return activateBusy; },
    get loadingModel() { return loadingModel; },
    set loadingModel(v) { loadingModel = v; },
    get conversations() { return conversations; },
    get searchQuery() { return searchQuery; },
    get searchResults() { return searchResults; },
    get activeConvId() { return activeConvId; },
    get convSelectNonce() { return convSelectNonce; },
    get loading() { return loading; },
    get errorMsg() { return errorMsg; },
    get projects() { return projects; },
    get activeProjectId() { return activeProjectId; },
    get activeProject(): Project | null {
      if (!activeProjectId) return null;
      return projects.find((p) => p.id === activeProjectId) ?? null;
    },
    get partnerMode() { return partnerMode; },
    get isVoiceMode() { return isVoiceMode; },
    get isTranscribing() { return isTranscribing; },
    get voiceAudioLevel() { return voiceAudioLevel; },
    get voiceError() { return voiceError; },
    get voiceConfig() { return voiceConfig; },
    get showVoiceSetup() { return showVoiceSetup; },
    refresh,
    search,
    deleteConv,
    unload,
    newConversation,
    selectConversation,
    adoptActiveConv,
    toggleStar,
    setFolder,
    refreshProjects,
    setActiveProject,
    setPartnerMode,
    get ramUsedGb() { return ramUsedGb; },
    get ramTotalGb() { return ramTotalGb; },
    refreshSystemStats,
    refreshVoiceConfig,
    voiceReady,
    setVoiceMode,
    setTranscribing,
    setVoiceAudioLevel,
    setVoiceError,
    setShowVoiceSetup,
    refreshAdapters,
    activateAdapter,
  };
}

export const app = appStore();
