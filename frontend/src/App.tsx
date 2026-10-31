import { CandidateInterview } from "./CandidateInterview";
import { HumanCopilot } from "./HumanCopilot";
import { RecruiterScreening } from "./RecruiterScreening";
import { ScheduleWorkspace } from "./ScheduleWorkspace";

export function App() {
  const path = window.location.pathname;
  const candidate = path.match(/^\/interview\/([^/]+)$/);
  if (candidate) return <CandidateInterview token={candidate[1]} />;
  if (path === "/copilot") return <HumanCopilot />;
  if (path === "/schedule") return <ScheduleWorkspace />;
  return <RecruiterScreening />;
}
