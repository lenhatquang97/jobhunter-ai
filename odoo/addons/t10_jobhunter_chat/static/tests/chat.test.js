import { expect, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";
import { contains, mountWithCleanup, onRpc } from "@web/../tests/web_test_helpers";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { JobhunterChat } from "@t10_jobhunter_chat/chat";
import { activityEntries, renderMarkdown } from "@t10_jobhunter_chat/chat_utils";

defineMailModels();

const conversation = { id: "11111111-1111-4111-8111-111111111111", title: "Find open roles", archived: false };
const runId = "22222222-2222-4222-8222-222222222222";
const stamp = "2026-10-31T03:00:00Z";
function mockChat(extra = {}) {
    onRpc("/jobhunter/chat/rpc", async (request) => {
        const { params } = await request.json();
        if (extra[params.operation]) return extra[params.operation](params.arguments);
        return ({
            health: { status: "ok", database: "ok", provider_configured: true },
            "conversations.list": { conversations: [] },
            "conversations.create": conversation,
            "conversations.get": { conversation, messages: [], runs: [] },
            "context.search": { records: [] },
        })[params.operation] || {};
    });
}

test("native workspace shows a usable empty composer", async () => {
    mockChat();
    await mountWithCleanup(JobhunterChat, { props: { action: { tag: "t10_jobhunter_chat.workspace" } } });
    expect(".jh-welcome h1").toHaveText("Where should we begin?");
    expect('textarea[aria-label="Message Jobhunter"]').toHaveCount(1);
    expect('button[aria-label="Add Odoo context"]').toHaveCount(1);
    expect('button[aria-label="Send message"]').toHaveProperty("disabled", true);
});

test("sending renders actual response and activity without interpreting HTML", async () => {
    mockChat({
        "messages.send": () => ({ run_id: runId, message_id: "message-1", status: "queued" }),
        "runs.events": () => ({ run: { id: runId, status: "completed", source_state: "current", started_at: stamp, finished_at: stamp }, next_seq: 5, events: [
            { run_id: runId, seq: 1, type: "agent.started", timestamp: stamp, agent_id: "Orchestrator", payload: {} },
            { run_id: runId, seq: 2, type: "message.delta", timestamp: stamp, payload: { text: "**Hello** <script>bad()</script>" } },
            { run_id: runId, seq: 3, type: "agent.completed", timestamp: stamp, agent_id: "Orchestrator", payload: {} },
            { run_id: runId, seq: 4, type: "message.completed", timestamp: stamp, payload: { message_id: "answer-1", text: "**Hello** <script>bad()</script>", sources: [] } },
            { run_id: runId, seq: 5, type: "run.completed", timestamp: stamp, payload: {} },
        ] }),
    });
    await mountWithCleanup(JobhunterChat, { props: { action: {} } });
    await contains(".jh-composer textarea").edit("Hello");
    await contains('button[aria-label="Send message"]').click();
    await animationFrame();
    expect(".jh-user-bubble").toHaveText("Hello");
    expect(".jh-answer strong").toHaveText("Hello");
    expect(".jh-answer script").toHaveCount(0);
    await contains(".jh-activity-toggle").click();
    expect(".jh-agent-name").toHaveText("Orchestrator");
    expect(".jh-tool").toHaveCount(0);
});

test("source picker adds an existing record as removable context", async () => {
    mockChat({ "context.search": () => ({ records: [{ model: "hr.job", id: 12, label: "Platform Engineer" }] }) });
    await mountWithCleanup(JobhunterChat, { props: { action: {} } });
    await contains('button[aria-label="Add Odoo context"]').click();
    await contains(".jh-picker-select").click();
    expect(".jh-context-chip > span:not(.oi)").toHaveText("Platform Engineer");
    await contains('.jh-context-chip button[aria-label="Remove context"]').click();
    expect(".jh-context-chip").toHaveCount(0);
});

test("terminal replay drains later pages before displaying the complete answer", async () => {
    mockChat({
        "messages.send": () => ({ run_id: runId, message_id: "message-1", status: "queued" }),
        "runs.events": ({ after_seq }) => ({
            run: { id: runId, status: "completed", source_state: "current", started_at: stamp, finished_at: stamp },
            next_seq: after_seq === 0 ? 200 : 202,
            events: after_seq === 0 ? Array.from({ length: 200 }, (_, index) => ({ run_id: runId, seq: index + 1, type: "message.delta", timestamp: stamp, payload: { text: "partial " } })) : after_seq === 200 ? [
                { run_id: runId, seq: 201, type: "message.completed", timestamp: stamp, payload: { message_id: "answer-1", text: "Complete final answer.", sources: [] } },
                { run_id: runId, seq: 202, type: "run.completed", timestamp: stamp, payload: {} },
            ] : [],
        }),
    });
    await mountWithCleanup(JobhunterChat, { props: { action: {} } });
    await contains(".jh-composer textarea").edit("Hello");
    await contains('button[aria-label="Send message"]').click();
    await animationFrame();
    expect(".jh-answer").toHaveText("Complete final answer.");
});

test("unavailable service leaves context picker and reconnect accessible", async () => {
    mockChat({ "conversations.list": () => ({ error: { code: "service_unavailable", message: "Chat service is unavailable." } }) });
    await mountWithCleanup(JobhunterChat, { props: { action: {} } });
    expect(".jh-notice").toHaveText(/Chat service is unavailable/);
    expect('button[aria-label="Reconnect chat"]').toHaveCount(1);
    expect(".jh-welcome").toHaveCount(1);
});

test("formatting and activity enforce source-data boundaries", () => {
    expect(renderMarkdown('<img src=x onerror="bad()">')).not.toInclude("<img");
    expect(activityEntries({ status: "completed", source_state: "unavailable" }, [
        { seq: 1, type: "agent.started", agent_id: "Report", payload: {} },
    ])).toEqual([]);
});

test("Reconnect resumes the active run without resubmitting the message", async () => {
    mockChat({ "runs.events": () => ({ run: { id: runId, status: "completed", source_state: "current" }, next_seq: 1, events: [{ seq: 1, run_id: runId, type: "message.completed", timestamp: stamp, payload: { message_id: "answer-recovered", text: "Recovered answer.", sources: [] } }] }) });
    const component = await mountWithCleanup(JobhunterChat, { props: { action: {} } });
    component.state.conversation = conversation;
    component.state.messages = [{ id: "recover-user", role: "user", text: "Hello", run_id: runId }];
    component.state.runs = [{ id: runId, status: "running", source_state: "current" }];
    component.state.notice = "Connection lost.";
    await component.bootstrap();
    await animationFrame();
    expect(".jh-answer").toHaveText("Recovered answer.");
    expect(component.activeRun).toBe(undefined);
});

test("New chat discards a late conversation creation before any send", async () => {
    const creation = Promise.withResolvers();
    let sends = 0;
    mockChat({ "conversations.create": () => creation.promise, "messages.send": () => { sends++; return { run_id: runId, message_id: "late-message", status: "completed" }; } });
    const component = await mountWithCleanup(JobhunterChat, { props: { action: {} } });
    component.state.draft = "Abandoned draft";
    const pending = component.send();
    component.newChat();
    creation.resolve(conversation);
    await pending;
    await animationFrame();
    expect(component.state.conversation).toBe(null);
    expect(sends).toBe(0);
    expect(".jh-user-bubble").toHaveCount(0);
});

test("New chat clears an abandoned history loading state", async () => {
    const opening = Promise.withResolvers();
    mockChat({ "conversations.get": () => opening.promise });
    const component = await mountWithCleanup(JobhunterChat, { props: { action: {} } });
    const pending = component.openConversation(conversation.id);
    component.newChat();
    opening.resolve({ conversation, messages: [], runs: [] });
    await pending;
    component.state.draft = "Fresh message";
    await animationFrame();
    expect(".jh-loading").toHaveCount(0);
    expect('button[aria-label="Send message"]').toHaveProperty("disabled", false);
});

test("document Back restores record search", async () => {
    mockChat({ "context.search": () => ({ records: [{ model: "hr.job", id: 12, label: "Platform Engineer" }] }), "context.documents": () => ({ records: [{ model: "ir.attachment", id: 8, label: "Role.txt" }] }) });
    const component = await mountWithCleanup(JobhunterChat, { props: { action: {} } });
    await component.openPicker();
    await component.showDocuments({ model: "hr.job", id: 12, label: "Platform Engineer" });
    await animationFrame();
    await contains(".jh-picker-parent button").click();
    expect(".jh-picker-search").toHaveCount(1);
    expect(".jh-picker-parent").toHaveCount(0);
});

test("candidate workflow renders criterion evidence with a verified source link", async () => {
    const citation = "Built Python services backed by PostgreSQL.";
    mockChat({
        "context.search": ({ model }) => ({ records: model === "hr.applicant" ? [{ model, id: 31, label: "E2E Alex" }] : [] }),
        "workflow.get": () => ({ case: {
            id: 7, applicant_id: 31, job_id: 4, version: 3, applicant_name: "E2E Alex", candidate_email: "alex@example.invalid",
            criteria_version: 1, criteria_approved: true,
            criteria: [{ id: "C1", text: "Python service development" }, { id: "C2", text: "PostgreSQL database experience" }],
            documents: [{ id: 54, name: "E2E Alex CV.txt" }],
            screening: { id: 9, kind: "screening", source_state: "current", version: 1,
                content: { summary: "Synthetic fixture", findings: [
                    { criterion_id: "C1", status: "partial", finding: "Self-reported experience needs confirmation.", citations: [{ source_key: "S4", quote: citation }], question: "Describe your contribution." },
                    { criterion_id: "C2", status: "unknown", finding: "No independent evidence found.", citations: [], question: "Which PostgreSQL systems did you use?" },
                ] },
                sources: [{ key: "S4", model: "hr.applicant", record_id: 31, attachment_id: 54, title: "E2E Alex CV.txt", locator: "line:1", excerpt: citation }],
            },
            schedule: null, interview: null, report: null, interview_summary: null,
        } }),
    });
    await mountWithCleanup(JobhunterChat, { props: { action: {} } });
    await contains('button[aria-label="Add Odoo context"]').click();
    await contains(".jh-picker-tabs button:last-child").click();
    await contains(".jh-picker-select").click();
    expect(".jh-screening-findings").toHaveCount(1);
    expect(".jh-screening-findings").toHaveText(/Python service development/);
    expect(".jh-screening-findings").toHaveText(/partial/i);
    expect(".jh-screening-findings").toHaveText(/unknown/i);
    expect(".jh-screening-findings q").toHaveText(citation);
    expect('button[aria-label="View source S4 E2E Alex CV.txt line:1"]').toHaveCount(1);
});

test("late documents cannot replace a different picker tab", async () => {
    const documents = Promise.withResolvers();
    mockChat({ "context.documents": () => documents.promise, "context.search": ({ model }) => ({ records: [{ model, id: 12, label: model === "hr.applicant" ? "Applicant A" : "Platform Engineer" }] }) });
    const component = await mountWithCleanup(JobhunterChat, { props: { action: {} } });
    await component.openPicker();
    const pending = component.showDocuments({ model: "hr.job", id: 12, label: "Platform Engineer" });
    await component.pickerTab("hr.applicant");
    documents.resolve({ records: [{ model: "ir.attachment", id: 8, label: "Old role.txt" }] });
    await pending;
    await animationFrame();
    expect(".jh-picker-select").toHaveText("Applicant A");
    expect(".jh-picker-parent").toHaveCount(0);
});

test("retry after a lost send acknowledgment reuses its idempotency key", async () => {
    const submissions = [];
    mockChat({
        "messages.send": (args) => {
            submissions.push(args.client_message_id);
            return submissions.length === 1 ? { error: { code: "service_unavailable", message: "The acknowledgment was lost." } } : { run_id: runId, message_id: "accepted-message", status: "completed" };
        },
        "runs.events": () => ({ run: { id: runId, status: "completed", source_state: "current" }, next_seq: 1, events: [{ seq: 1, run_id: runId, type: "message.completed", timestamp: stamp, payload: { message_id: "accepted-answer", text: "Already completed once.", sources: [] } }] }),
    });
    const component = await mountWithCleanup(JobhunterChat, { props: { action: {} } });
    component.state.draft = "Same intended message";
    await component.send();
    await component.send();
    await animationFrame();
    expect(submissions.length).toBe(2);
    expect(submissions[0]).toBe(submissions[1]);
    expect(".jh-user-bubble").toHaveCount(1);
    expect(".jh-answer").toHaveText("Already completed once.");
});

test("authoritative history recovery resolves the previous pending intent", async () => {
    const ids = [];
    const newRun = "33333333-3333-4333-8333-333333333333";
    mockChat({
        "messages.send": (args) => {
            ids.push(args.client_message_id);
            if (ids.length === 1) return { error: { code: "service_unavailable", message: "Acknowledgment lost." } };
            return { run_id: args.client_message_id === ids[0] ? runId : newRun, message_id: args.client_message_id === ids[0] ? "recovered-user" : "new-user", status: "completed" };
        },
        "conversations.get": () => ({ conversation, messages: [{ id: "recovered-user", role: "user", text: "Repeat this", run_id: runId }], runs: [{ id: runId, status: "completed", source_state: "current" }] }),
        "runs.events": ({ run_id, after_seq }) => ({ run: { id: run_id, status: "completed", source_state: "current" }, next_seq: 1, events: after_seq ? [] : [{ seq: 1, run_id, type: "message.completed", timestamp: stamp, payload: { message_id: "answer-" + run_id, text: "Done.", sources: [] } }] }),
    });
    const component = await mountWithCleanup(JobhunterChat, { props: { action: {} } });
    component.state.draft = "Repeat this";
    await component.send();
    await component.openConversation(conversation.id);
    component.state.draft = "Repeat this";
    await component.send();
    expect(ids[0] === ids[1]).toBe(false);
    expect(new Set(component.state.runs.map(run => run.id)).size).toBe(2);
});

test("outdated answers retain text and render an enabled refresh action", async () => {
    const outdated = { id: runId, status: "completed", source_state: "outdated" };
    mockChat({
        "conversations.get": () => ({ conversation, messages: [{ id: "outdated-user", role: "user", text: "Summarize role", run_id: runId }, { id: "outdated-answer", role: "assistant", text: "Earlier sourced answer.", run_id: runId, visibility: "outdated", sources: [] }], runs: [outdated] }),
        "runs.events": () => ({ run: outdated, events: [], next_seq: 0 }),
    });
    const component = await mountWithCleanup(JobhunterChat, { props: { action: {} } });
    await component.openConversation(conversation.id);
    await animationFrame();
    expect(".jh-outdated").toHaveCount(1);
    expect(".jh-answer").toHaveText("Earlier sourced answer.");
    expect(".jh-retry").toHaveProperty("disabled", false);
});

test("direct interview preparation is available without a scheduling draft", async () => {
    mockChat({"workflow.get": () => ({case: {id: 7, applicant_id:31, criteria_approved:true, criteria:[], documents:[], direct_guide:{version:1, language:'en', questions:[{question:'Your Python example?'}]}}})});
    const component = await mountWithCleanup(JobhunterChat, {props:{action:{}}});
    component.state.candidateIds = [31]; component.state.focusId=31;
    await component.refreshWorkflow(); await animationFrame();
    expect('button.jh-prepare-direct').toHaveText('Prepare interview');
    expect('.jh-direct-guide').toHaveText(/Your Python example/);
    expect('button.jh-create-interview').toHaveText('Create private interview link');
});

test("a lost direct-link response is recoverable after reopening the chat", async () => {
    let issued=false;
    const requests=[];
    const directCase=()=>({id:78,applicant_id:31,criteria_approved:true,criteria:[],documents:[],direct_guide:{version:4,language:'en',questions:[{question:'Your example?'}]},interview:issued?{id:12,guide_version:4,status:'awaiting_answers'}:null});
    mockChat({
        'workflow.get':()=>({case:directCase()}),
        'workflow.interview.create':args=>{
            requests.push(args.client_request_id); issued=true;
            return requests.length===1 ? {error:{code:'service_unavailable',message:'Acknowledgment lost.'}} : {case:directCase(),interview_url:'/jobhunter/interview/synthetic-recovered-link',session_id:12};
        },
    });
    const first=await mountWithCleanup(JobhunterChat,{props:{action:{}}});
    first.state.focusId=31; first.state.candidateIds=[31]; await first.refreshWorkflow();
    await first.createInterviewLink();
    const reopened=await mountWithCleanup(JobhunterChat,{props:{action:{}}});
    reopened.state.focusId=31; reopened.state.candidateIds=[31]; await reopened.refreshWorkflow();
    await animationFrame();
    expect('.jh-create-interview:last').toHaveProperty('disabled',false);
    await reopened.createInterviewLink();
    expect(requests[1]).toBe(requests[0]);
    expect(reopened.state.interviewUrl).toBe('/jobhunter/interview/synthetic-recovered-link');
});
