import React from "react";
import { api, useRecruiterToken } from "./api";

type Speaker = "interviewer" | "candidate" | "unassigned";
type Turn = {
  id: string; order: number; providerSpeaker: string; speaker: Speaker; text: string;
  translated_text?: string; language: string; final: boolean;
};
type Suggestion = { question: string; reason: string; based_on_turn_ids: string[] };
type Feedback = {
  summary: string; strengths: string[]; gaps: string[];
  findings: { criterion_id: string; status: string; statement: string; evidence_turn_ids: string[] }[];
};

const emptyFeedback: Feedback = { summary: "", strengths: [], gaps: [], findings: [] };

export function HumanCopilot() {
  const [token, setToken] = useRecruiterToken();
  const [fixture, setFixture] = React.useState("j1_strong");
  const [criteriaText, setCriteriaText] = React.useState("");
  const [turns, setTurns] = React.useState<Turn[]>([]);
  const turnsRef = React.useRef<Turn[]>([]);
  const turnMapRef = React.useRef<Map<number, Turn>>(new Map());
  const [mapping, setMapping] = React.useState<Record<string, Speaker>>({ A: "interviewer", B: "candidate" });
  const mappingRef = React.useRef<Record<string, Speaker>>({ A: "interviewer", B: "candidate" });
  const [suggestions, setSuggestions] = React.useState<Suggestion[]>([]);
  const [feedback, setFeedback] = React.useState<Feedback>(emptyFeedback);
  const [status, setStatus] = React.useState("Connect AssemblyAI to begin");
  const [preparing, setPreparing] = React.useState(false);
  const [streamReady, setStreamReady] = React.useState(false);
  const [playing, setPlaying] = React.useState(false);
  const [recordingFinished, setRecordingFinished] = React.useState(false);
  const [pendingTranslations, setPendingTranslations] = React.useState(0);
  const [showVietnamese, setShowVietnamese] = React.useState(true);
  const [elapsed, setElapsed] = React.useState(0);
  const [duration, setDuration] = React.useState(0);
  const [error, setError] = React.useState("");
  const audioRef = React.useRef<HTMLAudioElement | null>(null);
  const socketRef = React.useRef<WebSocket | null>(null);
  const audioContextRef = React.useRef<AudioContext | null>(null);
  const sourceRef = React.useRef<MediaElementAudioSourceNode | null>(null);
  const processorRef = React.useRef<ScriptProcessorNode | null>(null);
  const completedCandidateRef = React.useRef<Set<string>>(new Set());
  const translationRequestedRef = React.useRef<Set<string>>(new Set());
  const followUpRequestRef = React.useRef(0);
  const conversationFeedRef = React.useRef<HTMLDivElement | null>(null);
  const followUpRef = React.useRef<HTMLDivElement | null>(null);

  const orderedTurns = () => [...turnMapRef.current.values()].sort((a, b) => a.order - b.order);
  const renderTurns = () => {
    const current = orderedTurns();
    turnsRef.current = current;
    setTurns(current);
  };

  React.useEffect(() => {
    const feed = conversationFeedRef.current;
    if (feed) feed.scrollTop = feed.scrollHeight;
  }, [turns]);
  React.useEffect(() => {
    if (suggestions.length) followUpRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [suggestions]);
  React.useEffect(() => {
    fetch("/api/copilot/demo/rubric_j1.json")
      .then((response) => response.json())
      .then((rubric: { name: string; description: string }[]) => {
        setCriteriaText(rubric.map((item) => `${item.name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}: ${item.description}`).join("\n"));
      })
      .catch(() => setError("Could not load the interview rubric."));
    return () => {
      const socket = socketRef.current;
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "Terminate" }));
      socket?.close();
      void audioContextRef.current?.close();
    };
  }, []);

  const criteria = () => criteriaText.split("\n").map((line) => {
    const [id, ...description] = line.split(":");
    return { id: id.trim(), description: description.join(":").trim() };
  }).filter((item) => item.id && item.description);

  const requestBody = (current = turnsRef.current) => ({
    context: {
      job_title: "Backend Engineer (Python)",
      job_context: "Synthetic human-led technical interview demo",
      criteria: criteria(),
    },
    transcript: current.filter((turn) => turn.final && turn.speaker !== "unassigned").map((turn) => ({
      id: turn.id, speaker: turn.speaker, text: turn.text,
      language: turn.language, translated_text: turn.translated_text,
    })),
  });

  const refreshSuggestions = async (current: Turn[]) => {
    const last = [...current].reverse().find((turn) => turn.final && turn.speaker !== "unassigned");
    if (!last || last.speaker !== "candidate") return;
    const requestId = ++followUpRequestRef.current;
    try {
      const result = await api<{ questions: Suggestion[] }>("/api/copilot/follow-ups", {
        recruiterToken: token, method: "POST", body: JSON.stringify(requestBody(current)),
      });
      if (requestId === followUpRequestRef.current) setSuggestions(result.questions);
    } catch (reason) { setError(`Follow-up failed: ${String(reason)}`); }
  };

  const translateFinalTurn = async (turn: Turn) => {
    if (translationRequestedRef.current.has(turn.id)) return;
    translationRequestedRef.current.add(turn.id);
    setPendingTranslations((count) => count + 1);
    try {
      const result = await api<{ translation: string }>("/api/copilot/translate", {
        recruiterToken: token,
        method: "POST",
        body: JSON.stringify({ text: turn.text, source_language: "en", target_language: "vi" }),
      });
      const latest = turnMapRef.current.get(turn.order);
      if (latest?.final && latest.text === turn.text) {
        turnMapRef.current.set(turn.order, { ...latest, translated_text: result.translation });
        renderTurns();
      }
    } catch (reason) { setError(`AssemblyAI translation failed: ${String(reason)}`); }
    finally { setPendingTranslations((count) => Math.max(0, count - 1)); }
  };

  const handleFinalTurn = (turn: Turn) => {
    void translateFinalTurn(turn);
    const current = orderedTurns().filter((item) => item.final);
    if (turn.speaker === "candidate" && !completedCandidateRef.current.has(turn.id)) {
      completedCandidateRef.current.add(turn.id);
      void refreshSuggestions(current);
    }
  };

  const handleAssemblyMessage = (event: MessageEvent<string>) => {
    const message = JSON.parse(event.data) as {
      type: string; turn_order?: number; speaker_label?: string; transcript?: string; end_of_turn?: boolean;
      revisions?: { turn_order: number; speaker_label: string }[];
    };
    if (message.type === "Turn" && message.turn_order !== undefined && message.transcript) {
      const order = Number(message.turn_order);
      const existing = turnMapRef.current.get(order);
      const providerSpeaker = message.speaker_label || existing?.providerSpeaker || "?";
      const final = Boolean(message.end_of_turn);
      const next: Turn = {
        id: `turn-${order}`,
        order,
        providerSpeaker,
        speaker: mappingRef.current[providerSpeaker] || "unassigned",
        text: message.transcript,
        translated_text: existing?.text === message.transcript ? existing.translated_text : undefined,
        language: "en",
        final,
      };
      turnMapRef.current.set(order, next);
      renderTurns();
      if (final && !existing?.final) handleFinalTurn(next);
    } else if (message.type === "SpeakerRevision") {
      for (const revision of message.revisions || []) {
        const existing = turnMapRef.current.get(Number(revision.turn_order));
        if (existing) turnMapRef.current.set(existing.order, {
          ...existing,
          providerSpeaker: revision.speaker_label,
          speaker: mappingRef.current[revision.speaker_label] || "unassigned",
        });
      }
      renderTurns();
      const latestFinalCandidate = [...orderedTurns()].reverse().find((turn) =>
        turn.final && turn.speaker === "candidate" && !completedCandidateRef.current.has(turn.id));
      if (latestFinalCandidate) {
        completedCandidateRef.current.add(latestFinalCandidate.id);
        void refreshSuggestions(orderedTurns().filter((turn) => turn.final));
      }
    }
  };

  const prepareRealtime = async () => {
    if (!token) { setError("Enter the recruiter token first."); return; }
    audioRef.current?.pause();
    if (audioRef.current) audioRef.current.currentTime = 0;
    socketRef.current?.close();
    turnMapRef.current.clear(); turnsRef.current = []; setTurns([]);
    completedCandidateRef.current.clear(); translationRequestedRef.current.clear();
    followUpRequestRef.current += 1;
    setSuggestions([]); setFeedback(emptyFeedback); setElapsed(0); setRecordingFinished(false); setPendingTranslations(0);
    setStreamReady(false); setPreparing(true); setError(""); setStatus("Connecting to AssemblyAI streaming…");
    try {
      const { token: streamingToken } = await api<{ token: string }>("/api/copilot/assemblyai-token", {
        recruiterToken: token,
      });
      const params = new URLSearchParams({
        sample_rate: "16000", encoding: "pcm_s16le", speaker_labels: "true", max_speakers: "2", token: streamingToken,
      });
      const socket = new WebSocket(`wss://streaming.assemblyai.com/v3/ws?${params}`);
      socketRef.current = socket;
      socket.onmessage = handleAssemblyMessage;
      socket.onerror = () => setError("AssemblyAI streaming connection failed.");
      socket.onclose = (event) => {
        if (event.code !== 1000 && !recordingFinished) setError(`AssemblyAI disconnected (${event.code}): ${event.reason}`);
      };
      await new Promise<void>((resolve, reject) => {
        const timeout = window.setTimeout(() => reject(new Error("AssemblyAI connection timed out")), 15_000);
        socket.onopen = () => { window.clearTimeout(timeout); resolve(); };
      });
      setStreamReady(true); setStatus("AssemblyAI live stream ready — press Start interview");
    } catch (reason) {
      socketRef.current?.close(); setError(String(reason)); setStatus("Streaming preparation failed");
    } finally { setPreparing(false); }
  };

  const ensureAudioGraph = () => {
    const audio = audioRef.current;
    if (!audio) throw new Error("Demo audio is unavailable");
    let context = audioContextRef.current;
    if (!context) {
      context = new AudioContext({ sampleRate: 16_000 });
      audioContextRef.current = context;
      const source = context.createMediaElementSource(audio);
      const processor = context.createScriptProcessor(4096, 1, 1);
      const silentOutput = context.createGain();
      silentOutput.gain.value = 0;
      source.connect(context.destination);
      source.connect(processor);
      processor.connect(silentOutput);
      silentOutput.connect(context.destination);
      processor.onaudioprocess = (event) => {
        const socket = socketRef.current;
        if (socket?.readyState !== WebSocket.OPEN || audio.paused) return;
        const samples = event.inputBuffer.getChannelData(0);
        const pcm = new Int16Array(samples.length);
        for (let index = 0; index < samples.length; index += 1) {
          pcm[index] = Math.max(-1, Math.min(1, samples[index])) * 0x7fff;
        }
        socket.send(pcm.buffer);
      };
      sourceRef.current = source;
      processorRef.current = processor;
    }
    void context.resume();
  };

  const startPlayback = () => {
    const audio = audioRef.current;
    if (!audio || !streamReady || socketRef.current?.readyState !== WebSocket.OPEN) return;
    setError(""); setRecordingFinished(false);
    try {
      ensureAudioGraph();
      void audio.play().then(() => {
        setPlaying(true); setStatus("● AssemblyAI live transcription");
      }).catch((reason) => setError(`Audio could not start: ${String(reason)}`));
    } catch (reason) { setError(String(reason)); }
  };

  const finishRecording = () => {
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "Terminate" }));
    setPlaying(false); setStreamReady(false); setRecordingFinished(true);
    setStatus("Conversation finished — waiting for final translation, then create feedback");
  };

  const applyMapping = (label: string, speaker: Speaker) => {
    const next = { ...mappingRef.current, [label]: speaker };
    mappingRef.current = next; setMapping(next);
    for (const [order, turn] of turnMapRef.current) {
      if (turn.providerSpeaker === label) turnMapRef.current.set(order, { ...turn, speaker });
    }
    renderTurns();
  };

  const endInterview = async () => {
    setStatus("Creating evidence-based feedback…");
    try {
      const result = await api<Feedback>("/api/copilot/feedback", {
        recruiterToken: token, method: "POST", body: JSON.stringify(requestBody()),
      });
      setFeedback(result); setStatus("Interview complete — human review required");
    } catch (reason) { setError(`Feedback failed: ${String(reason)}`); setStatus("Feedback failed"); }
  };

  const currentSpeaker = [...turns].reverse().find((turn) => !turn.final)?.speaker;
  const progress = duration ? Math.min(100, (elapsed / duration) * 100) : 0;
  const time = (seconds: number) => `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;

  return <div className="interview-app">
    <header className="interview-header">
      <a href="/" className="interview-brand"><span>J</span> Jobhunter AI</a>
      <div className="interview-controls">
        <input aria-label="Recruiter token" type="password" value={token} onChange={(event) => setToken(event.target.value)} placeholder="Recruiter token" />
        <select aria-label="Demo recording" value={fixture} onChange={(event) => setFixture(event.target.value)} disabled={playing || preparing || streamReady}>
          <option value="j1_strong">Strong candidate</option><option value="j1_weak">Weak candidate</option>
        </select>
        <button onClick={() => void prepareRealtime()} disabled={preparing || playing}>{preparing ? "Connecting…" : "Connect AssemblyAI"}</button>
        <button className="primary" onClick={startPlayback} disabled={!streamReady || playing}>Start interview</button>
      </div>
    </header>

    <main className="interview-layout">
      <section className="conversation-stage">
        <div className="conversation-topline">
          <div><p className="eyebrow">Human-led interview</p><h1>Backend Engineer Interview</h1></div>
          <div className="language-toggle" aria-label="Transcript language">
            <button className={!showVietnamese ? "selected" : ""} onClick={() => setShowVietnamese(false)}>English</button>
            <button className={showVietnamese ? "selected" : ""} onClick={() => setShowVietnamese(true)}>English + Vietnamese</button>
          </div>
        </div>
        <div className="interview-progress"><span style={{ width: `${progress}%` }} /></div>
        <div className="interview-meta"><span>{status}</span><span>{time(elapsed)} / {time(duration)}</span></div>

        <div className="conversation-feed" aria-live="polite" ref={conversationFeedRef}>
          {!turns.length && <div className="empty-conversation"><div className="empty-orb">AI</div><h2>Connect AssemblyAI, then press Start</h2><p>The hidden WAV is streamed as live 16 kHz audio. Partial transcripts come directly from AssemblyAI; final turns are translated to Vietnamese.</p></div>}
          {turns.map((turn) => <article className={`conversation-turn ${turn.speaker} ${turn.final ? "final" : "live-turn"}`} key={turn.id}>
            <div className="turn-label">{turn.speaker === "interviewer" ? "Interviewer · Ezra" : turn.speaker === "candidate" ? "Candidate · You" : `Speaker ${turn.providerSpeaker}`}</div>
            <p>{turn.text}</p>
            {showVietnamese && turn.final && !turn.translated_text && <p className="translation-pending">AssemblyAI translating…</p>}
            {showVietnamese && turn.translated_text && <p className="translated-line">{turn.translated_text}</p>}
          </article>)}
        </div>

        <audio className="background-audio" ref={audioRef} src={`/api/copilot/demo/${fixture}.wav`} preload="metadata"
          onLoadedMetadata={(event) => setDuration(event.currentTarget.duration)}
          onTimeUpdate={(event) => setElapsed(event.currentTarget.currentTime)}
          onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={finishRecording} />
        {recordingFinished && !feedback.summary && <div className="audio-dock completed">
          <button className="end-interview" onClick={() => void endInterview()} disabled={pendingTranslations > 0}>
            {pendingTranslations > 0 ? `Waiting for ${pendingTranslations} translation${pendingTranslations === 1 ? "" : "s"}…` : "End interview & create feedback"}
          </button>
        </div>}

        {error && <p className="interview-error" role="alert">{error}</p>}
        {feedback.summary && <section className="feedback-sheet">
          <p className="eyebrow">AI feedback · human review required</p><h2>Interview evidence</h2><p>{feedback.summary}</p>
          <div className="finding-grid">{feedback.findings.map((finding) => <article key={finding.criterion_id}><span className={`status ${finding.status}`}>{finding.status}</span><h3>{finding.criterion_id}</h3><p>{finding.statement}</p><small>{finding.evidence_turn_ids.join(", ") || "No candidate evidence"}</small></article>)}</div>
        </section>}
      </section>

      <aside className="participant-stage">
        <div className="speaking-pill"><i className={playing ? "live" : ""} />{currentSpeaker === "candidate" ? "Candidate is speaking" : currentSpeaker === "interviewer" ? "Ezra is speaking" : playing ? "AssemblyAI is listening" : "Ready"}</div>
        <Participant name="Ezra" role="Interviewer" active={currentSpeaker === "interviewer" && playing} color="blue" />
        <Participant name="You" role="Candidate" active={currentSpeaker === "candidate" && playing} color="lime" />
        <div className="speaker-settings">{Object.keys(mapping).map((label) => <label key={label}>Speaker {label}<select value={mapping[label]} onChange={(event) => applyMapping(label, event.target.value as Speaker)}><option value="interviewer">Interviewer</option><option value="candidate">Candidate</option><option value="unassigned">Unassigned</option></select></label>)}</div>
        <div className="follow-up-card" ref={followUpRef}>
          <p className="eyebrow">Private AI suggestion</p>
          {suggestions[0] ? <><h3>{suggestions[0].question}</h3><p>{suggestions[0].reason}</p></> : <p>Follow-up guidance appears after AssemblyAI finalizes a candidate answer.</p>}
          {suggestions.length > 1 && <details><summary>{suggestions.length - 1} more suggestions</summary><ul>{suggestions.slice(1).map((item) => <li key={item.question}>{item.question}</li>)}</ul></details>}
        </div>
      </aside>
    </main>
  </div>;
}

function Participant({ name, role, active, color }: { name: string; role: string; active: boolean; color: "blue" | "lime" }) {
  return <section className={`participant ${color} ${active ? "active" : ""}`}>
    <div><p>{role}</p><h2>{name}</h2></div>
    <div className="sound-wave" aria-hidden="true">{[0, 1, 2].map((line) => <span key={line} />)}</div>
  </section>;
}
