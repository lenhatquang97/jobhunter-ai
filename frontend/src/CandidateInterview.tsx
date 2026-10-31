import React from "react";
import { api } from "./api";

type SubmittedTurn = {
  id: string; question: string; answer: string; language: "en" | "vi"; submitted_at: string;
};
type Page = {
  open: boolean; job?: string; company?: string; state?: string;
  pending?: { question: string }; turns?: SubmittedTurn[]; audio_consent?: boolean;
};
type Turn = { question?: string; done?: boolean; error?: string; detail?: string; submitted_turn?: SubmittedTurn };

export function CandidateInterview({ token }: { token: string }) {
  const draftKey = `screening-draft:${token}`;
  const [page, setPage] = React.useState<Page | null>(null);
  const [question, setQuestion] = React.useState("");
  const [draft, setDraft] = React.useState(() => localStorage.getItem(draftKey) || "");
  const [answerLanguage, setAnswerLanguage] = React.useState<"en" | "vi">("en");
  const [submittedTurns, setSubmittedTurns] = React.useState<SubmittedTurn[]>([]);
  const [started, setStarted] = React.useState(false);
  const [done, setDone] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [audioConsent, setAudioConsent] = React.useState(false);
  const [voiceStatus, setVoiceStatus] = React.useState("Enable English voice");
  const [error, setError] = React.useState("");
  const peer = React.useRef<RTCPeerConnection | null>(null);
  const stream = React.useRef<MediaStream | null>(null);
  const dataChannel = React.useRef<RTCDataChannel | null>(null);

  const closeVoice = React.useCallback(() => {
    dataChannel.current?.close(); dataChannel.current = null;
    peer.current?.close(); peer.current = null;
    stream.current?.getTracks().forEach((track) => track.stop()); stream.current = null;
    setVoiceStatus("Enable English voice");
  }, []);

  React.useEffect(() => {
    void api<Page>(`/api/i/${token}`).then((result) => {
      setPage(result); setSubmittedTurns(result.turns || []); setAudioConsent(Boolean(result.audio_consent));
      if (result.state === "running" && result.pending?.question) {
        setStarted(true); setQuestion(result.pending.question);
      }
    }).catch((reason) => setError(String(reason)));
    return closeVoice;
  }, [token, closeVoice]);

  React.useEffect(() => {
    if (draft) localStorage.setItem(draftKey, draft); else localStorage.removeItem(draftKey);
  }, [draft, draftKey]);

  React.useEffect(() => {
    if (!question || !dataChannel.current || dataChannel.current.readyState !== "open") return;
    dataChannel.current.send(JSON.stringify({
      type: "response.create",
      response: { conversation: "none", input: [], output_modalities: ["audio"], instructions: `Read this English interview question exactly as written and add nothing: ${question}` },
    }));
  }, [question]);

  const start = async () => {
    setBusy(true); setError("");
    try {
      const result = await api<Turn>(`/api/i/${token}/start`, { method: "POST" });
      if (result.error) throw new Error(result.detail || result.error);
      setQuestion(result.question || ""); setDone(Boolean(result.done)); setStarted(true);
    } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  };

  const submit = async () => {
    if (!draft.trim()) return;
    setBusy(true); setError("");
    try {
      const result = await api<Turn>(`/api/i/${token}/answer`, {
        method: "POST", body: JSON.stringify({ text: draft, language: answerLanguage }),
      });
      if (result.error) throw new Error(result.detail || result.error);
      if (result.submitted_turn) setSubmittedTurns((current) => [...current, result.submitted_turn!]);
      setDraft(""); setQuestion(result.question || ""); setDone(Boolean(result.done));
      if (result.done) closeVoice();
    } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  };

  const changeConsent = async (consent: boolean) => {
    setAudioConsent(consent); setError("");
    if (!consent) closeVoice();
    try {
      await api(`/api/i/${token}/consent`, { method: "POST", body: JSON.stringify({ consent }) });
    } catch (reason) { setAudioConsent(!consent); setError(String(reason)); }
  };

  const enableVoice = async () => {
    if (!audioConsent) return;
    closeVoice(); setVoiceStatus("Connecting…"); setError("");
    try {
      await api(`/api/i/${token}/consent`, { method: "POST", body: JSON.stringify({ consent: true }) });
      stream.current = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      const voice = await api<{ secret: string; model: string }>(`/api/i/${token}/voice`, {
        method: "POST", body: JSON.stringify({ consent: true }),
      });
      const pc = new RTCPeerConnection(); peer.current = pc;
      const audio = new Audio(); audio.autoplay = true;
      pc.ontrack = (event) => { audio.srcObject = event.streams[0]; };
      pc.addTrack(stream.current.getTracks()[0]);
      const dc = pc.createDataChannel("oai-events"); dataChannel.current = dc;
      dc.onmessage = (event) => {
        const message = JSON.parse(event.data);
        if (message.type === "conversation.item.input_audio_transcription.completed") {
          setDraft((current) => `${current} ${message.transcript || ""}`.trim());
          setAnswerLanguage("en"); setVoiceStatus("Transcript draft ready — review before submitting");
        } else if (message.type === "error") {
          setError(`Voice provider error: ${message.error?.message || "unknown error"}`);
        }
      };
      await pc.setLocalDescription(await pc.createOffer());
      const sdp = await fetch(`https://api.openai.com/v1/realtime/calls?model=${encodeURIComponent(voice.model)}`, {
        method: "POST", body: pc.localDescription!.sdp,
        headers: { Authorization: `Bearer ${voice.secret}`, "Content-Type": "application/sdp" },
      });
      if (!sdp.ok) throw new Error("Voice provider connection failed");
      await pc.setRemoteDescription({ type: "answer", sdp: await sdp.text() });
      await new Promise<void>((resolve) => dc.readyState === "open" ? resolve() : dc.addEventListener("open", () => resolve(), { once: true }));
      dc.send(JSON.stringify({ type: "response.create", response: { conversation: "none", input: [], output_modalities: ["audio"], instructions: `Read this English interview question exactly as written and add nothing: ${question}` } }));
      setVoiceStatus("Listening — transcript stays a draft until Submit");
    } catch (reason) {
      closeVoice(); setError(String(reason));
      await api(`/api/i/${token}/consent`, { method: "POST", body: JSON.stringify({ consent: false }) }).catch(() => undefined);
      setAudioConsent(false);
    }
  };

  const cancel = async () => {
    await api(`/api/i/${token}/cancel`, { method: "POST" });
    closeVoice(); setDone(true);
  };

  if (!page) return <main className="candidate-shell"><p>{error || "Loading…"}</p></main>;
  if (!page.open && !done) return <main className="candidate-shell"><section className="candidate-card"><h1>This interview link is closed</h1></section></main>;

  return <main className="candidate-shell">
    <section className="candidate-card brand"><p className="eyebrow">AI screening interview</p><h1>{page.job}</h1><p className="muted">{page.company}</p></section>
    {!started ? <section className="candidate-card">
      <h2>Before you begin</h2>
      <p>You can answer in English or Vietnamese by typing. Voice is optional and English-only. Every transcript remains an editable draft until you press Submit answer.</p>
      <button className="primary" onClick={() => void start()} disabled={busy}>Start screening</button>
    </section> : <section className="candidate-card interview-stage">
      {done ? <><span className="orb done" /><h2>Thank you — the interview is complete.</h2></> : <>
        <span className="orb" /><p className="eyebrow">Current question</p><h2>{question}</h2>
        <label className="consent"><input type="checkbox" checked={audioConsent} onChange={(event) => void changeConsent(event.target.checked)} /> I consent to audio capture and AI speech processing for this interview.</label>
        <button onClick={() => void enableVoice()} disabled={!audioConsent || busy}>{voiceStatus}</button>
        <label>Answer language<select value={answerLanguage} onChange={(event) => setAnswerLanguage(event.target.value as "en" | "vi")}><option value="en">English</option><option value="vi">Tiếng Việt</option></select></label>
        <label>Your answer draft<textarea value={draft} onChange={(event) => setDraft(event.target.value)} rows={7} placeholder="Type in English or Vietnamese, or enable English voice transcription." /></label>
        <p className="muted">Review and edit this draft. It is not evidence and will not advance the interview until you submit it.</p>
        <div className="actions"><button className="primary" onClick={() => void submit()} disabled={busy || !draft.trim()}>Submit answer</button><button className="danger" onClick={() => void cancel()}>End interview</button></div>
      </>}
      {error && <p className="error" role="alert">{error}</p>}
    </section>}
    {submittedTurns.length > 0 && <section className="candidate-card submitted-answers">
      <p className="eyebrow">Submitted answers</p>
      {submittedTurns.map((turn) => <article key={turn.id}><small>{turn.id} · {turn.language.toUpperCase()}</small><strong>{turn.question}</strong><p>{turn.answer}</p></article>)}
    </section>}
  </main>;
}
