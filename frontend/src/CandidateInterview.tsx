import React from "react";
import { api } from "./api";

type Page = { open: boolean; job?: string; company?: string };
type Turn = { question?: string; done?: boolean; error?: string; detail?: string };

export function CandidateInterview({ token }: { token: string }) {
  const [page, setPage] = React.useState<Page | null>(null);
  const [question, setQuestion] = React.useState("");
  const [draft, setDraft] = React.useState("");
  const [started, setStarted] = React.useState(false);
  const [done, setDone] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [audioConsent, setAudioConsent] = React.useState(false);
  const [voiceStatus, setVoiceStatus] = React.useState("Voice is off");
  const [error, setError] = React.useState("");
  const peer = React.useRef<RTCPeerConnection | null>(null);
  const stream = React.useRef<MediaStream | null>(null);
  const dataChannel = React.useRef<RTCDataChannel | null>(null);

  React.useEffect(() => {
    void api<Page>(`/api/i/${token}`).then(setPage).catch((err) => setError(String(err)));
    return () => { peer.current?.close(); stream.current?.getTracks().forEach((track) => track.stop()); };
  }, [token]);

  React.useEffect(() => {
    if (!question || !dataChannel.current || dataChannel.current.readyState !== "open") return;
    dataChannel.current.send(JSON.stringify({
      type: "response.create",
      response: { conversation: "none", input: [], output_modalities: ["audio"], instructions: `Read this interview question exactly as written and add nothing: ${question}` },
    }));
  }, [question]);

  const start = async () => {
    setBusy(true); setError("");
    try {
      const result = await api<Turn>(`/api/i/${token}/start`, { method: "POST" });
      if (result.error) throw new Error(result.detail || result.error);
      setQuestion(result.question || ""); setDone(Boolean(result.done)); setStarted(true);
    } catch (err) { setError(String(err)); } finally { setBusy(false); }
  };

  const submit = async () => {
    if (!draft.trim()) return;
    setBusy(true); setError("");
    try {
      const result = await api<Turn>(`/api/i/${token}/answer`, { method: "POST", body: JSON.stringify({ text: draft }) });
      if (result.error) throw new Error(result.detail || result.error);
      setDraft(""); setQuestion(result.question || ""); setDone(Boolean(result.done));
    } catch (err) { setError(String(err)); } finally { setBusy(false); }
  };

  const enableVoice = async () => {
    if (!audioConsent) return;
    setVoiceStatus("Connecting…"); setError("");
    try {
      stream.current = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
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
          setVoiceStatus("Transcript draft ready — review before submitting");
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
      dc.send(JSON.stringify({ type: "response.create", response: { conversation: "none", input: [], output_modalities: ["audio"], instructions: `Read this interview question exactly as written and add nothing: ${question}` } }));
      setVoiceStatus("Listening — pause when your answer is complete");
    } catch (err) {
      stream.current?.getTracks().forEach((track) => track.stop());
      setVoiceStatus("Voice is off"); setError(String(err));
    }
  };

  const cancel = async () => {
    await api(`/api/i/${token}/cancel`, { method: "POST" });
    peer.current?.close(); stream.current?.getTracks().forEach((track) => track.stop()); setDone(true);
  };

  if (!page) return <main className="candidate-shell"><p>{error || "Loading…"}</p></main>;
  if (!page.open) return <main className="candidate-shell"><section className="candidate-card"><h1>This interview link is closed</h1></section></main>;

  return <main className="candidate-shell">
    <section className="candidate-card brand"><p className="eyebrow">AI screening interview</p><h1>{page.job}</h1><p className="muted">{page.company}</p></section>
    {!started ? <section className="candidate-card">
      <h2>Before you begin</h2>
      <p>You can answer by typing without sharing audio. If you enable voice, audio is sent to the configured speech provider. Every transcript remains an editable draft until you press Submit answer.</p>
      <button className="primary" onClick={() => void start()} disabled={busy}>Start with typed answers</button>
    </section> : <section className="candidate-card interview-stage">
      {done ? <><span className="orb done" /><h2>Thank you — the interview is complete.</h2></> : <>
        <span className="orb" /><p className="eyebrow">Current question</p><h2>{question}</h2>
        <label className="consent"><input type="checkbox" checked={audioConsent} onChange={(e) => setAudioConsent(e.target.checked)} /> I consent to audio capture and AI speech processing for this interview.</label>
        <button onClick={() => void enableVoice()} disabled={!audioConsent}>{voiceStatus}</button>
        <label>Your answer draft<textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={7} placeholder="Type in English or Vietnamese, or enable voice for English transcription." /></label>
        <p className="muted">Review and edit this draft. It is not an answer until you submit it.</p>
        <div className="actions"><button className="primary" onClick={() => void submit()} disabled={busy || !draft.trim()}>Submit answer</button><button className="danger" onClick={() => void cancel()}>End interview</button></div>
      </>}
      {error && <p className="error" role="alert">{error}</p>}
    </section>}
  </main>;
}
