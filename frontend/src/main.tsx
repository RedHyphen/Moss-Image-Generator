import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent, PointerEvent } from "react";
import { createRoot } from "react-dom/client";
import { Check, ChevronDown, CircleHelp, Download, ImagePlus, LoaderCircle, LogOut, MessageCircle, Plus, RefreshCw, Search, Send, Settings2, Sparkles, Star, Trash2 } from "lucide-react";
import "./styles.css";

type Job = { id: string; kind: string; status: string; error: string; created_at: string; params: Record<string, unknown>; progress?: Record<string, unknown> };
type GalleryImage = { id: string; kind: string; prompt: string; url: string; metadata: Record<string, unknown>; created_at: string; favorite?: boolean };
type Session = { username: string; csrf_token: string };
type LayoutItem = { id: string; label: "text" | "object"; caption: string; text: string; bbox: [number, number, number, number] };
type Conversation = { id: string; title: string; created_at: string; updated_at: string; archived?: number };
type ConversationMessage = { id: string; role: "user" | "assistant"; content: string; status: string; error?: string; created_at: string; plan?: Record<string, unknown>; assets?: Array<Record<string, unknown>> };
type NumericFieldValue = string;

function savedPrompt(image: GalleryImage): string {
  const originalPrompt = image.metadata?.original_prompt;
  return typeof originalPrompt === "string" && originalPrompt.trim() ? originalPrompt : image.prompt;
}

function metadataNumber(metadata: Record<string, unknown>, key: string): number | undefined {
  const raw = metadata[key];
  const value = typeof raw === "number" ? raw : typeof raw === "string" && raw.trim() ? Number(raw) : NaN;
  return Number.isFinite(value) ? value : undefined;
}

function loadedLayoutSettings(value: unknown): { json: Record<string, unknown>; caption: string; items: LayoutItem[] } | null {
  if (!value || typeof value !== "object") return null;
  const json = value as Record<string, unknown>;
  if (!Array.isArray(json.items)) return null;
  const summary = json.summary && typeof json.summary === "object" ? json.summary as Record<string, unknown> : {};
  const items = json.items.flatMap((rawItem, index) => {
    if (!rawItem || typeof rawItem !== "object") return [];
    const item = rawItem as Record<string, unknown>;
    const rawBbox = Array.isArray(item.bbox) ? item.bbox.map(Number) : [];
    if (rawBbox.length !== 4 || rawBbox.some((number) => !Number.isFinite(number))) return [];
    const [left, top, right, bottom] = rawBbox;
    if (left >= right || top >= bottom) return [];
    return [{
      id: `loaded-layout-${index}`,
      label: (item.label === "text" ? "text" : "object") as LayoutItem["label"],
      caption: typeof item.caption === "string" && item.caption.trim() ? item.caption : "Loaded layout element",
      text: typeof item.text === "string" ? item.text : "",
      bbox: [top, left, bottom, right] as LayoutItem["bbox"],
    }];
  });
  if (!items.length) return null;
  return {
    json,
    caption: typeof summary.caption === "string" && summary.caption.trim() ? summary.caption : "A composed scene with clearly arranged elements.",
    items,
  };
}

// ---------------------------------------------------------------------------
// Immutable UI policy and generation presets
// ---------------------------------------------------------------------------
const SUBJECT_COUNT_NEGATIVE_PROMPT = "duplicate person, multiple versions of the same person, extra people, multiple subjects";
const DEFAULT_POSITIVE_PROMPT = "A cinematic portrait of a single person, one subject only, in soft morning light, detailed, high quality";
const RESOLUTION_PRESETS = [
  { id: "square", label: "Square · 1:1", width: 512, height: 512 },
  { id: "landscape", label: "Landscape · 4:3", width: 768, height: 576 },
  { id: "classic", label: "Classic · 3:2", width: 768, height: 512 },
  { id: "wide", label: "Wide · 16:9", width: 1024, height: 576 },
  { id: "portrait", label: "Portrait · 2:3", width: 512, height: 768 },
  { id: "tall", label: "Tall · 9:16", width: 576, height: 1024 },
  { id: "square-hd", label: "Square HD · 1:1", width: 768, height: 768 },
  { id: "standard", label: "Standard · 4:3", width: 1024, height: 768 },
  { id: "square-uhd", label: "Square UHD · 1:1", width: 1024, height: 1024 },
  { id: "wide-hd", label: "Wide HD · 16:9", width: 1280, height: 720 },
  { id: "landscape-large", label: "Large · 3:2", width: 1536, height: 1024 },
  { id: "square-large", label: "Square Large · 1:1", width: 1536, height: 1536 },
];
const GENERATION_PRESETS = [
  {
    id: "portrait",
    label: "Portrait",
    prompt: "A polished editorial portrait of a single person, one subject only, with natural skin texture, flattering soft light, intentional composition, and a clean background.",
    negativePrompt: `${SUBJECT_COUNT_NEGATIVE_PROMPT}, worst quality, bad anatomy, distorted face, watermark, text, jpeg artifact`,
    width: 512,
    height: 768,
    steps: 50,
    guidance: 3,
  },
  {
    id: "landscape",
    label: "Landscape",
    prompt: "A cinematic wide landscape with strong foreground, middle-ground, and background depth, atmospheric light, and detailed natural textures.",
    negativePrompt: `${SUBJECT_COUNT_NEGATIVE_PROMPT}, worst quality, flat composition, blurry, watermark, text, jpeg artifact`,
    width: 1024,
    height: 576,
    steps: 50,
    guidance: 3,
  },
  {
    id: "product",
    label: "Product",
    prompt: "A premium product photograph with precise geometry, controlled studio lighting, realistic materials, subtle shadows, and a clean commercial composition.",
    negativePrompt: `${SUBJECT_COUNT_NEGATIVE_PROMPT}, worst quality, warped product, clutter, watermark, text, jpeg artifact`,
    width: 512,
    height: 512,
    steps: 50,
    guidance: 3,
  },
  {
    id: "anime",
    label: "Anime",
    prompt: "A richly illustrated anime scene with expressive characters, confident linework, layered cel shading, atmospheric lighting, and a coherent background.",
    negativePrompt: `${SUBJECT_COUNT_NEGATIVE_PROMPT}, worst quality, bad anatomy, muddy linework, watermark, text, jpeg artifact`,
    width: 512,
    height: 512,
    steps: 50,
    guidance: 3,
  },
];

// Keep numeric controls editable while the user types. Range, integer, and
// multiple-of-16 validation belongs to the authenticated API boundary.
function numericPayload(value: NumericFieldValue): number | string {
  if (!value.trim()) return value;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : value;
}

function canvasDimension(value: NumericFieldValue): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 512;
}

