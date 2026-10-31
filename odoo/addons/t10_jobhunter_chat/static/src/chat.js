/** @odoo-module **/
import { Component, markup, onMounted, onPatched, onWillStart, onWillUnmount, proxy, signal, t, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { browser } from "@web/core/browser/browser";
import { user } from "@web/core/user";
import { session } from "@web/session";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";
import { activityEntries, formatDuration, mergeEventPage, renderMarkdown, runIsActive, sourceTarget, statusLabel } from "./chat_utils";

export class JobhunterChat extends Component {
    static template = "t10_jobhunter_chat.Workspace";
    props = useProps({ ...standardActionServiceProps, docked: t.boolean().optional(), currentRecord: t.or([t.object(), t.literal(null)]).optional() });
    inputRef = signal.ref();
    threadRef = signal.ref();
    searchRef = signal.ref();

    setup() {
        this.action = useService("action");
        this.state = proxy({
            sidebar: !this.props.docked && window.innerWidth > 900, loading: true, sending: false, stopping: false,
            history: [], conversation: null, messages: [], runs: [], events: {}, cursors: {}, expanded: {},
            draft: "", contexts: [], notice: "", noticeCode: "", connected: false, copied: null,
            picker: false, pickerModel: "hr.job", pickerQuery: "", pickerRecords: [], pickerBusy: false,
            pickerParent: null, pickerError: "",
            jobProposals: [], jobBusy: false, candidateIds: [], focusId: null, workflowCase: null, workflowBusy: false, workflowOpen: false,
            scheduleStart: "", scheduleZone: "", scheduleDuration: "", participants: [], interviewUrl: "", interviewBusy: false,
        });
        this.generation = 0;
        this.destroyed = false;
        this.followBottom = true;
        onWillStart(() => this.bootstrap());
        onMounted(() => this.inputRef()?.focus());
        onPatched(() => {
            const input = this.inputRef();
            if (input) { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 180)}px`; }
            const thread = this.threadRef();
            if (thread && this.followBottom) thread.scrollTop = thread.scrollHeight;
        });
        onWillUnmount(() => {
            this.destroyed = true;
            this.generation++;
            clearTimeout(this.pollTimer);
            clearTimeout(this.searchTimer);
            clearTimeout(this.copyTimer);
        });
    }

    async call(operation, args = {}) {
        let response;
        try {
            response = await rpc("/jobhunter/chat/rpc", { operation, arguments: args, csrf_token: globalThis.odoo?.csrf_token || "" }, { silent: true });
        } catch {
            throw Object.assign(new Error("Chat service is unavailable. Reconnect to try again."), { code: "service_unavailable" });
        }
        if (response?.error) throw Object.assign(new Error(response.error.message || "The request could not be completed."), { code: response.error.code });
        return response;
    }

    showError(error) {
        this.state.notice = error.message || "The request could not be completed.";
        this.state.noticeCode = error.code || "request_failed";
    }

    async bootstrap() {
        const generation = this.generation;
        this.state.loading = true;
        this.state.notice = "";
        try {
            const history = await this.call("conversations.list");
            if (generation !== this.generation || this.destroyed) return;
            this.state.history = history.conversations || [];
            const health = await this.call("health");
            if (generation !== this.generation || this.destroyed) return;
            this.state.connected = health.status === "ok";
            if (health.provider_configured === false) {
                this.state.notice = "OpenAI is not configured for Jobhunter yet. Existing records and chat history remain available.";
                this.state.noticeCode = "configuration_required";
            }
            const active = this.activeRun;
            if (active) {
                await this.fetchRun(active.id, generation);
                if (generation === this.generation && this.activeRun) this.schedulePoll(active.id, generation);
            }
        } catch (error) { if (generation === this.generation && !this.destroyed) { this.state.connected = false; this.showError(error); } }
        finally { if (generation === this.generation && !this.destroyed) this.state.loading = false; }
    }

    async refreshHistory() {
        const result = await this.call("conversations.list");
        if (!this.destroyed) this.state.history = result.conversations || [];
    }

    newChat() {
        this.generation++;
        this.pendingSubmission = null;
        this.workflowGeneration = (this.workflowGeneration || 0) + 1;
        Object.assign(this.state, {jobProposals: [], jobBusy: false, candidateIds: [], focusId: null, workflowCase: null, workflowBusy: false, workflowOpen: false, interviewUrl: "", participants: [], scheduleStart: "", scheduleZone: "", scheduleDuration: ""});
        clearTimeout(this.pollTimer);
        Object.assign(this.state, { conversation: null, messages: [], runs: [], events: {}, cursors: {}, expanded: {}, draft: "", contexts: [], notice: "", sending: false, stopping: false, loading: false });
        this.followBottom = true;
        this.inputRef()?.focus();
    }

    async openConversation(id) {
        const generation = ++this.generation;
        this.workflowGeneration = (this.workflowGeneration || 0) + 1;
        Object.assign(this.state, {jobProposals: [], jobBusy: false, interviewUrl: "", workflowCase: null, focusId: null, workflowOpen: false, interviewUrl: "", participants: [], scheduleStart: "", scheduleZone: "", scheduleDuration: ""});
        clearTimeout(this.pollTimer);
        this.state.sending = false;
        this.state.stopping = false;
        this.state.loading = true;
        this.state.notice = "";
        try {
            const result = await this.call("conversations.get", { conversation_id: id });
            if (generation !== this.generation || this.destroyed) return;
            this.pendingSubmission = null;
            Object.assign(this.state, { conversation: result.conversation, messages: result.messages || [], runs: result.runs || [], contexts: [], draft: "", events: {}, cursors: {}, expanded: {} });
            this.state.candidateIds = result.candidate_ids || [];
            this.state.focusId = result.focused_applicant_id || null;
            if (this.state.focusId) this.refreshWorkflow();
            this.followBottom = true;
            await this.refreshJobProposals(generation);
            for (const run of this.state.runs) {
                await this.fetchRun(run.id, generation, true);
                if (generation !== this.generation) return;
            }
            if (this.activeRun) this.schedulePoll(this.activeRun.id, generation);
        } catch (error) { if (generation === this.generation) this.showError(error); }
        finally { if (generation === this.generation) this.state.loading = false; }
    }

    async refreshJobProposals(generation = this.generation) {
        if (generation !== this.generation || this.destroyed) return;
        const id = this.state.conversation?.id;
        if (!id) return;
        const result = await this.call("jobs.proposals", {conversation_id: id});
        if (generation === this.generation && !this.destroyed && this.state.conversation?.id === id) this.state.jobProposals = result.proposals || [];
    }

    async confirmJob(proposal) {
        if (this.state.jobBusy || this.state.sending || this.activeRun || proposal.status !== "pending") return;
        const generation = this.generation;
        this.state.jobBusy = true;
        try {
            await this.call("jobs.confirm", {proposal_id: proposal.id, review_hash: proposal.review_hash});
            await this.refreshJobProposals(generation);
        } catch (error) {
            if (generation === this.generation) {
                this.showError(error);
                try {
                    await this.refreshJobProposals(generation);
                    if (this.state.jobProposals.some(item => item.id === proposal.id && item.status === "created")) this.state.notice = "Position created; confirmation receipt recovered.";
                } catch { /* A manual retry uses the same immutable proposal. */ }
            }
        } finally { if (generation === this.generation) this.state.jobBusy = false; }
    }

    openCreatedJob(proposal) {
        if (proposal.job_id) this.action.doAction({type: "ir.actions.act_window", res_model: "hr.job", res_id: proposal.job_id, views: [[false, "form"]], target: "current"});
    }

    async archiveConversation(id) {
        try {
            await this.call("conversations.archive", { conversation_id: id, archived: true });
            if (this.state.conversation?.id === id) this.newChat();
            await this.refreshHistory();
        } catch (error) { this.showError(error); }
    }

    get activeRun() { return this.state.runs.find(run => runIsActive(run)); }
    get hasMessages() { return this.state.messages.length > 0; }
    get canSend() { return Boolean(this.state.draft.trim()) && !this.state.sending && !this.state.loading && !this.activeRun; }
    get turns() {
        return this.state.messages.filter(message => message.role === "user").map(message => {
            const run = this.state.runs.find(item => item.id === message.run_id) || { id: message.run_id, status: "queued" };
            const events = this.state.events[run.id] || [];
            const saved = this.state.messages.find(item => item.run_id === run.id && item.role === "assistant");
            const completed = [...events].reverse().find(event => event.type === "message.completed");
            const unavailable = run.source_state === "unavailable" || saved?.visibility === "unavailable";
            const text = unavailable ? "" : saved?.text || completed?.payload.text || events.filter(event => event.type === "message.delta").map(event => event.payload.text || "").join("");
            return { id: message.id, user: message, run, text, sources: unavailable ? [] : saved?.sources || completed?.payload.sources || [], unavailable, outdated: run.source_state === "outdated" || saved?.visibility === "outdated", active: runIsActive(run), agents: activityEntries(run, events), expanded: Boolean(this.state.expanded[run.id]), duration: formatDuration(run.started_at || run.created_at, run.finished_at), status: statusLabel(run.status) };
        });
    }

    async send(retryTurn = null, workflow = null) {
        if (this.state.sending || this.activeRun) return;
        const text = retryTurn ? retryTurn.user.text : this.state.draft.trim();
        if (!text) return;
        const refs = retryTurn ? retryTurn.user.context_refs || [] : this.state.contexts.map(({ model, id, label }) => ({ model, id, label }));
        if (!retryTurn && this.state.focusId && !refs.some(r => r.model === "hr.applicant" && r.id === this.state.focusId)) refs.push({model: "hr.applicant", id: this.state.focusId, label: this.state.workflowCase?.applicant_name || "Applicant #" + this.state.focusId});
        const generation = this.generation;
        this.state.sending = true;
        this.state.notice = "";
        try {
            if (!this.state.conversation) {
                const conversation = await this.call("conversations.create");
                if (generation !== this.generation || this.destroyed) return;
                this.state.conversation = conversation;
            }
            const contextKeys = [...new Set(refs.map(ref => `${ref.model}:${ref.id}`))].sort();
            const intent = JSON.stringify([this.state.conversation.id, text, contextKeys, retryTurn?.run.id || null, workflow]);
            if (this.pendingSubmission?.intent !== intent) this.pendingSubmission = { intent, id: crypto.randomUUID() };
            const args = { conversation_id: this.state.conversation.id, client_message_id: this.pendingSubmission.id, text, context_refs: refs };
            if (retryTurn) args.retry_of_run_id = retryTurn.run.id;
            if (workflow) args.workflow = workflow;
            const result = await this.call("messages.send", args);
            if (this.pendingSubmission?.id === args.client_message_id) this.pendingSubmission = null;
            if (generation !== this.generation || this.destroyed) return;
            this.state.messages.push({ id: result.message_id, role: "user", text, run_id: result.run_id, context_refs: refs });
            this.state.runs.push({ id: result.run_id, status: result.status, created_at: new Date().toISOString(), source_state: "current" });
            this.state.draft = "";
            this.state.contexts = [];
            this.followBottom = true;
            await this.fetchRun(result.run_id, generation);
            if (this.activeRun) this.schedulePoll(result.run_id, generation);
            this.refreshHistory().catch(() => {});
        } catch (error) { if (generation === this.generation) this.showError(error); }
        finally { if (generation === this.generation && !this.destroyed) { this.state.sending = false; this.inputRef()?.focus(); } }
    }

    async fetchRun(id, generation, drain = false) {
        let pages = 0;
        while (!this.destroyed && generation === this.generation) {
            const cursor = this.state.cursors[id] || 0;
            const result = await this.call("runs.events", { run_id: id, after_seq: cursor });
            if (generation !== this.generation || this.destroyed) return;
            const merged = mergeEventPage(this.state.events[id] || [], result);
            this.state.events[id] = merged.events;
            this.state.cursors[id] = merged.next_seq;
            const index = this.state.runs.findIndex(run => run.id === id);
            if (index >= 0) this.state.runs[index] = result.run;
            // Final publication is atomic: a completed run can still have later event pages.
            if (!runIsActive(result.run)) {
                drain = true;
                if (this.jobRefreshedRun !== id && merged.events.some(event => event.type === "tool.completed" && event.payload?.name === "create_job")) {
                    await this.refreshJobProposals(generation);
                    this.jobRefreshedRun = id;
                }
                if (this.state.focusId && this.workflowRefreshedRun !== id) { this.workflowRefreshedRun = id; this.refreshWorkflow(); }
            }
            if (!drain || !result.events?.length || merged.next_seq === cursor) return;
            if (++pages >= 20) {
                // Very long text traces must never leave a truncated final answer on screen.
                if (!runIsActive(result.run) && this.state.conversation) {
                    const history = await this.call("conversations.get", { conversation_id: this.state.conversation.id });
                    if (generation === this.generation && !this.destroyed) {
                        this.state.messages = history.messages || [];
                        this.state.runs = history.runs || [];
                    }
                }
                return;
            }
        }
    }

    schedulePoll(id, generation) {
        clearTimeout(this.pollTimer);
        this.pollTimer = setTimeout(async () => {
            if (this.destroyed || generation !== this.generation) return;
            try {
                await this.fetchRun(id, generation);
                if (generation !== this.generation) return;
                if (this.activeRun) this.schedulePoll(id, generation);
                else this.refreshHistory().catch(() => {});
            } catch (error) { if (generation === this.generation) this.showError(error); }
        }, 500);
    }

    async stop() {
        const run = this.activeRun;
        if (!run || this.state.stopping) return;
        this.state.stopping = true;
        try {
            const result = await this.call("runs.cancel", { run_id: run.id });
            if (result.status === "cancelled") run.status = "cancelled";
            await this.fetchRun(run.id, this.generation, true);
        } catch (error) { this.showError(error); }
        finally { this.state.stopping = false; }
    }

    onInput(event) { this.state.draft = event.target.value; }
    onKeydown(event) {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (this.canSend) this.send(); }
    }
    onScroll(event) { const el = event.target; this.followBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 100; }
    markdown(text) { return markup(renderMarkdown(text)); }
    pretty(value) { return JSON.stringify(value || {}, null, 2).slice(0, 2000); }
    label(status) { return statusLabel(status); }
    duration(start, end) { return formatDuration(start, end); }
    toggleActivity(id) { this.state.expanded[id] = !this.state.expanded[id]; }
    selectPrompt(text) { this.state.draft = text; this.inputRef()?.focus(); }
    contextIcon(model) { return model === "hr.job" ? "work_outline" : model === "hr.applicant" ? "person_outline" : "description"; }

    async copy(turn) {
        try {
            await navigator.clipboard.writeText(turn.text);
            this.state.copied = turn.id;
            clearTimeout(this.copyTimer);
            this.copyTimer = setTimeout(() => { if (!this.destroyed) this.state.copied = null; }, 1500);
        } catch { this.state.notice = "Copy is unavailable in this browser. Select the answer text to copy it."; }
    }

    openSource(source) {
        const target = sourceTarget(source);
        if (target?.url) window.open(target.url, "_blank", "noopener,noreferrer");
        else if (target) this.action.doAction({ type: "ir.actions.act_window", res_model: target.model, res_id: target.id, views: [[false, "form"]], target: "current" });
    }

    async openPicker() {
        Object.assign(this.state, { picker: true, pickerParent: null, pickerQuery: "", pickerError: "" });
        await this.searchContext();
        this.searchRef()?.focus();
    }
    closePicker() { this.state.picker = false; clearTimeout(this.searchTimer); this.pickerGeneration = (this.pickerGeneration || 0) + 1; this.inputRef()?.focus(); }
    onPickerKeydown(event) {
        if (event.key === "Escape") { event.preventDefault(); this.closePicker(); return; }
        if (event.key !== "Tab") return;
        const elements = [...event.currentTarget.querySelectorAll('button:not(:disabled), input, [tabindex="0"]')];
        const first = elements[0];
        const last = elements.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
    async pickerTab(model) { clearTimeout(this.searchTimer); this.state.pickerModel = model; this.state.pickerParent = null; this.state.pickerQuery = ""; await this.searchContext(); }
    async backToRecords() { this.state.pickerParent = null; await this.searchContext(); this.searchRef()?.focus(); }
    onSearch(event) { this.state.pickerQuery = event.target.value; clearTimeout(this.searchTimer); this.searchTimer = setTimeout(() => this.searchContext(), 250); }
    async searchContext() {
        const generation = this.pickerGeneration = (this.pickerGeneration || 0) + 1;
        this.state.pickerBusy = true;
        this.state.pickerError = "";
        try {
            const result = await this.call("context.search", { model: this.state.pickerModel, query: this.state.pickerQuery });
            if (generation === this.pickerGeneration && this.state.picker) this.state.pickerRecords = result.records || [];
        } catch (error) { if (generation === this.pickerGeneration) this.state.pickerError = error.message; }
        finally { if (generation === this.pickerGeneration) this.state.pickerBusy = false; }
    }
    addContext(record) {
        if (!this.state.contexts.some(item => item.model === record.model && item.id === record.id)) this.state.contexts.push({ ...record, key: `${record.model}:${record.id}` });
        if (record.model === "hr.applicant") {
            if (this.state.focusId !== record.id) Object.assign(this.state, {participants: [], scheduleStart: "", scheduleZone: "", scheduleDuration: "", workflowOpen: false});
            if (!this.state.candidateIds.includes(record.id)) this.state.candidateIds.push(record.id);
            this.state.interviewUrl = ""; this.directRequest = null;
            this.state.focusId = record.id; this.refreshWorkflow();
        }
        this.closePicker();
    }
    removeContext(key) { this.state.contexts = this.state.contexts.filter(item => item.key !== key); }
    async focusCandidate(event) {
        Object.assign(this.state, {participants: [], scheduleStart: "", scheduleZone: "", scheduleDuration: "", workflowOpen: false});
        this.state.interviewUrl = "";
        this.directRequest = null;
        this.state.focusId = Number(event.target.value) || null;
        await this.refreshWorkflow();
    }
    async refreshWorkflow() {
        const ticket = this.workflowGeneration = (this.workflowGeneration || 0) + 1;
        const id = this.state.focusId;
        let resumeReport = false;
        this.state.workflowCase = null;
        if (!id) return;
        this.state.workflowBusy = true;
        try {
            const result = await this.call("workflow.get", {applicant_id: id});
            if (ticket !== this.workflowGeneration || this.destroyed || id !== this.state.focusId) return;
            this.state.workflowCase = result.case;
            const resumeKey = `${result.case.id}:${result.case.version}`;
            if (result.case.automation_state === "reporting" && this.workflowResumeKey !== resumeKey) {
                this.workflowResumeKey = resumeKey;
                resumeReport = true;
            }
            if (!this.state.candidateIds.includes(id)) this.state.candidateIds.push(id);
            if (!this.state.participants.length) this.state.participants = [{name: result.case.applicant_name, email: result.case.candidate_email || "", timezone: "", start: "", end: ""}];
        } catch (error) { if (ticket === this.workflowGeneration) this.showError(error); }
        finally {
            if (ticket === this.workflowGeneration) {
                this.state.workflowBusy = false;
                if (resumeReport) queueMicrotask(() => this.runWorkflow("report"));
            }
        }
    }
    candidateLabel(id) { return this.state.workflowCase?.applicant_id === id ? this.state.workflowCase.applicant_name : "Applicant #" + id; }
    criterionLabel(caseData, id) { return caseData?.criteria?.find((criterion) => criterion.id === id)?.text || id; }
    workflowSource(artifact, key) { return artifact?.sources?.find((source) => source.key === key) || null; }
    async approveCriteria() {
        const current = this.state.workflowCase;
        if (!current || this.activeRun || this.state.sending) return;
        try {
            await this.call("workflow.criteria.approve", {case_id: current.id, version: current.criteria_version});
            await this.refreshWorkflow();
            await this.runWorkflow("screen");
        } catch (error) { this.showError(error); }
    }
    async runWorkflow(action, data = {}) {
        if (!this.state.focusId || this.activeRun || this.state.sending || this.state.workflowBusy) return;
        this.state.draft = {screen: "Help me check this candidate CV against the approved Software Engineer requirements.", schedule: "Prepare an unbooked interview scheduling proposal for this candidate.", orchestrate: "Review CV evidence and prepare a scheduling preview and interview questions for my review. Do not book or send an invitation before explicit confirmation.", prepare_interview: "Prepare five typed interview questions for this candidate using the approved criteria and CV evidence.", report: "Summarize the available CV findings and actually submitted interview answers, preserving incomplete evidence.", confirm: "Confirm booking & send the reviewed candidate invitation."}[action];
        if (action === "prepare_interview") { this.state.interviewUrl = ""; this.directRequest = null; }
        this.workflowRefreshedRun = null;
        await this.send(null, {action, applicant_id: this.state.focusId, data});
    }
    useCurrentRecord() { if (this.props.currentRecord) this.addContext(this.props.currentRecord); }
    get directInterview() {
        const current=this.state.workflowCase;
        return current?.direct_interview || (current?.interview?.guide_version === current?.direct_guide?.version ? current.interview : null);
    }
    directRequestId(create=false) {
        const current=this.state.workflowCase;
        if (!current?.direct_guide) return null;
        // This is an idempotency marker, never a bearer token. Odoo authenticates
        // the owner and revalidates the version/sources on every recovery request.
        const identity=`${session.db}:${user.userId}:${current.id}:${current.direct_guide.version}`;
        const storageKey=`jobhunter.direct.request:${identity}`;
        let id=this.directRequest?.identity === identity ? this.directRequest.id : null;
        try { id=id || browser.localStorage.getItem(storageKey); } catch { /* storage may be disabled */ }
        if (!id && create) id=crypto.randomUUID();
        if (id) {
            this.directRequest={identity,id};
            try { browser.localStorage.setItem(storageKey,id); } catch { /* in-memory retry remains available */ }
        }
        return id;
    }
    get canCreateInterviewLink() {
        return Boolean(this.state.workflowCase?.direct_guide) && (!this.directInterview || Boolean(this.directRequestId())) && !['revoked','expired'].includes(this.directInterview?.status);
    }
    async createInterviewLink() {
        const current = this.state.workflowCase;
        const guide = current?.direct_guide;
        if (!guide || !this.canCreateInterviewLink || this.activeRun || this.state.interviewBusy) return;
        const requestId=this.directRequestId(true);
        const generation = this.workflowGeneration;
        this.state.interviewBusy=true;
        try {
            const result=await this.call("workflow.interview.create", {case_id:current.id,guide_version:guide.version,client_request_id:requestId});
            if (this.destroyed || generation !== this.workflowGeneration || current.id !== this.state.workflowCase?.id) return;
            this.state.workflowCase=result.case;
            this.state.interviewUrl=result.interview_url;
        } catch(error) { if (generation === this.workflowGeneration) this.showError(error); }
        finally { this.state.interviewBusy=false; }
    }
    updateParticipant(index, field, event) { this.state.participants[index][field] = event.target.value; }
    addParticipant() { if (this.state.participants.length < 10) this.state.participants.push({name: "", email: "", timezone: "", start: "", end: ""}); }
    removeParticipant(index) { this.state.participants.splice(index, 1); }
    schedulePayload() {
        const participants = this.state.participants.map(p => ({name: p.name, email: p.email, timezone: p.timezone, user_id: null, availability: p.start && p.end ? [{start: p.start, end: p.end}] : []}));
        return {start: this.state.scheduleStart || null, timezone: this.state.scheduleZone || null, duration_minutes: this.state.scheduleDuration ? Number(this.state.scheduleDuration) : null, participant_count: participants.length || null, participants, candidate_email: this.state.workflowCase?.candidate_email || null};
    }
    async submitSchedule() {
        await this.runWorkflow("schedule", {schedule: this.schedulePayload()});
    }
    async submitOrchestration() { await this.runWorkflow("orchestrate", {schedule: this.schedulePayload()}); }
    async confirmBooking() {
        const draft = this.state.workflowCase?.schedule;
        if (draft?.status === "ready" && draft.guide_ready && !draft.booking) await this.runWorkflow("confirm", {draft_version: draft.version});
    }
    async showDocuments(record) {
        clearTimeout(this.searchTimer);
        const generation = this.pickerGeneration = (this.pickerGeneration || 0) + 1;
        this.state.pickerBusy = true;
        this.state.pickerError = "";
        try {
            const result = await this.call("context.documents", { model: record.model, record_id: record.id });
            if (generation !== this.pickerGeneration || !this.state.picker || this.destroyed) return;
            this.state.pickerParent = record;
            this.state.pickerRecords = result.records || [];
        } catch (error) { if (generation === this.pickerGeneration && !this.destroyed) this.state.pickerError = error.message; }
        finally { if (generation === this.pickerGeneration && !this.destroyed) this.state.pickerBusy = false; }
    }
}


