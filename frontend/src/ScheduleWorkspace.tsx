import React from "react";
import { api, useRecruiterToken } from "./api";

type Interviewer = { ref: string; name: string; email: string; google_connected: boolean };
type Candidate = { candidate_ref: string; candidate_name: string; candidate_email: string };
type Job = {
  job_ref: string; job_title: string; duration_minutes: number;
  interviewers: Interviewer[]; candidates: Candidate[];
};
type Proposal = {
  candidate: Candidate; scheduled_for: string; response_deadline: string; duration_minutes: number;
};
type JobPlan = {
  job: Job; timezone: string; google_verified: boolean;
  availability_source: string; proposals: Proposal[];
  busy_windows: Array<{ interviewer_ref: string; interviewer_name: string; start: string; end: string; source: string }>;
};
type DemoResult = {
  delivery_target?: string;
  results: Array<{
    summary: string;
    output: Array<{ candidate_ref: string; state: string; delivery_ref: string | null }>;
  }>;
};

export function ScheduleWorkspace() {
  const [token, setToken] = useRecruiterToken();
  const [jobs, setJobs] = React.useState<Job[]>([]);
  const [jobRef, setJobRef] = React.useState("");
  const [selected, setSelected] = React.useState<string[]>([]);
  const [recipients, setRecipients] = React.useState<Record<string, string>>({});
  const [plan, setPlan] = React.useState<JobPlan | null>(null);
  const [result, setResult] = React.useState<DemoResult | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const loaded = React.useRef(false);
  const job = jobs.find((item) => item.job_ref === jobRef);

  const loadJobs = async () => {
    setBusy(true); setError("");
    try {
      const response = await api<{ jobs: Job[] }>("/api/agents/schedule/jobs", { recruiterToken: token });
      setJobs(response.jobs);
      const demoJob = response.jobs.find(
        (item) => item.interviewers.length >= 3 && item.candidates.length >= 2,
      ) || response.jobs[0];
      if (demoJob) {
        setJobRef(demoJob.job_ref);
        setSelected(demoJob.candidates.slice(0, 2).map((item) => item.candidate_ref));
      }
    } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  };

  React.useEffect(() => {
    if (!token || loaded.current) return;
    loaded.current = true; void loadJobs();
  }, [token]);

  const changeJob = (ref: string) => {
    const next = jobs.find((item) => item.job_ref === ref);
    setJobRef(ref); setPlan(null); setResult(null);
    setSelected(next?.candidates.slice(0, 2).map((item) => item.candidate_ref) || []);
  };

  const toggleCandidate = (ref: string) => {
    setPlan(null);
    setSelected((current) => current.includes(ref) ? current.filter((item) => item !== ref) : [...current, ref]);
  };

  const createPlan = async () => {
    setBusy(true); setError(""); setResult(null);
    try {
      const response = await api<JobPlan>("/api/agents/schedule/job-plan", {
        method: "POST", recruiterToken: token,
        body: JSON.stringify({ job_ref: jobRef, candidate_refs: selected }),
      });
      setPlan(response);
      setRecipients(Object.fromEntries(response.proposals.map((item) => [
        item.candidate.candidate_ref,
        item.candidate.candidate_email.endsWith("@example.com") ? "" : item.candidate.candidate_email,
      ])));
    } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  };

  const runJudgeDemo = async () => {
    setBusy(true); setError(""); setResult(null);
    try {
      const response = await api<{ plan: JobPlan } & DemoResult>(
        `/api/agents/schedule/job-demo-run/${jobRef}`,
        { method: "POST", recruiterToken: token },
      );
      setPlan(response.plan);
      setSelected(response.plan.proposals.map((item) => item.candidate.candidate_ref));
      setResult({ delivery_target: response.delivery_target, results: response.results });
    } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  };

  const connectGoogle = async (interviewer: Interviewer) => {
    const popup = window.open("", "_blank", "noopener,noreferrer");
    try {
      const response = await api<{ connect_url: string }>(
        `/api/agents/schedule/google/connect/${interviewer.ref}`, { recruiterToken: token },
      );
      if (popup) popup.location.href = response.connect_url;
    } catch (reason) { popup?.close(); setError(String(reason)); }
  };

  const confirm = async () => {
    if (!plan) return;
    setBusy(true); setError("");
    try {
      const response = await api<DemoResult>("/api/agents/schedule/job-confirm", {
        method: "POST", recruiterToken: token,
        body: JSON.stringify({
          request_ref: `job-demo-${jobRef}-${Date.now()}`, job_ref: jobRef,
          proposals: plan.proposals, recipient_overrides: recipients,
        }),
      });
      setResult(response);
    } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  };

  const readyToSend = !!plan && plan.proposals.every((item) => recipients[item.candidate.candidate_ref]?.includes("@"));

  return <div className="app-shell">
    <header className="topbar"><div><span className="eyebrow">Jobhunter AI</span><h1>Schedule Agent workspace</h1></div><nav><a href="/">AI screening</a><a className="active" href="/schedule">Schedule</a><a href="/copilot">Human interview assistant</a></nav></header>
    <main className="dashboard schedule-dashboard">
      <section className="panel intro-panel">
        <p className="eyebrow">Job-based standalone demo</p><h2>Schedule a complete interview panel</h2>
        <p className="muted">Choose one job and its candidates. Schedule finds a distinct shared slot for every candidate across all interviewers assigned to that job.</p>
        <label>Recruiter access token<input type="password" value={token} onChange={(event) => setToken(event.target.value)} placeholder="test-recruiter" /></label>
        <button disabled={busy || !token} onClick={() => void loadJobs()}>{busy ? "Loading…" : "Reload jobs"}</button>
        {error && <p className="error" role="alert">{error}</p>}
      </section>
      <section className="panel form-grid">
        <label className="wide">Job<select value={jobRef} onChange={(event) => changeJob(event.target.value)}><option value="">Choose job</option>{jobs.map((item) => <option key={item.job_ref} value={item.job_ref}>{item.job_title} · {item.interviewers.length} interviewers · {item.candidates.length} candidates</option>)}</select></label>
        {job && <div className="notice wide"><strong>Assigned interviewers</strong>{job.interviewers.map((item) => <div key={item.ref}>{item.name} · {item.google_connected ? "Google connected" : <button type="button" onClick={() => void connectGoogle(item)}>Connect Google</button>}</div>)}</div>}
        {job && <fieldset className="wide"><legend>Candidates</legend>{job.candidates.slice(0, 2).map((item) => <label key={item.candidate_ref}><input type="checkbox" checked readOnly /> {item.candidate_name} · {item.candidate_email}</label>)}</fieldset>}
        <button className="primary wide" disabled={busy || !jobRef || (job?.candidates.length || 0) < 2} onClick={() => void runJudgeDemo()}>{busy ? "Agent is scheduling & sending…" : "Run Schedule Agent demo · auto-pick & send 2 emails"}</button>
      </section>
      {plan && <section className="panel sessions">
        <div className="section-title"><div><p className="eyebrow">Preview</p><h2>{plan.job.job_title}</h2></div><span className={`status ${plan.google_verified ? "accepted" : "prepared"}`}>{plan.google_verified ? "Google verified" : "Internal calendar only"}</span></div>
        <div className="notice"><strong>Busy windows used by the agent</strong>{plan.busy_windows.length ? plan.busy_windows.map((item, index) => <div key={`${item.interviewer_ref}-${item.start}-${index}`}>{item.interviewer_name}: {item.start} → {item.end} · {item.source}</div>) : <div>No busy blocks in the search window.</div>}</div>
        {plan.proposals.map((item) => <article className="session" key={item.candidate.candidate_ref}>
          <div><strong>✓ Agent picked · {item.candidate.candidate_name}</strong><p>{item.scheduled_for} · {item.duration_minutes} minutes</p></div>
          <span className="status prepared">Email sent to test inbox</span>
        </article>)}
        {!plan.google_verified && <p className="notice"><strong>Availability:</strong> the tech leads have not connected Google Calendar, so the agent used persisted interview calendars only. <strong>Email delivery is active</strong> and uses the company SMTP account configured in the environment.</p>}
        {result !== null && <div className="notice"><strong>✓ {result.results.length} real emails sent</strong><div>Waiting for candidate Accept/Decline responses.</div>{result.results.flatMap((item) => item.output).map((item) => <div key={item.delivery_ref || item.candidate_ref}>Candidate {item.candidate_ref}: {item.state} · {item.delivery_ref}</div>)}</div>}
      </section>}
    </main>
  </div>;
}
