/**
 * Shared reactive state — the Svelte 5 runes equivalent of SwiftUI's AppState.
 * Only what's needed for Phase 2 lives here; more state lands phase by phase.
 */
import {
  deleteConversation as apiDelete,
  fetchActiveModel,
  fetchConversations,
  fetchLocalModels,
  fetchProjects as apiFetchProjects,
  patchConversation as apiPatch,
  searchConversations as apiSearch,
  unloadModel as apiUnload,
  type ConversationMeta,
  type LocalModel,
  type Project,
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

  async function refresh(): Promise<void> {
    loading = true;
    errorMsg = null;
    try {
      const [models, active, convs, projs] = await Promise.all([
        fetchLocalModels(),
        fetchActiveModel(),
        fetchConversations(),
        apiFetchProjects().catch(() => [] as Project[]),
      ]);
      localModels = models;
      activeModelName = active;
      conversations = convs;
      projects = projs;
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
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
  }

  function setPartnerMode(mode: PartnerMode): void {
    partnerMode = mode;
  }

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
  };
}

export const app = appStore();
