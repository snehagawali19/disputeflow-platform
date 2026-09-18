import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity, AlertTriangle, ArrowRight, Check, ChevronDown, Clock3, Database,
  FileCheck2, Fingerprint, Gauge, Layers3, LockKeyhole, Plus, RefreshCw,
  Search, ShieldCheck, Sparkles, TerminalSquare, UserCheck, X,
} from "lucide-react";
import "./App.css";
import { usePipelineSocket } from "./hooks/usePipelineSocket";
import { api } from "./utils/api";
import type { DisputeSession, GitHubStatus, ModelStatus, PipelineEvent } from "./types/api";

const CORE_STAGES = ["intake", "evidence_assembly", "strategy_formulation", "human_review", "response_drafting", "filing", "awaiting_outcome"];

type Evidence = { evidence_id: string; evidence_type: string; title: string; content: string; relevance_score: number; source: string };
type CaseView = {
  case_id?: string; status?: string; reason_code?: string; risk_level?: string;
  dispute_amount?: number; dispute_currency?: string; intake_summary?: string;
  evidence_items?: Evidence[]; evidence_gaps?: string[];
  strategy?: { recommended_action?: string; win_probability?: number; confidence?: number; evidence_strength?: string; reasoning?: string; model_inputs?: string[] };
  response?: { rebuttal_letter?: string; evidence_summary?: string; word_count?: number };
  filing?: { simulated?: boolean; status?: string; confirmation_reference?: string; notes?: string };
  agent_trace?: Array<{ agent?: string; status?: string; output_summary?: string; error?: string }>;
  error_log?: string[]; human_approvals?: Array<{ open?: boolean; origin?: string; decision?: string; automatic?: boolean }>;
  lessons_learned?: string[];
  source?: {
    source_type?: string; source_repository?: string; source_ref?: string; source_commit_sha?: string;
    source_file_path?: string; source_blob_sha?: string; artifact_count?: number;
  };
};

const titleCase = (value = "") => value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
const formatMoney = (amount = 0, currency = "USD") => new Intl.NumberFormat(currency === "INR" ? "en-IN" : "en-US", { style: "currency", currency, maximumFractionDigits: 0 }).format(amount);
const percent = (value?: number) => value == null ? "—" : `${Math.round(value * 100)}%`;
const compactText = (value = "", limit = 320) => {
  const normalized = value.replace(/\s+/g, " ").trim();
  return normalized.length > limit ? `${normalized.slice(0, limit).trimEnd()}…` : normalized;
};
const unsafeDraft = (value = "") => /\b(?:win probability|model confidence|predicted win probability)\b/i.test(value);

