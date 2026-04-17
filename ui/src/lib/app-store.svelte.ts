/**
 * Shared reactive state — the Svelte 5 runes equivalent of SwiftUI's AppState.
 * Only what's needed for Phase 2 lives here; more state lands phase by phase.
 */
import {
  deleteConversation as apiDelete,
  fetchActiveModel,
  fetchConversations,
  fetchLocalModels,
  searchConversations as apiSearch,
  unloadModel as apiUnload,
  type ConversationMeta,
  type LocalModel,
} from './api.client';

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
  let loading            = $state<boolean>(false);
  let errorMsg           = $state<string | null>(null);

  async function refresh(): Promise<void> {
    loading = true;
    errorMsg = null;
    try {
      const [models, active, convs] = await Promise.all([
        fetchLocalModels(),
        fetchActiveModel(),
        fetchConversations(),
      ]);
      localModels = models;
      activeModelName = active;
      conversations = convs;
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
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
  }

  function selectConversation(id: string): void {
    activeConvId = id;
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
    get loading() { return loading; },
    get errorMsg() { return errorMsg; },
    refresh,
    search,
    deleteConv,
    unload,
    newConversation,
    selectConversation,
  };
}

export const app = appStore();