// ---------------------------------------------------------------------------
// Pure formatting and progress helpers
// ---------------------------------------------------------------------------
function formatBytes(value: unknown) {
  const bytes = typeof value === "number" ? value : 0;
  if (!bytes) return "Unknown";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let amount = bytes;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; }
  return `${amount.toFixed(amount >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function progressLabel(job: Job) {
  const progress = job.progress || {};
  const stage = typeof progress.stage === "string" ? progress.stage.replaceAll("_", " ") : job.status;
  const percent = progressPercent(job);
  const suffix = percent === null ? " · live" : ` · ${percent}%`;
  return `${job.status} · ${stage}${suffix}`;
}

function progressPercent(job: Job): number | null {
  const value = job.progress?.percent;
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return Math.min(100, Math.max(1, Math.round(value)));
}

function ProgressMeter({ job }: { job: Job }) {
  const percent = progressPercent(job);
  const label = percent === null ? "Live progress; sampling percentage unavailable" : `${percent}% complete`;
  return <div className="job-progress" role="progressbar" aria-label={label} aria-valuemin={percent === null ? undefined : 1} aria-valuemax={percent === null ? undefined : 100} aria-valuenow={percent === null ? undefined : percent}>
    <div className={`job-progress-track ${percent === null ? "indeterminate" : ""}`}><span style={percent === null ? undefined : { width: `${percent}%` }} /></div>
    <strong>{percent === null ? "LIVE" : `${percent}%`}</strong>
  </div>;
}

// ---------------------------------------------------------------------------
// Same-origin API boundary
// ---------------------------------------------------------------------------
function formatApiError(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      if (item && typeof item === "object" && "msg" in item) return String(item.msg);
      return typeof item === "string" ? item : JSON.stringify(item) || String(item);
    });
    if (messages.length) return messages.join("; ");
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail) || fallback;
  return fallback;
}

async function api(path: string, init: RequestInit = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...init });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(formatApiError(body.detail, `Request failed (${response.status})`));
  return body;
}

// ---------------------------------------------------------------------------
// Page shell and authenticated application state
// ---------------------------------------------------------------------------
function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [setupRequired, setSetupRequired] = useState(false);
  const [authError, setAuthError] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [prompt, setPrompt] = useState(DEFAULT_POSITIVE_PROMPT);
  const [negativePrompt, setNegativePrompt] = useState(`worst quality, low quality, blurry, jpeg artifacts, watermark, ${SUBJECT_COUNT_NEGATIVE_PROMPT}`);
  const [width, setWidth] = useState<NumericFieldValue>("512");
  const [height, setHeight] = useState<NumericFieldValue>("512");
  const [steps, setSteps] = useState<NumericFieldValue>("50");
  const [guidance, setGuidance] = useState<NumericFieldValue>("3");
  const [seed, setSeed] = useState<NumericFieldValue>("42");
  const [count, setCount] = useState<NumericFieldValue>("1");
  const [mossModel, setMossModel] = useState("OpenMOSS-Team/MOSS-Image-SFT");
  const mossModelManuallySelected = useRef(false);
  const [randomSeed, setRandomSeed] = useState(false);
  const [randomizedBatch, setRandomizedBatch] = useState(false);
  const outputCount = numericPayload(count);
  const outputSeed = randomizedBatch || randomSeed ? null : numericPayload(seed);
  const [layoutMode, setLayoutMode] = useState(false);
  const [layoutJson, setLayoutJson] = useState("");
  const [layoutCaption, setLayoutCaption] = useState("A composed scene with clearly arranged elements.");
  const [layoutItems, setLayoutItems] = useState<LayoutItem[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [gallery, setGallery] = useState<GalleryImage[]>([]);
  const [galleryQuery, setGalleryQuery] = useState("");
  const [galleryModel, setGalleryModel] = useState("");
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [system, setSystem] = useState<any>(null);
  const [models, setModels] = useState<any>(null);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationSearch, setConversationSearch] = useState("");
  const [activeConversationId, setActiveConversationId] = useState("");
  const [conversationMessages, setConversationMessages] = useState<ConversationMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatSending, setChatSending] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [promptSuggestion, setPromptSuggestion] = useState("");
  const [originalPrompt, setOriginalPrompt] = useState("");
  const [loadedPromptId, setLoadedPromptId] = useState("");

  const refresh = async () => {
    if (!session) return;
    try {
      const [jobData, imageData, systemData, modelData] = await Promise.all([api("/api/jobs"), api("/api/images"), api("/api/system"), api("/api/models")]);
      setJobs(jobData);
      setGallery(imageData);
      setSystem(systemData);
      const activeMossModel = modelData?.models?.find((item: any) => item.backend === "vllm-omni" && item.active)?.id;
      if (!mossModelManuallySelected.current && activeMossModel) setMossModel(activeMossModel);
      setModels(modelData);
      const refreshedConversationId = await loadConversationList();
      if (refreshedConversationId) await loadConversation(refreshedConversationId);
    } catch (err) { setError((err as Error).message); }
  };

  const loadConversation = async (conversationId: string) => {
    if (!conversationId) return;
    try {
      const data = await api(`/api/conversations/${conversationId}`);
      setConversationMessages(data.messages || []);
    } catch (err) { setError((err as Error).message); }
  };

  const loadConversationList = async (query = conversationSearch) => {
    try {
      const suffix = query.trim() ? `?q=${encodeURIComponent(query.trim())}` : "";
      let items = await api(`/api/conversations${suffix}`);
      if (!items.length && session && !query.trim()) {
        const created = await api("/api/conversations", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": session.csrf_token }, body: JSON.stringify({ title: "New conversation" }) });
        items = [created];
      }
      setConversations(items);
      const refreshedConversationId = items.some((item: Conversation) => item.id === activeConversationId)
        ? activeConversationId
        : items[0]?.id || "";
      if (refreshedConversationId && refreshedConversationId !== activeConversationId) {
        setActiveConversationId(refreshedConversationId);
        setConversationMessages([]);
      }
      return refreshedConversationId;
    } catch (err) { setError((err as Error).message); }
  };

  useEffect(() => {
    api("/api/auth/status").then((status) => {
      setSetupRequired(status.setup_required);
      if (!status.setup_required) api("/api/auth/me").then(setSession).catch(() => undefined);
    }).catch((err) => setAuthError(err.message));
  }, []);
  useEffect(() => {
    if (!session) return;
    refresh();
    const events = new EventSource("/api/events");
    events.onmessage = (event) => { try { setJobs(JSON.parse(event.data)); if (activeConversationId) loadConversation(activeConversationId); } catch { /* the next event will retry */ } };
    const healthInterval = window.setInterval(refresh, 15000);
    return () => { events.close(); window.clearInterval(healthInterval); };
  }, [session, activeConversationId]);

  useEffect(() => { if (session && activeConversationId) loadConversation(activeConversationId); }, [session, activeConversationId]);
  useEffect(() => {
    if (!session) return;
    const timer = window.setTimeout(() => { loadConversationList(conversationSearch); }, 180);
    return () => window.clearTimeout(timer);
  }, [session, conversationSearch]);

  const auth = async (event: FormEvent) => {
    event.preventDefault(); setAuthError("");
    try {
      const data = await api(setupRequired ? "/api/auth/setup" : "/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }) });
      setConversations([]); setActiveConversationId(""); setConversationMessages([]); setChatInput(""); setJobs([]); setGallery([]); setSystem(null); setModels(null); setError(""); setMessage("");
      setSession(data); setPassword("");
    } catch (err) { setAuthError((err as Error).message); }
  };

  const sendConversation = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!session || !activeConversationId || !chatInput.trim() || chatSending) return;
    setChatSending(true); setError("");
    try {
      await api(`/api/conversations/${activeConversationId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": session.csrf_token },
        body: JSON.stringify({ content: chatInput.trim(), width: numericPayload(width), height: numericPayload(height), steps: numericPayload(steps), guidance_scale: numericPayload(guidance), negative_prompt: negativePrompt, seed: outputSeed, n: outputCount, randomized_batch: randomizedBatch }),
      });
      setChatInput("");
      await loadConversation(activeConversationId); await loadConversationList(); refresh();
    } catch (err) {
      setError((err as Error).message);
      // The backend persists some failures (for example an unavailable MOSS
      // worker) as conversation messages even though no job is created.
      // Refresh that conversation immediately so the chat does not appear to
      // lose the user's request until the next manual refresh.
      await loadConversation(activeConversationId);
      await loadConversationList();
    }
    finally { setChatSending(false); }
  };

  const newConversation = async () => {
    if (!session) return;
    try {
      const created = await api("/api/conversations", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": session.csrf_token }, body: JSON.stringify({ title: "New conversation" }) });
      setConversationSearch(""); setConversations((old) => [created, ...old]); setActiveConversationId(created.id); setConversationMessages([]);
    } catch (err) { setError((err as Error).message); }
  };

  const applyGenerationPreset = (presetId: string) => {
    const preset = GENERATION_PRESETS.find((item) => item.id === presetId);
    if (!preset) return;
    setPrompt(preset.prompt);
    setNegativePrompt(preset.negativePrompt);
    setWidth(String(preset.width));
    setHeight(String(preset.height));
    setSteps(String(preset.steps));
    setGuidance(String(preset.guidance));
    setRandomSeed(true);
  };

  const buildLayout = () => ({
    width: numericPayload(width),
    height: numericPayload(height),
    bbox_coord_space: "abs",
    bbox_format: "xyxy",
    summary: { meta: ["custom"], canvas: "single", caption: layoutCaption },
    items: layoutItems.map((item) => {
      const base = {
        label: item.label,
        caption: item.caption,
        bbox: [item.bbox[1], item.bbox[0], item.bbox[3], item.bbox[2]],
      };
      return item.label === "text" ? { ...base, text: item.text } : base;
    }),
  });

  useEffect(() => {
    if (layoutMode) setLayoutJson(JSON.stringify(buildLayout(), null, 2));
  }, [layoutMode, layoutCaption, layoutItems, width, height]);

  const submit = async (event: FormEvent) => {
    event.preventDefault(); setSubmitting(true); setError(""); setMessage("");
    try {
      let parsedLayout: unknown = undefined;
      if (layoutMode) { parsedLayout = JSON.parse(layoutJson || JSON.stringify(buildLayout())); }
      const body = { kind: "generate", prompt, original_prompt: originalPrompt || undefined, negative_prompt: negativePrompt, model: mossModel, width: numericPayload(width), height: numericPayload(height), steps: numericPayload(steps), guidance_scale: numericPayload(guidance), shift: 5, seed: outputSeed, n: outputCount, randomized_batch: randomizedBatch, slg: true, layout_json: parsedLayout };
      await api("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": session?.csrf_token || "" }, body: JSON.stringify(body) });
      setMessage("Queued for MOSS-Image on the vLLM worker.");
      refresh();
    } catch (err) { setError((err as Error).message); }
    finally { setSubmitting(false); }
  };

  const enhance = async () => {
    try {
      const data = await api("/api/prompt/enhance", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": session?.csrf_token || "" }, body: JSON.stringify({ prompt, mode: layoutMode ? "layout" : "caption", width: numericPayload(width), height: numericPayload(height) }) });
      if (!layoutMode) setOriginalPrompt(prompt);
      setPromptSuggestion(layoutMode ? JSON.stringify(data.result, null, 2) : String(data.result));
    }
    catch (err) { setError((err as Error).message); }
  };

  const acceptPromptSuggestion = () => {
    if (layoutMode) setLayoutJson(promptSuggestion);
    else setPrompt(promptSuggestion);
    setPromptSuggestion("");
  };


  const toggleFavorite = async (image: GalleryImage) => {
    try { await api(`/api/images/${image.id}/favorite`, { method: "PATCH", headers: { "Content-Type": "application/json", "X-CSRF-Token": session?.csrf_token || "" }, body: JSON.stringify({ favorite: !image.favorite }) }); refresh(); }
    catch (err) { setError((err as Error).message); }
  };

  const loadPrompt = (id: string, value: string, metadata: Record<string, unknown> = {}, fallbackNegativePrompt?: string) => {
    const promptToLoad = value.trim();
    if (!promptToLoad) {
      setError("This image does not have a saved prompt to load.");
      return;
    }
    setPrompt(promptToLoad);
    const loadedNegativePrompt = typeof metadata.negative_prompt === "string" ? metadata.negative_prompt : fallbackNegativePrompt;
    if (typeof loadedNegativePrompt === "string") setNegativePrompt(loadedNegativePrompt);
    const loadedModel = typeof metadata.model === "string" && metadata.model.trim() ? metadata.model : undefined;
    if (loadedModel) {
      setMossModel(loadedModel);
      mossModelManuallySelected.current = true;
    }
    const loadedWidth = metadataNumber(metadata, "width");
    const loadedHeight = metadataNumber(metadata, "height");
    const loadedSteps = metadataNumber(metadata, "steps");
    const loadedGuidance = metadataNumber(metadata, "guidance_scale");
    const loadedCount = metadataNumber(metadata, "n");
    const exactOutputSeed = metadataNumber(metadata, "output_seed");
    const loadedSeed = exactOutputSeed ?? metadataNumber(metadata, "seed");
    if (loadedWidth !== undefined) setWidth(String(loadedWidth));
    if (loadedHeight !== undefined) setHeight(String(loadedHeight));
    if (loadedSteps !== undefined) setSteps(String(loadedSteps));
    if (loadedGuidance !== undefined) setGuidance(String(loadedGuidance));
    if (loadedCount !== undefined) setCount(String(loadedCount));
    setSeed(loadedSeed !== undefined ? String(loadedSeed) : "42");
    const hasExplicitRandomizedBatch = typeof metadata.randomized_batch === "boolean";
    const randomizedBatchSetting = hasExplicitRandomizedBatch
      ? metadata.randomized_batch === true
      : loadedSeed === undefined && (loadedCount || 1) > 1;
    setRandomizedBatch(exactOutputSeed === undefined && randomizedBatchSetting);
    setRandomSeed(exactOutputSeed === undefined && (randomizedBatchSetting || loadedSeed === undefined));
    const loadedLayout = loadedLayoutSettings(metadata.layout_json);
    if (loadedLayout) {
      setLayoutMode(true);
      setLayoutJson(JSON.stringify(loadedLayout.json, null, 2));
      setLayoutCaption(loadedLayout.caption);
      setLayoutItems(loadedLayout.items);
    } else {
      setLayoutMode(false);
      setLayoutJson("");
      setLayoutCaption("A composed scene with clearly arranged elements.");
      setLayoutItems([]);
    }
    setOriginalPrompt("");
    setPromptSuggestion("");
    setShowAdvanced(true);
    setLoadedPromptId(id);
    setError("");
    setMessage(exactOutputSeed === undefined
      ? "Prompt and generation settings loaded into the workspace."
      : "Prompt, generation settings, and the exact output seed loaded for reproducibility.");
    window.setTimeout(() => document.querySelector(".control-panel")?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
    window.setTimeout(() => setLoadedPromptId((current) => current === id ? "" : current), 1800);
  };

  const deleteImage = async (image: GalleryImage) => {
    if (!window.confirm("Delete this generated output and its metadata?")) return;
    try { await api(`/api/images/${image.id}`, { method: "DELETE", headers: { "X-CSRF-Token": session?.csrf_token || "" } }); refresh(); }
    catch (err) { setError((err as Error).message); }
  };

  const clearHistory = async () => {
    if (!session) return;
    const confirmed = window.confirm(
      "Clear your account history? This permanently removes your conversations, generated images, and job history. Model files, your account, and other users' data are preserved.",
    );
    if (!confirmed) return;
    setError("");
    try {
      const result = await api("/api/account/history", { method: "DELETE", headers: { "X-CSRF-Token": session.csrf_token } });
      const created = await api("/api/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": session.csrf_token },
        body: JSON.stringify({ title: "New conversation" }),
      });
      setJobs([]);
      setGallery([]);
      setConversationMessages([]);
      setConversations([created]);
      setActiveConversationId(created.id);
      await refresh();
      const deleted = result.deleted || {};
      setMessage(`Your history was cleared: ${deleted.conversation_count || 0} conversations, ${deleted.image_count || 0} images, and ${deleted.reference_count || 0} stored records removed.`);
    } catch (err) { setError((err as Error).message); }
  };

  if (!session) return <AuthScreen setupRequired={setupRequired} username={username} password={password} setUsername={setUsername} setPassword={setPassword} error={authError} onSubmit={auth} />;
  const activeJobs = jobs.filter((job) => ["queued", "running", "cancelling"].includes(job.status));
  const galleryModels = Array.from(new Set(gallery.map((image) => typeof image.metadata.backend_model === "string" ? image.metadata.backend_model : "MOSS-Image"))).sort();
  const maxPixels = typeof system?.hardware?.configured_limits?.max_pixels === "number" ? system.hardware.configured_limits.max_pixels : null;
  const availableResolutionPresets = RESOLUTION_PRESETS.filter((preset) => maxPixels === null || preset.width * preset.height <= maxPixels);
  const selectedResolution = availableResolutionPresets.find((preset) => String(preset.width) === width && String(preset.height) === height)?.id || "custom";
  const visibleGallery = gallery.filter((image) => {
    const model = typeof image.metadata.backend_model === "string" ? image.metadata.backend_model : "MOSS-Image";
    return (!galleryQuery || image.prompt.toLowerCase().includes(galleryQuery.toLowerCase())) && (!galleryModel || model === galleryModel) && (!favoritesOnly || image.favorite);
  });
  return <div className={`app-shell${system?.moss_only !== false ? " moss-only" : ""}`}>
    <header className="topbar"><div className="brand"><div className="brand-mark">M</div><div><strong>MOSS<span>-Image</span></strong><small>LOCAL CREATIVE STUDIO</small></div></div><div className="top-actions"><div className={`worker-pill ${system?.vllm?.ok ? "online" : "offline"}`}><i /> {system?.vllm?.ok ? "vLLM worker online" : "vLLM worker offline"}</div><button className="icon-button" onClick={() => refresh()} title="Refresh"><RefreshCw size={17} /></button><button className="clear-history-button" onClick={clearHistory} title="Clear my history" aria-label="Clear my history"><Trash2 size={16} /><span>Clear history</span></button><button className="icon-button" onClick={async () => { await api("/api/auth/logout", { method: "POST", headers: { "X-CSRF-Token": session.csrf_token } }); setSession(null); setConversations([]); setActiveConversationId(""); setConversationMessages([]); setChatInput(""); setJobs([]); setGallery([]); setSystem(null); setModels(null); }} title="Log out"><LogOut size={17} /></button></div></header>
    <main className="workspace">
      <section className="hero"><div><div className="eyebrow">LOCAL ONLY</div><h1>Make something<br /><em>worth keeping.</em></h1><p>Generate realistic images with MOSS-Image locally. Your prompts and outputs stay on this host.</p></div><div className="hero-orbit"><div className="orbit-ring" /><div className="orbit-dot one" /><div className="orbit-dot two" /><span>AI</span></div></section>
      <ConversationWorkspace conversations={conversations} activeConversationId={activeConversationId} messages={conversationMessages} search={conversationSearch} input={chatInput} jobs={activeJobs} sending={chatSending} error={error} onNew={newConversation} onSelect={setActiveConversationId} onSearch={setConversationSearch} onInput={setChatInput} onSubmit={sendConversation} />
      <ConversationOutputActions messages={conversationMessages} loadedPromptId={loadedPromptId} onLoadPrompt={loadPrompt} />
      <div className="advanced-heading"><div><span className="section-kicker">ADVANCED WORKSPACE</span><p>Technical controls remain available for reproducible runs and diagnostics.</p></div><button type="button" className="ghost-toggle" onClick={() => setShowAdvanced(!showAdvanced)}><Settings2 size={15} /> {showAdvanced ? "Hide advanced controls" : "Show advanced controls"}</button></div>
      {showAdvanced && <>
      <div className="studio-grid">
        <form className="control-panel" onSubmit={submit}>

          <div className="panel-heading"><div><span className="section-kicker">01 / BRIEF</span><h2>Describe your image</h2></div><button type="button" className={`ghost-toggle ${layoutMode ? "selected" : ""}`} onClick={() => setLayoutMode(!layoutMode)}><Settings2 size={15} /> Layout JSON</button></div>
          <label className="field-label">Positive prompt<span>{prompt.length}/16000</span></label><textarea className="prompt-box" value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Describe the image you want to see..." />
          {promptSuggestion && <div className="prompt-suggestion"><div className="field-row"><strong>Suggested prompt · not applied</strong><button type="button" className="text-button" onClick={() => setPromptSuggestion("")}>Dismiss</button></div><textarea className="prompt-box suggestion-box" value={promptSuggestion} onChange={(e) => setPromptSuggestion(e.target.value)} /><button type="button" className="tiny-button" onClick={acceptPromptSuggestion}>Use suggestion</button></div>}
          {layoutMode && <><LayoutEditor canvasWidth={canvasDimension(width)} canvasHeight={canvasDimension(height)} caption={layoutCaption} items={layoutItems} onCaptionChange={setLayoutCaption} onItemsChange={setLayoutItems} /><div className="field-row"><label className="field-label">Advanced MOSS-Image layout JSON</label><button type="button" className="text-button" onClick={() => setLayoutJson(JSON.stringify(buildLayout(), null, 2))}>Sync from canvas</button></div><textarea className="layout-box" value={layoutJson} onChange={(e) => setLayoutJson(e.target.value)} placeholder="{\n  &quot;summary&quot;: ...\n}" /></>}
          <label className="field-label">Negative prompt <span>Optional</span></label><input className="text-input" value={negativePrompt} onChange={(e) => setNegativePrompt(e.target.value)} />

          <div className="controls-block"><div className="field-row"><span className="section-kicker">02 / PARAMETERS</span><button type="button" className="text-button" disabled={!system?.prompt_assistance?.configured} title={system?.prompt_assistance?.configured ? "Use the configured local prompt assistant" : "Configure VLLM_CHAT_URL and VLLM_CHAT_MODEL to enable local prompt assistance"} onClick={enhance}><Sparkles size={14} /> {system?.prompt_assistance?.configured ? "Enhance prompt" : "Prompt enhancement unavailable"}</button></div><label className="field-label parameter-field"><span className="parameter-field-name">Generation preset</span><select className="select-input" value="" onChange={(e) => applyGenerationPreset(e.target.value)}><option value="">Choose a reusable starting point…</option>{GENERATION_PRESETS.map((preset) => <option key={preset.id} value={preset.id}>{preset.label}</option>)}</select></label><label className="field-label parameter-field"><span className="parameter-field-name">MOSS-Image checkpoint</span><select className="select-input" value={mossModel} onChange={(e) => { setMossModel(e.target.value); mossModelManuallySelected.current = true; }}>{(models?.models || [{ id: "OpenMOSS-Team/MOSS-Image-SFT" }, { id: "OpenMOSS-Team/MOSS-Image-Base" }]).filter((model: any) => model.backend === "vllm-omni" || !model.backend).map((model: any) => <option key={model.id} value={model.id}>{model.id.split("/").pop()} {model.active ? "· active worker" : ""}</option>)}</select></label><label className="field-label parameter-field"><span className="parameter-field-name">Aspect ratio <small className="parameter-field-meta">{maxPixels ? `${maxPixels.toLocaleString()} px max` : "Auto limit loading…"}</small></span><select className="select-input" value={selectedResolution} onChange={(e) => { const preset = availableResolutionPresets.find((item) => item.id === e.target.value); if (preset) { setWidth(String(preset.width)); setHeight(String(preset.height)); } }}><option value="custom">Custom dimensions</option>{availableResolutionPresets.map((preset) => <option key={preset.id} value={preset.id}>{preset.label} · {preset.width}×{preset.height}</option>)}</select></label><div className="parameter-grid"><label className="parameter-input-field"><span>Width</span><input type="number" value={width} onChange={(e) => setWidth(e.target.value)} /></label><label className="parameter-input-field"><span>Height</span><input type="number" value={height} onChange={(e) => setHeight(e.target.value)} /></label><label className="parameter-input-field"><span>Steps</span><input type="number" value={steps} onChange={(e) => setSteps(e.target.value)} /></label><label className="parameter-input-field"><span>CFG</span><input type="number" value={guidance} onChange={(e) => setGuidance(e.target.value)} /></label><label className="parameter-input-field"><span>{randomizedBatch ? "Variations" : "Images"}</span><input type="number" value={count} onChange={(e) => setCount(e.target.value)} /></label></div><div className="seed-row"><label>Seed</label><input type="number" value={seed} disabled={randomSeed || randomizedBatch} onChange={(e) => setSeed(e.target.value)} /><label className="check"><input type="checkbox" checked={randomizedBatch || randomSeed} disabled={randomizedBatch} onChange={(e) => setRandomSeed(e.target.checked)} /> {randomizedBatch ? "Randomize each output" : "Randomize seed"}</label></div><label className="check batch-toggle"><input type="checkbox" checked={randomizedBatch} onChange={(e) => setRandomizedBatch(e.target.checked)} /> Generate randomized variations</label>{randomizedBatch && <small className="field-help batch-help">Generates {outputCount} independently seeded outputs.</small>}</div>
          {error && <div className="alert error"><CircleHelp size={16} /> {error}</div>}{message && <div className="alert success"><Check size={16} /> {message}</div>}
          <button className="primary-button" disabled={submitting}>{submitting ? <LoaderCircle className="spin" size={18} /> : <Send size={18} />} Generate with MOSS-Image<span>↗</span></button>
        </form>
        <aside className="side-column"><Queue jobs={jobs} activeJobs={activeJobs} csrf={session.csrf_token} refresh={refresh} /><WorkerCard system={system} /></aside>
      </div>
      <Gallery images={visibleGallery} query={galleryQuery} model={galleryModel} models={galleryModels} favoritesOnly={favoritesOnly} loadedPromptId={loadedPromptId} onQueryChange={setGalleryQuery} onModelChange={setGalleryModel} onFavoritesChange={setFavoritesOnly} onFavorite={toggleFavorite} onLoadPrompt={(image) => loadPrompt(image.id, savedPrompt(image), image.metadata)} onDelete={deleteImage} />
      </>}
    </main>
    <footer className="footer"><span>Moss-Image-Generator · local-first</span><span>Built for deliberate image-making <span className="footer-dot">●</span></span></footer>
  </div>;
}