function App() {
  const [sessions, setSessions] = useState<DisputeSession[]>([]);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [caseDetail, setCaseDetail] = useState<any>(null);
  const [events, setEvents] = useState<PipelineEvent[]>([]);
  const [currentStage, setCurrentStage] = useState("");
  const [needsReview, setNeedsReview] = useState(false);
  const [loading, setLoading] = useState(false);
  const [systemReady, setSystemReady] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [showComposer, setShowComposer] = useState(false);
  const [showRaw, setShowRaw] = useState(false);
  const [queueFilter, setQueueFilter] = useState<"all" | "github" | "review" | "ready" | "outcome">("all");
  const [githubStatus, setGithubStatus] = useState<GitHubStatus | null>(null);
  const [syncState, setSyncState] = useState<"idle" | "loading" | "success" | "failure">("idle");
  const [syncMessage, setSyncMessage] = useState("");
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null);
  const [pipelineInFlight, setPipelineInFlight] = useState(false);
  const decisionLock = useRef(false);
  const [formData, setFormData] = useState({
    dispute_amount: 15000, dispute_currency: "INR", reason_code: "product_not_received",
    dispute_phase: "chargeback", merchant_name: "TechStore India", customer_email: "customer@example.com",
    payment_method: "card", avs_match: true, cvv_verified: true, three_ds_authenticated: false, delivery_proof: "",
  });

  useEffect(() => {
    const reduced = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const coarse = typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches;
    if (reduced || coarse) return;
    const move = (event: PointerEvent) => {
      document.documentElement.style.setProperty("--cursor-x", `${event.clientX}px`);
      document.documentElement.style.setProperty("--cursor-y", `${event.clientY}px`);
    };
    const targetState = (event: PointerEvent) => {
      const target = event.target;
      const interactive = target instanceof Element && Boolean(target.closest("button, input, select, textarea, .session-row"));
      document.documentElement.toggleAttribute("data-cursor-target", interactive);
    };
    window.addEventListener("pointermove", move, { passive: true });
    window.addEventListener("pointerover", targetState, { passive: true });
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerover", targetState);
      document.documentElement.removeAttribute("data-cursor-target");
    };
  }, []);

  const currentCase = (caseDetail?.case ?? {}) as CaseView;

  const refreshActive = async (id = activeSession) => {
    if (!id) return;
    const detail = await api.get(id);
    setCaseDetail(detail);
    const status = (detail.case as CaseView)?.status;
    if (status) setCurrentStage(status === "under_review" ? "intake" : status);
    setNeedsReview(((detail.case as CaseView)?.human_approvals ?? []).some((entry) => entry.open));
  };

  const socketReady = usePipelineSocket(activeSession, (data) => {
    if (data.event === "snapshot" && data.events) setEvents(data.events);
    else setEvents((previous) => [...previous.filter((event) => event.sequence !== data.sequence), data]);
    if (data.next_stage) setCurrentStage(data.next_stage);
    if (data.event === "pipeline_started" || data.event === "stage_completed") setPipelineInFlight(true);
    if (data.event === "pipeline_completed") { setCurrentStage(data.stage || "completed"); setPipelineInFlight(false); }
    if (data.event === "pipeline_failed") setPipelineInFlight(false);
    if (data.needs_human_approval || data.next_stage === "human_review") setNeedsReview(true);
    if (data.event === "human_decision") setNeedsReview(false);
    refreshActive(activeSession).catch(() => undefined);
  });

  const fetchSessions = async () => {
    try {
      const [rows, status, model] = await Promise.all([api.list(), api.githubStatus(), api.modelStatus()]);
      setSessions(rows);
      setGithubStatus(status);
      setModelStatus(model);
      if (!status.allow_local_create) setShowComposer(false);
      setSystemReady(true); setError("");
    } catch (err) {
      setSystemReady(false); setError(err instanceof Error ? err.message : "Unable to reach the DisputeFlow API");
    }
  };

  const syncGithub = async () => {
    setSyncState("loading"); setSyncMessage(""); setError("");
    try {
      const result = await api.githubSync();
      setSyncState("success");
      setSyncMessage(`Imported ${result.imported}, updated ${result.updated}, unchanged ${result.unchanged}, rejected ${result.rejected}.`);
      await fetchSessions();
    } catch (err) {
      setSyncState("failure");
      setSyncMessage(err instanceof Error ? err.message : "GitHub sync failed");
      setError(err instanceof Error ? err.message : "GitHub sync failed");
    }
  };

  const fetchOneAndStart = async () => {
    setSyncState("loading"); setSyncMessage(""); setError(""); setLoading(true);
    try {
      const result = await api.githubFetchOneAndStart();
      setActiveSession(result.session_id); setEvents([]); setNeedsReview(false); setCaseDetail(null); setPipelineInFlight(true); setCurrentStage("intake");
      setSyncState("success");
      setSyncMessage(`Fetched ${result.file_path} and started its analysis pipeline.`);
      await fetchSessions();
      await refreshActive(result.session_id);
    } catch (err) {
      setSyncState("failure");
      setSyncMessage(err instanceof Error ? err.message : "Could not fetch a GitHub case");
      setError(err instanceof Error ? err.message : "Could not fetch a GitHub case");
    } finally { setLoading(false); }
  };

  useEffect(() => { fetchSessions(); }, []);

  const createDispute = async () => {
    setLoading(true); setError("");
    try {
      const payload: Record<string, unknown> = { ...formData };
      delete payload.delivery_proof;
      if (formData.delivery_proof.trim()) payload.artifacts = [{ evidence_type: "delivery_proof", title: "Carrier delivery confirmation", content: formData.delivery_proof.trim() }];
      const result = await api.create(payload);
      setActiveSession(result.session_id); setEvents([]); setNeedsReview(false); setCaseDetail(null);
      await fetchSessions(); return result.session_id;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create dispute"); return null;
    } finally { setLoading(false); }
  };

  const startPipeline = async (sessionId: string) => {
    setCurrentStage("intake"); setPipelineInFlight(true);
    try { await api.start(sessionId); await refreshActive(sessionId); }
    catch (err) { setPipelineInFlight(false); setError(err instanceof Error ? err.message : "Failed to start pipeline"); }
  };

  const handleCreateAndStart = async () => {
    const id = await createDispute();
    if (id) { await new Promise((resolve) => setTimeout(resolve, 200)); await startPipeline(id); setShowComposer(false); }
  };

  const submitDecision = async (decision: string) => {
    if (!activeSession || decisionLock.current) return;
    decisionLock.current = true; setLoading(true); setPipelineInFlight(true);
    try { await api.decide(activeSession, decision); setNeedsReview(false); await refreshActive(); }
    catch (err) {
      const message = err instanceof Error ? err.message : "Decision failed";
      if (message.includes("409")) { await refreshActive(); setError(""); }
      else setError(message);
    }
    finally { decisionLock.current = false; setLoading(false); }
  };

  const submitOutcome = async (outcome: string) => {
    if (!activeSession) return; setLoading(true);
    try { await api.outcome(activeSession, outcome, "Recorded from operations console"); await Promise.all([fetchSessions(), refreshActive()]); }
    catch (err) { setError(err instanceof Error ? err.message : "Outcome update failed"); }
    finally { setLoading(false); }
  };

  const viewCase = async (sessionId: string) => {
    setActiveSession(sessionId); setEvents([]);
    try { await refreshActive(sessionId); setShowComposer(false); }
    catch (err) { setError(err instanceof Error ? err.message : "Failed to load case"); }
  };

  const filteredSessions = useMemo(() => {
    const term = search.trim().toLowerCase();
    return sessions.filter((session) => {
      if (queueFilter === "github" && session.source_type !== "github") return false;
      if (queueFilter === "review" && !(session.needs_human_review || session.pipeline_status === "awaiting_human_review" || session.status === "pending_human_review")) return false;
      if (queueFilter === "ready" && session.pipeline_status !== "created" && session.status !== "open") return false;
      if (queueFilter === "outcome" && session.pipeline_status !== "awaiting_outcome" && session.status !== "awaiting_outcome") return false;
      if (!term) return true;
      return [session.session_id, session.case_id, session.reason_code, session.status].some((value) => String(value).toLowerCase().includes(term));
    });
  }, [search, sessions, queueFilter]);
  const completedStages = useMemo(() => new Set(events.flatMap((event) => [event.stage, event.next_stage]).filter(Boolean) as string[]), [events]);
  const visibleStages = useMemo(() => currentCase.lessons_learned?.length || currentStage === "completed" ? [...CORE_STAGES, "feedback_loop", "completed"] : CORE_STAGES, [currentCase.lessons_learned, currentStage]);
  const awaitingOutcome = currentStage === "awaiting_outcome" || currentCase.status === "awaiting_outcome";
  const openApproval = [...(currentCase.human_approvals ?? [])].reverse().find((entry) => entry.open);
  const automaticApproval = [...(currentCase.human_approvals ?? [])].reverse().find((entry) => !entry.open && entry.decision === "approve" && entry.automatic);
  const reviewOrigin = openApproval?.origin ?? "strategy";
  const reviewCopy = reviewOrigin === "evidence" ? "Automated evidence scoring was unavailable or material evidence gaps remain. Validate the evidence before allowing strategy analysis." : reviewOrigin === "filing" ? "Package validation failed. An override creates only a simulated filing record." : "The recommendation is uncertain or sensitive enough to require an analyst decision.";
  const reviewCount = sessions.filter((session) => session.pipeline_status === "awaiting_human_review" || session.status === "pending_human_review").length;
  const activeCount = sessions.filter((session) => !["won", "lost", "accepted"].includes(session.status)).length;
  const evidence = currentCase.evidence_items ?? [];
  const hasWarnings = Boolean(currentCase.evidence_gaps?.length || currentCase.error_log?.length);
  const draftWithheld = unsafeDraft(currentCase.response?.rebuttal_letter);

  return <div className="app-shell"><div className="cursor-dot" aria-hidden="true" /><div className="cursor-ring" aria-hidden="true" />
    <div className="ambient ambient-one" /><div className="ambient ambient-two" />
    <header className="topbar">
      <div className="brand-lockup"><div className="brand-mark"><ShieldCheck size={20} /></div><div><div className="brand-name">DisputeFlow</div><div className="brand-kicker">Resolution Intelligence</div></div></div>
      <div className="topbar-actions"><div className={`system-pill ${systemReady ? "online" : "offline"}`}><span className="status-light" /><span>{systemReady ? "Systems operational" : "API unavailable"}</span></div><button className="icon-button" aria-label="Refresh cases" onClick={fetchSessions}><RefreshCw size={16} /></button>{githubStatus?.allow_local_create ? <button className="primary-button compact" onClick={() => setShowComposer((value) => !value)}><Plus size={16} /> New case</button> : null}</div>
    </header>

    <main className="command-center">
      <section className="hero-row"><div className="hero-copy"><div className="eyebrow"><Sparkles size={13} /> Operations command center</div><h1>Make the record clear.<br /><em>Make the next step human.</em></h1><p>DisputeFlow organizes supplied evidence, surfaces uncertainty, and gives your team a calm path from intake to resolution.</p><div className="hero-notes"><span><i className="status-light amber" /> Evidence-led</span><span><i className="status-light" /> Human oversight</span></div></div><div className="hero-art" aria-label="A case moving from evidence to resolution"><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="hero-axis"><span>evidence</span><b /><span>review</span><b /><span>resolve</span></div><div className="hero-core"><span className="status-light" /></div><div className="hero-art-note"><span>Filing simulated</span><span>Human review gates active</span></div></div></section>
      {error && <div className="alert-banner" role="alert"><AlertTriangle size={17} /><span>{error}</span><button onClick={() => setError("")}><X size={15} /></button></div>}
      <section className="github-source-panel panel-glow" aria-label="GitHub source">
        <div className="section-heading"><div><span className="section-index">00</span><h2>GitHub source</h2><p>Versioned repository input. Not live Stripe data. Filing remains simulated.</p></div>
          <div className="button-row"><button className="secondary-button compact" onClick={syncGithub} disabled={syncState === "loading"}><RefreshCw size={16} /> Sync all</button><button className="primary-button compact" onClick={fetchOneAndStart} disabled={syncState === "loading"}>{syncState === "loading" ? <RefreshCw className="spin" size={16} /> : <Fingerprint size={16} />}{syncState === "loading" ? "Fetching case" : "Fetch & analyze one"}</button></div>
        </div>
        <div className="github-meta-grid">
          <div><span>Repository</span><strong>{githubStatus?.repository || "not configured"}</strong></div>
          <div><span>Branch/ref</span><strong>{githubStatus?.ref || "—"}</strong></div>
          <div><span>Last synchronized commit</span><strong>{githubStatus?.last_commit_sha ? githubStatus.last_commit_sha.slice(0, 12) : "—"}</strong></div>
          <div><span>Last synchronization time</span><strong>{githubStatus?.last_sync_at ? new Date(githubStatus.last_sync_at).toLocaleString() : "—"}</strong></div>
          <div><span>Imported case count</span><strong>{githubStatus?.imported_count ?? 0}</strong></div>
          <div><span>Rejected case count</span><strong>{githubStatus?.rejected_count ?? 0}</strong></div>
          <div><span>Webhook status</span><strong>{githubStatus?.webhook_configured ? "Signature verification enabled" : "Not configured — use manual sync"}</strong></div>
        </div>
        {syncState !== "idle" && <div className={`sync-banner ${syncState}`}>{syncMessage || (syncState === "loading" ? "Synchronizing GitHub cases…" : "")}</div>}
      </section>
      <section className="journey-band" aria-label="Dispute resolution journey"><div className="journey-intro"><span className="section-index">01</span><div><strong>The resolution path</strong><p>One case. One accountable record.</p></div></div><div className="journey-steps"><div className="journey-step active"><span>01</span><strong>Gather</strong><small>facts & evidence</small></div><div className="journey-line" /><div className="journey-step"><span>02</span><strong>Understand</strong><small>issues & gaps</small></div><div className="journey-line" /><div className="journey-step"><span>03</span><strong>Review</strong><small>human checkpoint</small></div><div className="journey-line" /><div className="journey-step"><span>04</span><strong>Resolve</strong><small>decision & record</small></div></div></section>
      <section className="metric-strip" aria-label="Operational metrics"><div><span>Open cases</span><strong>{activeCount}</strong><Activity size={17} /></div><div><span>Review queue</span><strong>{reviewCount}</strong><UserCheck size={17} /></div><div><span>Selected probability</span><strong>{percent(currentCase.strategy?.win_probability)}</strong><Gauge size={17} /></div><div><span>Evidence records</span><strong>{evidence.length || "—"}</strong><Database size={17} /></div></section>

      {showComposer && githubStatus?.allow_local_create && <section className="composer-panel panel-glow">
        <div className="section-heading"><div><span className="section-index">01</span><h2>Create Dispute</h2><p>Open a case using verified transaction facts.</p></div><button className="icon-button subtle" aria-label="Close composer" onClick={() => setShowComposer(false)}><X size={16} /></button></div>
        <div className="form-grid">
          <label><span>Dispute amount</span><div className="input-with-prefix"><b>{formData.dispute_currency}</b><input type="number" value={formData.dispute_amount} onChange={(event) => setFormData({ ...formData, dispute_amount: Number(event.target.value) })} /></div></label>
          <label><span>Reason code</span><div className="select-wrap"><select value={formData.reason_code} onChange={(event) => setFormData({ ...formData, reason_code: event.target.value })}><option value="fraudulent">Fraudulent</option><option value="unauthorized">Unauthorized</option><option value="product_not_received">Product not received</option><option value="product_unacceptable">Product unacceptable</option><option value="duplicate">Duplicate</option><option value="subscription_canceled">Subscription canceled</option><option value="credit_not_processed">Credit not processed</option><option value="general">General</option></select><ChevronDown size={14} /></div></label>
          <label><span>Merchant</span><input type="text" value={formData.merchant_name} onChange={(event) => setFormData({ ...formData, merchant_name: event.target.value })} /></label>
          <label><span>Customer email</span><input type="email" value={formData.customer_email} onChange={(event) => setFormData({ ...formData, customer_email: event.target.value })} /></label>
          <label><span>Dispute phase</span><div className="select-wrap"><select value={formData.dispute_phase} onChange={(event) => setFormData({ ...formData, dispute_phase: event.target.value })}><option value="retrieval">Retrieval</option><option value="chargeback">Chargeback</option><option value="pre_arbitration">Pre-arbitration</option><option value="arbitration">Arbitration</option></select><ChevronDown size={14} /></div></label>
          <label><span>Payment rail</span><div className="select-wrap"><select value={formData.payment_method} onChange={(event) => setFormData({ ...formData, payment_method: event.target.value })}><option value="card">Card</option><option value="upi">UPI</option><option value="netbanking">Net banking</option><option value="wallet">Wallet</option></select><ChevronDown size={14} /></div></label>
          <label className="wide-field"><span>Delivery evidence <em>optional · confirmed status plus tracking/signature details</em></span><textarea rows={3} value={formData.delivery_proof} onChange={(event) => setFormData({ ...formData, delivery_proof: event.target.value })} placeholder="Carrier confirmed delivered 2026-09-10; tracking ID DHL123456; signed by recipient…" /></label>
        </div>
        <div className="verification-row">{[{ key: "avs_match", label: "AVS matched" }, { key: "cvv_verified", label: "CVV verified" }, { key: "three_ds_authenticated", label: "3DS authenticated" }].map(({ key, label }) => <label className="check-control" key={key}><input type="checkbox" checked={Boolean(formData[key as keyof typeof formData])} onChange={(event) => setFormData({ ...formData, [key]: event.target.checked })} /><span><Check size={12} /></span>{label}</label>)}<button className="primary-button launch" onClick={handleCreateAndStart} disabled={loading}>{loading ? <RefreshCw className="spin" size={16} /> : <Fingerprint size={16} />}{loading ? "Processing case" : "Create & Start Pipeline"}<ArrowRight size={16} /></button></div>
      </section>}

      <section className="workspace-grid">
        <aside className="case-rail"><div className="section-heading rail-heading"><div><span className="section-index">02</span><h2>Case queue</h2></div><span className="count-chip">{filteredSessions.length}</span></div>
          <div className="queue-filters">{([
            ["all", "All"],
            ["github", "GitHub imported"],
            ["review", "Needs human review"],
            ["ready", "Ready for analysis"],
            ["outcome", "Awaiting outcome"],
          ] as const).map(([id, label]) => <button key={id} className={queueFilter === id ? "active" : ""} onClick={() => setQueueFilter(id)}>{label}</button>)}</div>
          <div className="search-control"><Search size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search case, status, reason…" /></div><div className="session-list">{filteredSessions.length === 0 && <div className="empty-state"><Layers3 size={24} /><strong>No cases found</strong><span>Sync GitHub cases to begin analysis.</span></div>}{filteredSessions.map((session) => <button key={session.session_id} className={`session-row ${activeSession === session.session_id ? "active" : ""}`} onClick={() => viewCase(session.session_id)}><div className="session-row-top"><strong>{formatMoney(session.amount, session.currency)}</strong><span className={`status-tag status-${session.status}`}>{titleCase(session.status)}</span></div><div className="session-row-meta"><span>{titleCase(session.reason_code)}</span><span>{session.case_id.slice(0, 8)}</span></div><div className="session-row-foot"><span>{session.pipeline_status ? titleCase(session.pipeline_status) : "Created"}</span>{session.source_type === "github" ? <em className="source-badge">GitHub imported</em> : null}<ArrowRight size={13} /></div></button>)}</div></aside>

        <section className="analysis-space">
          {!activeSession && <div className="analysis-empty panel-glow"><TerminalSquare size={34} /><span>ANALYSIS CHANNEL IDLE</span><h2>Select a case to inspect the decision record.</h2><p>Case strategy, evidence quality, workflow telemetry, and audit-safe actions appear here.</p>{githubStatus?.allow_local_create ? <button className="secondary-button" onClick={() => setShowComposer(true)}><Plus size={15} /> Open a new case</button> : <button className="secondary-button" onClick={syncGithub}><RefreshCw size={15} /> Sync GitHub cases</button>}</div>}
          {activeSession && <>
            {pipelineInFlight && <section className="processing-panel" role="status" aria-live="polite"><div className="processing-constellation" aria-hidden="true"><i /><i /><i /><b /></div><div><span className="section-label">Analysis in progress</span><h3>{currentStage === "intake" ? "Securing the case record" : currentStage === "evidence_assembly" ? "Organizing the supplied evidence" : currentStage === "strategy_formulation" ? "Testing the evidence pattern" : currentStage === "response_drafting" ? "Preparing the response" : currentStage === "filing" ? "Preparing the simulated filing" : "Updating the case record"}</h3><p>This uses actual pipeline events. You can keep this page open while the record updates.</p></div></section>}
            <div className="case-header"><div><div className="eyebrow">CASE / {currentCase.case_id?.slice(0, 8) ?? activeSession.slice(0, 8)}</div><h2>{titleCase(currentCase.reason_code || "Dispute analysis")}</h2><p>{currentCase.intake_summary || "Case created. Awaiting workflow telemetry."}</p>{currentCase.source?.source_type === "github" ? <span className="source-badge">GitHub imported</span> : null}</div><div className="case-header-meta"><span>Exposure</span><strong>{formatMoney(currentCase.dispute_amount, currentCase.dispute_currency)}</strong><span className={`risk risk-${currentCase.risk_level}`}>{titleCase(currentCase.risk_level || "Unclassified")} risk</span></div></div>
            {currentCase.source?.source_type === "github" && <section className="source-details"><div className="section-label"><Database size={14} /> Source details</div><div className="github-meta-grid compact"><div><span>Repository</span><strong>{currentCase.source.source_repository || "—"}</strong></div><div><span>Commit SHA</span><strong>{currentCase.source.source_commit_sha || "—"}</strong></div><div><span>Source file</span><strong>{currentCase.source.source_file_path || "—"}</strong></div><div><span>Blob SHA</span><strong>{currentCase.source.source_blob_sha || "—"}</strong></div><div><span>Artifact count</span><strong>{currentCase.source.artifact_count ?? 0}</strong></div></div></section>}
            <div className="truth-bar" role="note"><span><span className="status-light amber" /> Filing simulated</span><span>{modelStatus?.trained_from_real_outcomes ? "Model trained on recorded outcomes" : "Model trained on synthetic bootstrap data"}</span><span>Evidence limited to supplied facts</span><span className={socketReady ? "connected" : "muted"}>{socketReady ? "Live channel connected" : "Telemetry reconnecting"}</span></div>
            <div className="timeline-panel"><div className="section-label"><Clock3 size={14} /> Resolution timeline</div><div className="stage-timeline">{visibleStages.map((stage, index) => { const active = currentStage === stage; const completed = completedStages.has(stage) && !active; return <div key={stage} className={`stage-node ${active ? "active" : ""} ${completed ? "complete" : ""}`}><span>{completed ? <Check size={11} /> : index + 1}</span><small>{titleCase(stage)}</small></div>; })}</div></div>
            {automaticApproval && <div className="default-approval-note"><Check size={15} /> Default approval applied for {titleCase(automaticApproval.origin || "review")}. The workflow continued automatically; the decision record remains auditable.</div>}
            {caseDetail?.status === "created" && activeSession && <div className="review-actions" style={{ marginBottom: 16 }}><button className="primary-button" onClick={() => startPipeline(activeSession)}>Start analysis</button></div>}
            {(needsReview || currentStage === "human_review") && <div className="review-panel"><div className="review-icon"><UserCheck size={22} /></div><div className="review-content"><div className="eyebrow warning">Decision checkpoint</div><h3>Human approval required</h3><p>{reviewCopy}</p><div className="review-actions"><button className="approve-button" disabled={loading} onClick={() => submitDecision("approve")}><Check size={15} /> Approve next stage</button><button className="secondary-button" disabled={loading || reviewOrigin === "filing"} onClick={() => submitDecision("modify")}><RefreshCw size={14} /> Re-analyze</button><button className="danger-button" disabled={loading} onClick={() => submitDecision("reject")}><X size={14} /> Reject case</button></div></div></div>}
            {awaitingOutcome && <div className="outcome-panel"><div><div className="eyebrow warning">Simulated filing</div><h3>Record the observed outcome</h3><p>This updates training feedback; it does not contact a processor.</p></div><div className="review-actions"><button className="approve-button" onClick={() => submitOutcome("won")}>Won</button><button className="danger-button" onClick={() => submitOutcome("lost")}>Lost</button><button className="secondary-button" onClick={() => submitOutcome("withdrawn")}>Withdrawn</button></div></div>}

            <div className="analysis-columns"><div className="analysis-main">
              <section className="intel-section"><div className="section-heading"><div><span className="section-index">03</span><h2>Evidence intelligence</h2><p>{evidence.length} supplied records assessed</p></div>{hasWarnings && <span className="warning-chip"><AlertTriangle size={13} /> Review gaps</span>}</div>{currentCase.evidence_gaps?.length ? <div className="gap-list">{currentCase.evidence_gaps.map((gap) => <div key={gap}><AlertTriangle size={14} /><span>{gap}</span></div>)}</div> : null}<div className="evidence-list">{evidence.length === 0 && <div className="empty-inline">No evidence records available.</div>}{evidence.map((item) => <article className="evidence-row" key={item.evidence_id}><div className="evidence-symbol"><FileCheck2 size={16} /></div><div className="evidence-copy"><div><strong>{item.title}</strong><span>{titleCase(item.evidence_type)}</span></div><p>{item.content}</p><small>{titleCase(item.source)} · ID {item.evidence_id.slice(0, 8)}</small></div><div className="score"><b>{Math.round((item.relevance_score || 0) * 100)}</b><span>relevance</span></div></article>)}</div></section>
              {currentCase.response && <section className="intel-section response-section"><div className="section-heading"><div><span className="section-index">04</span><h2>Draft response</h2><p>{currentCase.response.word_count ?? "—"} words · supplied evidence only</p></div></div>{draftWithheld ? <div className="gap-list"><div><AlertTriangle size={14} /><span>Legacy draft withheld because it exposes internal model metrics. Re-run the case to generate a filing-safe response.</span></div></div> : <div className="document-preview"><div className="document-rule" /><p>{currentCase.response.rebuttal_letter}</p></div>}</section>}
            </div><aside className="decision-column">
              <section className="decision-card panel-glow"><div className="section-label"><Gauge size={14} /> XGBoost decision support</div><div className="probability"><strong>{percent(currentCase.strategy?.win_probability)}</strong><span>estimated chance the supplied record supports a contest</span></div><div className="confidence-track"><div style={{ width: percent(currentCase.strategy?.confidence) }} /><span>Distance from an uncertain 50/50 estimate: {percent(currentCase.strategy?.confidence)}</span></div><div className="decision-badges"><span>Suggested: {titleCase(currentCase.strategy?.recommended_action || "Pending")}</span><span>{titleCase(currentCase.strategy?.evidence_strength || "Unscored")} evidence</span></div><p>{currentCase.strategy?.reasoning || "The model has not completed its analysis yet."}</p><div className="model-interpretation"><strong>What this means</strong><p>{modelStatus?.available ? `${modelStatus.kind === "xgboost" ? "XGBoost" : "Fallback"} found a pattern in the supplied evidence. It helps prioritize the next step; it does not determine the dispute outcome.` : "The trained model is unavailable, so the workflow is using its conservative fallback."}</p>{modelStatus?.rows && <small>{modelStatus.trained_from_real_outcomes ? `${modelStatus.rows.toLocaleString()} recorded outcomes` : `${modelStatus.rows.toLocaleString()} synthetic training examples`} · simulator ROC-AUC {modelStatus.roc_auc?.toFixed(2) ?? "—"} · not real-world accuracy</small>}</div>{currentCase.strategy?.model_inputs?.length ? <div className="model-inputs"><strong>Inputs used for this estimate</strong><ul>{currentCase.strategy.model_inputs.map((input) => <li key={input}>{input}</li>)}</ul></div> : null}</section>
              <section className="telemetry-card"><div className="section-label"><Activity size={14} /> Agent telemetry</div><div className="trace-list">{(currentCase.agent_trace ?? []).map((trace, index) => <div key={`${trace.agent}-${index}`}><span className={`trace-dot trace-${trace.status}`} /><section><strong>{titleCase(trace.agent)}</strong><p title={compactText(trace.output_summary || trace.error, 1000)}>{compactText(trace.output_summary || trace.error || titleCase(trace.status))}</p></section></div>)}</div>{events.length > 0 && <div className="event-counter">{events.length} live events received</div>}</section>
              {currentCase.filing && <section className="filing-card"><div className="section-label"><FileCheck2 size={14} /> Filing record</div><div className="filing-status"><span>{titleCase(currentCase.filing.status)}</span><b>{currentCase.filing.simulated ? "SIMULATION" : "EXTERNAL"}</b></div><code>{currentCase.filing.confirmation_reference || "No reference"}</code><p>{currentCase.filing.notes}</p></section>}
              {currentCase.error_log?.length ? <section className="diagnostic-card"><div className="section-label"><AlertTriangle size={14} /> Diagnostics</div>{currentCase.error_log.map((entry, index) => <p key={index} title={compactText(entry, 1000)}>{compactText(entry, 420)}</p>)}</section> : null}
            </aside></div>
            <button className="raw-toggle" onClick={() => setShowRaw((value) => !value)}><TerminalSquare size={14} /> {showRaw ? "Hide" : "Show"} raw case record <ChevronDown className={showRaw ? "rotated" : ""} size={14} /></button>{showRaw && <pre className="raw-record">{JSON.stringify(currentCase, null, 2)}</pre>}
          </>}
        </section>
      </section>
    </main>
  </div>;
}

export default App;
