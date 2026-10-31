import React from "react";
import { api, useRecruiterToken } from "./api";

type Finding = { criterion_id: string; status: string; statement: string; evidence_turn_ids: string[] };
type CvCheck = { claim: string; status: string; statement: string; evidence_turn_ids: string[] };
type Report = { summary?: string; strengths?: string[]; gaps?: string[]; cv_claims?: string[]; cv_checks?: CvCheck[]; findings?: Finding[] };
type SubmittedTurn = { id: string; question: string; answer: string; language?: string; submitted_at?: string; topic?: string };
type Session = {
  id: number; candidate: string; job: string; company: string; state: string;
  created_at: string; url: string; error?: string; flags?: string[]; report?: Report; turns?: SubmittedTurn[];
};

export function RecruiterScreening() {
  const [token, setToken] = useRecruiterToken();
  const [sessions, setSessions] = React.useState<Session[]>([]);
  const [selected, setSelected] = React.useState<Session | null>(null);
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    if (!token) return;
    try { setSessions(await api<Session[]>("/api/interviews", { recruiterToken: token })); setError(""); }
    catch (reason) { setError(String(reason)); }
  }, [token]);

  React.useEffect(() => { void load(); }, [load]);

  const create = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const formElement = event.currentTarget;
      const form = new FormData(formElement);
      const companyContext = String(form.get("company_background") || "").trim();
      const jobDescription = String(form.get("job_description") || "").trim();
      const cv = form.get("cv");
      const cvName = cv instanceof File ? cv.name.replace(/\.[^.]+$/, "") : "Test Candidate";
      form.set("candidate", cvName || "Test Candidate");
      form.set("company", companyContext.split(/\r?\n/)[0].slice(0, 120) || "Test Company");
      form.set("job", jobDescription.split(/\r?\n/)[0].slice(0, 120) || "Open role");
      form.set("max_topics", "7");
      form.set("max_probes", "1");
      await api<Session>("/api/interviews", { method: "POST", body: form, recruiterToken: token });
      formElement.reset(); await load();
    } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  };

  const review = async (id: number) => {
    setBusy(true); setError("");
    try { setSelected(await api<Session>(`/api/interviews/${id}`, { recruiterToken: token })); }
    catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  };

  const retryReport = async (id: number) => {
    setBusy(true); setError("");
    try {
      await api(`/api/interviews/${id}/retry-report`, { method: "POST", recruiterToken: token });
      await load(); await review(id);
    } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
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
        <label>Recruiter access token<input type="password" value={token} onChange={(event) => setToken(event.target.value)} placeholder="RECRUITER_TOKEN" /></label>
        {error && <p className="error" role="alert">{error}</p>}
      </section>
      <form className="panel form-grid" onSubmit={create}>
        <label className="wide">Company<textarea name="company_background" required minLength={20} placeholder="First line: company name. Then add salary range, work arrangement, location, process and company background." /></label>
        <label className="wide">JD<textarea name="job_description" required minLength={20} placeholder="First line: role name. Then add responsibilities and must-have requirements." /></label>
        <label className="wide">Upload CV<input name="cv" type="file" accept=".pdf,.txt" required /></label>
        <button className="primary wide" disabled={busy || !token}>{busy ? "Working…" : "Create interview link"}</button>
      </form>
      <section className="panel sessions">
        <div className="section-title"><div><p className="eyebrow">Sessions</p><h2>Candidate interviews</h2></div><button onClick={() => void load()}>Refresh</button></div>
        {!sessions.length && <p className="muted">No sessions loaded.</p>}
        {sessions.map((session) => <article className="session" key={session.id}>
          <div><strong>{session.candidate}</strong><p>{session.job} · {session.company}</p></div>
          <span className={`status ${session.state}`}>{session.state}</span>
          <div className="session-actions"><button onClick={() => void review(session.id)}>Review</button><a href={session.url} target="_blank" rel="noreferrer">Candidate link ↗</a></div>
        </article>)}
      </section>

      {selected && <section className="panel screening-review">
        <div className="section-title">
          <div><p className="eyebrow">Evidence review · human decision required</p><h2>{selected.candidate}</h2><p className="muted">{selected.job} · {selected.state}</p></div>
          <button onClick={() => setSelected(null)}>Close</button>
        </div>
        {selected.error && <p className="error">{selected.error}</p>}
        {(selected.state === "done" || selected.state === "error") && <button onClick={() => void retryReport(selected.id)} disabled={busy}>Retry report</button>}
        <div className="review-columns">
          <div><h3>Submitted transcript</h3>{!selected.turns?.length && <p className="muted">No submitted answers.</p>}
            {selected.turns?.map((turn) => <article className="review-turn" key={turn.id}><small>{turn.id} · {(turn.language || "en").toUpperCase()}</small><strong>{turn.question}</strong><p>{turn.answer}</p></article>)}
          </div>
          <div><h3>AI evidence draft</h3>{!selected.report && <p className="muted">Report is not available yet. Refresh after the interview finishes.</p>}
            {selected.report?.summary && <p>{selected.report.summary}</p>}
            {!!selected.flags?.length && <><h3>Interview flags</h3><ul>{selected.flags.map((flag) => <li key={flag}>{flag}</li>)}</ul></>}
            {!!selected.report?.cv_checks?.length && <><h3>CV claim checks</h3><p className="muted">Consistency with an answer is not independent proof that a claim is true.</p><div className="review-findings">{selected.report.cv_checks.map((check, index) => <article key={`${check.claim}-${index}`}><span className={`status ${check.status}`}>{check.status}</span><strong>{check.claim}</strong><p>{check.statement}</p><small>{check.evidence_turn_ids.join(", ") || "Not verified by a submitted answer"}</small></article>)}</div></>}
            <h3>Screening rubric</h3>
            <div className="review-findings">{selected.report?.findings?.map((finding) => <article key={finding.criterion_id}><span className={`status ${finding.status}`}>{finding.status}</span><strong>{finding.criterion_id}</strong><p>{finding.statement}</p><small>{finding.evidence_turn_ids.join(", ") || "No candidate evidence"}</small></article>)}</div>
          </div>
        </div>
      </section>}
    </main>
  </div>;
}