// ---------------------------------------------------------------------------
// Authentication and conversation components
// ---------------------------------------------------------------------------
function AuthScreen(props: { setupRequired: boolean; username: string; password: string; setUsername: (v: string) => void; setPassword: (v: string) => void; error: string; onSubmit: (e: FormEvent) => void }) {
  return <div className="auth-screen"><div className="auth-card"><div className="brand centered"><div className="brand-mark">M</div><div><strong>MOSS<span>-Image</span></strong><small>LOCAL CREATIVE STUDIO</small></div></div><div className="auth-copy"><span className="eyebrow">LOCAL ONLY</span><h1>{props.setupRequired ? "Set up your studio." : "Welcome back."}</h1><p>{props.setupRequired ? "Create the first administrator account. Credentials stay in the local SQLite database." : "Sign in to access your local image workspace."}</p></div><form onSubmit={props.onSubmit}><label>Username<input className="text-input" value={props.username} onChange={(e) => props.setUsername(e.target.value)} autoComplete="username" required /></label><label>Password<input className="text-input" type="password" value={props.password} onChange={(e) => props.setPassword(e.target.value)} autoComplete={props.setupRequired ? "new-password" : "current-password"} minLength={12} required />{props.setupRequired && <small className="field-help">Use at least 12 characters.</small>}</label>{props.error && <div className="alert error">{props.error}</div>}<button className="primary-button">{props.setupRequired ? "Create account" : "Sign in"}<span>↗</span></button></form></div></div>;
}

