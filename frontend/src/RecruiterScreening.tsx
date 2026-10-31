import React from "react";
import { api, useRecruiterToken } from "./api";

type Session = {
  id: number; candidate: string; job: string; company: string; state: string;
  created_at: string; url: string; error?: string; report?: Record<string, unknown>;
};

export function RecruiterScreening() {
  const [token, setToken] = useRecruiterToken();
  const [sessions, setSessions] = React.useState<Session[]>([]);
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    if (!token) return;
    try { setSessions(await api<Session[]>("/api/interviews", { recruiterToken: token })); setError(""); }
    catch (err) { setError(String(err)); }
  }, [token]);

  React.useEffect(() => { void load(); }, [load]);

  const create = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const form = new FormData(event.currentTarget);
      await api<Session>("/api/interviews", { method: "POST", body: form, recruiterToken: token });
      event.currentTarget.reset(); await load();
    } catch (err) { setError(String(err)); } finally { setBusy(false); }
  };

  return <div className="app-shell">
    <header className="topbar">
      <div><span className="eyebrow">Jobhunter AI</span><h1>Interview workspace</h1></div>
      <nav><a className="active" href="/">AI screening</a><a href="/copilot">Human interview assistant</a></nav>
    </header>
    <main className="dashboard">
      <section className="panel intro-panel">
        <p className="eyebrow">Round 1</p><h2>Create a guided AI screening</h2>
        <p className="muted">The candidate reviews every typed or transcribed answer before submitting it. Final notes preserve evidence and gaps; they do not make a hiring decision.</p>
        <label>Recruiter access token<input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="RECRUITER_TOKEN" /></label>
        {error && <p className="error" role="alert">{error}</p>}
      </section>
      <form className="panel form-grid" onSubmit={create}>
        <label>Candidate name<input name="candidate" required /></label>
        <label>Role<input name="job" required /></label>
        <label>Company<input name="company" required /></label>
        <label className="wide">Company background<textarea name="company_background" required minLength={20} /></label>
        <label className="wide">Job description<textarea name="job_description" required minLength={20} /></label>
        <label className="wide">CV (PDF/TXT)<input name="cv" type="file" accept=".pdf,.txt" /></label>
        <label className="wide">Or paste CV text<textarea name="cv_plain" minLength={20} /></label>
        <div className="row wide"><label>Topics<input name="max_topics" type="number" min="1" max="12" defaultValue="7" /></label><label>Follow-ups per topic<input name="max_probes" type="number" min="0" max="6" defaultValue="1" /></label></div>
        <button className="primary wide" disabled={busy || !token}>{busy ? "Creating…" : "Create interview link"}</button>
      </form>
      <section className="panel sessions">
        <div className="section-title"><div><p className="eyebrow">Sessions</p><h2>Candidate interviews</h2></div><button onClick={() => void load()}>Refresh</button></div>
        {!sessions.length && <p className="muted">No sessions loaded.</p>}
        {sessions.map((session) => <article className="session" key={session.id}>
          <div><strong>{session.candidate}</strong><p>{session.job} · {session.company}</p></div>
          <span className={`status ${session.state}`}>{session.state}</span>
          <a href={session.url} target="_blank" rel="noreferrer">Open candidate link ↗</a>
        </article>)}
      </section>
    </main>
  </div>;
}