function ConversationOutputActions({ messages, loadedPromptId, onLoadPrompt }: { messages: ConversationMessage[]; loadedPromptId: string; onLoadPrompt: (id: string, prompt: string, metadata?: Record<string, unknown>) => void }) {
  const outputs = messages.flatMap((message) => message.role === "assistant" ? (message.assets || []).filter((asset) => asset.asset_type === "image" && typeof asset.asset_id === "string").map((asset) => {
    const metadata = asset.metadata && typeof asset.metadata === "object" ? asset.metadata as Record<string, unknown> : undefined;
    const originalPrompt = typeof metadata?.original_prompt === "string" && metadata.original_prompt.trim() ? metadata.original_prompt : undefined;
    return { id: String(asset.asset_id), prompt: originalPrompt || (typeof asset.prompt === "string" ? asset.prompt : ""), filename: typeof asset.filename === "string" ? asset.filename : `moss-${String(asset.asset_id)}.png`, metadata };
  }) : []);
  if (!outputs.length) return null;
  return <div className="conversation-output-actions"><span>Conversation outputs</span>{outputs.slice(-6).map((output) => <div className="conversation-output-action" key={output.id}><strong>{output.filename}</strong><a href={`/api/images/${output.id}/file`} download={output.filename}><Download size={13} /> Download</a><button className={loadedPromptId === output.id ? "loaded" : ""} type="button" disabled={!output.prompt} onClick={() => onLoadPrompt(output.id, output.prompt, output.metadata)}><Download size={13} /> {loadedPromptId === output.id ? "Loaded" : "Load prompt"}</button></div>)}</div>;
}

function ConversationWorkspace(props: {
  conversations: Conversation[];
  activeConversationId: string;
  messages: ConversationMessage[];
  search: string;
  input: string;
  jobs: Job[];
  sending: boolean;
  error: string;
  onNew: () => void;
  onSelect: (id: string) => void;
  onSearch: (value: string) => void;
  onInput: (value: string) => void;
  onSubmit: (event?: FormEvent) => void;
}) {
  const composerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    if (!props.sending && props.input.trim()) props.onSubmit();
  };
  return <section className="conversation-shell">
    <aside className="conversation-sidebar"><div className="conversation-sidebar-head"><div><span className="section-kicker">CONVERSATIONS</span><h2>Your studio</h2></div><button className="icon-button" onClick={props.onNew} title="New conversation"><Plus size={18} /></button></div><button className="new-conversation-button" onClick={props.onNew}><Plus size={15} /> New conversation</button><label className="conversation-search"><Search size={13} /><input value={props.search} onChange={(event) => props.onSearch(event.target.value)} placeholder="Search conversations" /></label><div className="conversation-list">{props.conversations.map((conversation) => <button key={conversation.id} className={`conversation-row ${conversation.id === props.activeConversationId ? "active" : ""}`} onClick={() => props.onSelect(conversation.id)}><MessageCircle size={15} /><span>{conversation.title}</span></button>)}</div></aside>
    <div className="chat-panel"><div className="chat-heading"><div><span className="section-kicker">CONVERSATIONAL IMAGE STUDIO</span><h2>{props.conversations.find((item) => item.id === props.activeConversationId)?.title || "New conversation"}</h2></div><div className="chat-heading-actions"><a className="icon-button" href={`/api/conversations/${props.activeConversationId}/export`} title="Export conversation" download><Download size={16} /></a><span className="chat-local-badge">LOCAL ONLY</span></div></div>{props.error && <div className="conversation-alert"><CircleHelp size={14} /> {props.error}</div>}<div className="chat-messages">{!props.messages.length && <div className="chat-empty"><Sparkles size={24} /><h3>What would you like to make?</h3><p>Describe a new image. Follow-up messages carry forward prompt context; MOSS-Image does not receive the previous raster.</p><div className="suggestion-row"><button onClick={() => props.onInput("Generate a photorealistic medieval knight in a snowy forest.")}>New image</button><button onClick={() => props.onInput("Continue the concept with a more cinematic atmosphere.")}>Continue the concept</button></div></div>}{props.messages.map((message) => <article className={`chat-message ${message.role}`} key={message.id}><div className="message-avatar">{message.role === "user" ? "YOU" : "M"}</div><div className="message-body"><div className="message-text">{message.content}</div>{message.assets?.length ? <div className="message-assets">{message.assets.map((asset, index) => typeof asset.url === "string" && <div className="message-asset" key={`${message.id}-${asset.asset_id || index}`}><img src={asset.url} alt={typeof asset.filename === "string" ? asset.filename : "Conversation image"} /></div>)}</div> : null}{["queued", "running", "cancelling"].includes(message.status) ? <div className="message-status"><LoaderCircle className="spin" size={14} /> {message.status === "queued" ? "Queued for local inference" : message.status === "cancelling" ? "Cancelling local operation" : "Generating locally"}</div> : null}{message.status === "cancelled" && <div className="message-failure"><CircleHelp size={14} /> {message.error || "Cancelled before local inference started."}</div>}{message.status === "failed" && <div className="message-failure"><CircleHelp size={14} /> {message.error || "The local operation failed."}</div>}{message.plan && message.role === "assistant" && <small className="message-provenance">{String(message.plan.backend || "local backend")} · {String(message.plan.model || "model")}</small>}</div></article>)}{props.jobs.length > 0 && <div className="chat-progress"><LoaderCircle className="spin" size={15} /><div className="chat-progress-copy"><span>{progressLabel(props.jobs[0])}</span><ProgressMeter job={props.jobs[0]} /></div></div>}</div><form className="chat-composer" onSubmit={props.onSubmit}><div className="composer-row"><textarea value={props.input} onChange={(event) => props.onInput(event.target.value)} onKeyDown={composerKeyDown} placeholder="Describe what you want to generate…" rows={2} /><button className="send-button" disabled={props.sending || !props.input.trim()}>{props.sending ? <LoaderCircle className="spin" size={18} /> : <Send size={18} />}</button></div><small className="composer-help">Press Enter to send · Shift+Enter for a new line. Follow-up requests reuse saved prompt context; MOSS-Image remains text-to-image.</small></form></div>
  </section>;
}

// ---------------------------------------------------------------------------
// Queue, layout, worker, and gallery components
// ---------------------------------------------------------------------------
function Queue({ jobs, activeJobs, csrf, refresh }: { jobs: Job[]; activeJobs: Job[]; csrf: string; refresh: () => void }) {
  const cancel = async (id: string) => { try { await api(`/api/jobs/${id}/cancel`, { method: "POST", headers: { "X-CSRF-Token": csrf } }); refresh(); } catch { /* refresh will show state */ } };
  const retry = async (id: string) => { try { await api(`/api/jobs/${id}/retry`, { method: "POST", headers: { "X-CSRF-Token": csrf } }); refresh(); } catch { /* refresh will show state */ } };
  return <section className="side-card"><div className="side-card-heading"><div><span className="section-kicker">03 / QUEUE</span><h3>In progress <span>{activeJobs.length}</span></h3></div><div className="queue-ring"><span>{activeJobs.length ? "BUSY" : "IDLE"}</span></div></div>{activeJobs.length ? activeJobs.slice(0, 3).map((job) => <div className="job-row" key={job.id}><div className="job-icon"><LoaderCircle className="spin" size={16} /></div><div className="job-copy"><strong>MOSS-Image generation</strong><span>{progressLabel(job)} · {String(job.params?.width || "")}×{String(job.params?.height || "")}</span><ProgressMeter job={job} /></div>{job.status === "queued" && <button className="tiny-button" onClick={() => cancel(job.id)}>Cancel</button>}{job.status === "cancelling" && <span className="queue-cancelling">Cancelling…</span>}</div>) : <div className="side-empty"><span>Nothing running.</span><small>Jobs will appear here as they move through the worker.</small></div>}<div className="history-line">{jobs.filter((job) => !["queued", "running", "cancelling"].includes(job.status)).slice(0, 3).map((job) => <div className="history-row" key={job.id}><span><span className={`status-dot ${job.status}`} /> {job.status}</span>{job.status === "failed" && <button className="tiny-button" onClick={() => retry(job.id)}>Retry</button>}<time>{new Date(job.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time></div>)}</div></section>;
}

function LayoutEditor({ canvasWidth, canvasHeight, caption, items, onCaptionChange, onItemsChange }: { canvasWidth: number; canvasHeight: number; caption: string; items: LayoutItem[]; onCaptionChange: (value: string) => void; onItemsChange: (items: LayoutItem[]) => void }) {
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const drag = useRef<{ id: string; mode: "move" | "resize"; startX: number; startY: number; bbox: LayoutItem["bbox"] } | null>(null);

  const updateItem = (id: string, update: (item: LayoutItem) => LayoutItem) => {
    onItemsChange(items.map((item) => item.id === id ? update(item) : item));
  };
  const addItem = (label: "text" | "object") => {
    const id = `${label}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const bbox: LayoutItem["bbox"] = label === "text"
      ? [64, Math.round(canvasWidth * 0.15), 192, Math.round(canvasWidth * 0.85)]
      : [Math.round(canvasHeight * 0.35), Math.round(canvasWidth * 0.2), Math.round(canvasHeight * 0.8), Math.round(canvasWidth * 0.8)];
    onItemsChange([...items, { id, label, caption: label === "text" ? "A prominent text element." : "Describe this object and its appearance.", text: label === "text" ? "TITLE" : "", bbox }]);
  };
  const beginDrag = (event: PointerEvent<HTMLElement>, item: LayoutItem, mode: "move" | "resize") => {
    event.preventDefault();
    event.stopPropagation();
    drag.current = { id: item.id, mode, startX: event.clientX, startY: event.clientY, bbox: [...item.bbox] as LayoutItem["bbox"] };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const moveDrag = (event: PointerEvent<HTMLElement>) => {
    const active = drag.current;
    const canvas = canvasRef.current;
    if (!active || !canvas) return;
    const bounds = canvas.getBoundingClientRect();
    const dx = (event.clientX - active.startX) * canvasWidth / bounds.width;
    const dy = (event.clientY - active.startY) * canvasHeight / bounds.height;
    let [top, left, bottom, right] = active.bbox;
    if (active.mode === "move") {
      const itemHeight = bottom - top;
      const itemWidth = right - left;
      top = Math.max(0, Math.min(canvasHeight - itemHeight, top + dy));
      left = Math.max(0, Math.min(canvasWidth - itemWidth, left + dx));
      bottom = top + itemHeight;
      right = left + itemWidth;
    } else {
      bottom = Math.max(top + 24, Math.min(canvasHeight, bottom + dy));
      right = Math.max(left + 24, Math.min(canvasWidth, right + dx));
    }
    updateItem(active.id, (item) => ({ ...item, bbox: [Math.round(top), Math.round(left), Math.round(bottom), Math.round(right)] }));
  };
  const endDrag = () => { drag.current = null; };
  const renderCanvasItem = (item: LayoutItem) => {
    const [top, left, bottom, right] = item.bbox;
    return <div key={item.id} className={`layout-item ${item.label}`} style={{ top: `${top / canvasHeight * 100}%`, left: `${left / canvasWidth * 100}%`, width: `${(right - left) / canvasWidth * 100}%`, height: `${(bottom - top) / canvasHeight * 100}%` }} onPointerDown={(event) => beginDrag(event, item, "move")}><span>{item.label === "text" ? item.text || "Text" : item.caption || "Object"}</span><button type="button" className="layout-remove" onPointerDown={(event) => event.stopPropagation()} onClick={() => onItemsChange(items.filter((candidate) => candidate.id !== item.id))}>×</button><span className="layout-resize" onPointerDown={(event) => beginDrag(event, item, "resize")} /></div>;
  };

  return <section className="layout-editor"><div className="field-row"><label className="field-label">Visual layout editor <span>Drag boxes; resize from the lower-right corner</span></label><div className="layout-add-actions"><button type="button" className="tiny-button" onClick={() => addItem("text")}>+ Text</button><button type="button" className="tiny-button" onClick={() => addItem("object")}>+ Object</button></div></div><input className="text-input" value={caption} onChange={(event) => onCaptionChange(event.target.value)} placeholder="Describe the overall scene and style" /><div ref={canvasRef} className="layout-canvas" style={{ aspectRatio: `${canvasWidth} / ${canvasHeight}` }} onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={endDrag}>{items.map(renderCanvasItem)}{!items.length && <span className="layout-empty">Add text or object elements to begin.</span>}</div>{items.map((item) => <div className="layout-item-form" key={`${item.id}-form`}><select className="mini-select" value={item.label} onChange={(event) => updateItem(item.id, (current) => ({ ...current, label: event.target.value as LayoutItem["label"] }))}><option value="text">Text</option><option value="object">Object</option></select><input className="text-input" value={item.caption} onChange={(event) => updateItem(item.id, (current) => ({ ...current, caption: event.target.value }))} placeholder="Describe this element" />{item.label === "text" && <input className="text-input" value={item.text} onChange={(event) => updateItem(item.id, (current) => ({ ...current, text: event.target.value }))} placeholder="Rendered text" />}</div>)}</section>;
}

function WorkerCard({ system }: { system: any }) {
  const gpu = system?.hardware?.gpu;
  const runtime = system?.hardware?.runtime;
  const limits = system?.hardware?.configured_limits;
  const gpuLabel = gpu?.available ? gpu.name : "Not detected";
  const gpuUsage = gpu?.available && gpu.memory_used_bytes != null && gpu.memory_total_bytes != null
    ? `${formatBytes(gpu.memory_used_bytes)} / ${formatBytes(gpu.memory_total_bytes)} VRAM`
    : "VRAM unavailable";
  const gpuLoad = gpu?.available && gpu.utilization_percent != null ? `${Math.round(gpu.utilization_percent)}% GPU` : "GPU load unavailable";
  return <section className="side-card worker-card"><div className="side-card-heading"><div><span className="section-kicker">SYSTEM</span><h3>Worker health</h3></div><span className={`health-badge ${system?.vllm?.ok ? "good" : "warn"}`}>{system?.vllm?.ok ? "READY" : "CHECK"}</span></div><div className="metric"><span>MOSS backend</span><strong>{system?.vllm?.ok ? "vLLM-Omni" : "Offline"}</strong></div><div className="metric"><span>Network policy</span><strong>{system?.offline_mode ? "Offline mode · downloads disabled" : "Local-first mode"}</strong></div><div className="metric"><span>Web runtime GPU</span><strong>{gpuLabel}</strong></div><div className="metric"><span>Web runtime memory / load</span><strong>{gpuUsage} · {gpuLoad}</strong></div><div className="metric"><span>Web runtime PyTorch / CUDA</span><strong>{runtime?.torch_version ? `${runtime.torch_version} · ${runtime.torch_cuda_version || "CPU build"}` : "Unavailable"}</strong></div><div className="metric"><span>Web runtime CUDA devices</span><strong>{runtime?.torch_cuda_available ? `${runtime.torch_device_count} detected` : runtime?.error || "Unavailable"}</strong></div><div className="metric"><span>Web runtime RAM / disk</span><strong>{formatBytes(system?.hardware?.ram_bytes)} / {formatBytes(system?.hardware?.disk_free_bytes)}</strong></div><div className="metric"><span>Media usage / cap</span><strong>{formatBytes(limits?.media_usage_bytes)} / {formatBytes(limits?.max_media_bytes)}</strong></div><div className="worker-note"><CircleHelp size={15} /><span>vLLM worker health is reported separately. The diagnostics above describe the web runtime; the worker owns GPU inference.</span></div></section>;
}

function Gallery({ images, query, model, models, favoritesOnly, loadedPromptId, onQueryChange, onModelChange, onFavoritesChange, onFavorite, onLoadPrompt, onDelete }: { images: GalleryImage[]; query: string; model: string; models: string[]; favoritesOnly: boolean; loadedPromptId: string; onQueryChange: (value: string) => void; onModelChange: (value: string) => void; onFavoritesChange: (value: boolean) => void; onFavorite: (image: GalleryImage) => void; onLoadPrompt: (image: GalleryImage) => void; onDelete: (image: GalleryImage) => void }) {
  return <section className="gallery-section"><div className="gallery-heading"><div><span className="section-kicker">04 / ARCHIVE</span><h2>Recent work <span>{images.length}</span></h2></div><div className="gallery-tools"><label className="gallery-search"><Search size={14} /><input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="Search prompts" /></label><select className="mini-select" value={model} onChange={(event) => onModelChange(event.target.value)}><option value="">All models</option>{models.map((item) => <option key={item} value={item}>{item.split("/").pop()}</option>)}</select><button className={`tiny-button ${favoritesOnly ? "selected" : ""}`} onClick={() => onFavoritesChange(!favoritesOnly)}><Star size={13} /> Favorites</button><button className="icon-button" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}><ChevronDown size={17} /></button></div></div>{images.length ? <div className="gallery-grid">{images.map((image) => <article className="gallery-card" key={image.id}><div className="gallery-image"><img src={image.url} alt={image.prompt} /><a href={image.url} download={`moss-image-generator-${image.id}.png`} className="download-button"><Download size={16} /></a><div className="gallery-tag">MOSS-IMAGE</div></div><div className="gallery-caption"><strong>{image.prompt.slice(0, 100)}{image.prompt.length > 100 ? "…" : ""}</strong><small>{new Date(image.created_at).toLocaleString()} · {typeof image.metadata.backend_model === "string" ? image.metadata.backend_model : "MOSS-Image"}</small><div className="gallery-actions"><button className={`gallery-action-button ${image.favorite ? "favorite" : ""}`} onClick={() => onFavorite(image)}><Star size={13} fill={image.favorite ? "currentColor" : "none"} /> {image.favorite ? "Favorited" : "Favorite"}</button><button className={`gallery-action-button ${loadedPromptId === image.id ? "loaded" : ""}`} onClick={() => onLoadPrompt(image)}><Download size={13} /> {loadedPromptId === image.id ? "Loaded" : "Load prompt"}</button><a className="gallery-action-button" href={`/api/images/${image.id}/metadata`}><Download size={13} /> Metadata</a><button className="gallery-action-button danger" onClick={() => onDelete(image)}><Trash2 size={13} /> Delete</button></div></div></article>)}</div> : <div className="gallery-empty"><ImagePlus size={24} /><h3>{query || model || favoritesOnly ? "No matching work." : "Your archive is quiet."}</h3><p>{query || model || favoritesOnly ? "Adjust the archive filters to see other outputs." : "Generated images will land here with their full metadata."}</p></div>}</section>;
}

createRoot(document.getElementById("root")!).render(<App />);
