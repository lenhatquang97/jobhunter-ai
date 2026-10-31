/** @odoo-module **/

const escapeHTML = (value) => String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

function inline(text) {
    const tokens = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g;
    let output = "";
    let cursor = 0;
    for (const match of text.matchAll(tokens)) {
        output += escapeHTML(text.slice(cursor, match.index));
        const value = match[0];
        if (value.startsWith("`")) output += `<code>${escapeHTML(value.slice(1, -1))}</code>`;
        else if (value.startsWith("**")) output += `<strong>${escapeHTML(value.slice(2, -2))}</strong>`;
        else output += `<em>${escapeHTML(value.slice(1, -1))}</em>`;
        cursor = match.index + value.length;
    }
    return output + escapeHTML(text.slice(cursor));
}

/** Deliberately small Markdown renderer: generated HTML is entirely ours. */
export function renderMarkdown(value = "") {
    const lines = String(value).replace(/\r\n/g, "\n").split("\n");
    const blocks = [];
    for (let index = 0; index < lines.length;) {
        const line = lines[index];
        if (!line.trim()) { index++; continue; }
        if (line.trimStart().startsWith("```")) {
            const code = [];
            index++;
            while (index < lines.length && !lines[index].trimStart().startsWith("```")) code.push(lines[index++]);
            if (index < lines.length) index++;
            blocks.push(`<pre><code>${escapeHTML(code.join("\n"))}</code></pre>`);
        } else if (/^#{1,3}\s/.test(line)) {
            const match = line.match(/^(#{1,3})\s+(.*)$/);
            const level = match[1].length + 1;
            blocks.push(`<h${level}>${inline(match[2])}</h${level}>`);
            index++;
        } else if (/^\s*[-*]\s+/.test(line)) {
            const items = [];
            while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) items.push(`<li>${inline(lines[index++].replace(/^\s*[-*]\s+/, ""))}</li>`);
            blocks.push(`<ul>${items.join("")}</ul>`);
        } else if (/^\s*\d+\.\s+/.test(line)) {
            const items = [];
            while (index < lines.length && /^\s*\d+\.\s+/.test(lines[index])) items.push(`<li>${inline(lines[index++].replace(/^\s*\d+\.\s+/, ""))}</li>`);
            blocks.push(`<ol>${items.join("")}</ol>`);
        } else {
            const paragraph = [line];
            index++;
            while (index < lines.length && lines[index].trim() && !/^(#{1,3}\s|\s*[-*]\s+|\s*\d+\.\s+|\s*```)/.test(lines[index])) paragraph.push(lines[index++]);
            blocks.push(`<p>${paragraph.map(inline).join("<br/>")}</p>`);
        }
    }
    return blocks.join("");
}

export const runIsActive = (run) => Boolean(run && ["queued", "running"].includes(run.status));
export const statusLabel = (status) => ({ queued: "Queued", running: "Working", completed: "Complete", needs_input: "Needs input", failed: "Failed", cancelled: "Stopped", interrupted: "Interrupted" })[status] || status || "";
export const formatDuration = (start, end) => {
    const seconds = Math.max(0, Math.round((new Date(end || Date.now()) - new Date(start)) / 1000)) || 0;
    return seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`;
};

export function mergeEventPage(existing, page) {
    const events = new Map(existing.map(event => [event.seq, event]));
    for (const event of page.events || []) {
        if (Number.isInteger(event.seq) && event.seq > 0) events.set(event.seq, event);
    }
    const ordered = [...events.values()].sort((a, b) => a.seq - b.seq);
    return { events: ordered, next_seq: Math.max(page.next_seq || 0, ordered.at(-1)?.seq || 0) };
}

/** Build only activity supported by actual server events. */
export function activityEntries(run, events) {
    if (run.source_state === "unavailable") return [];
    const agents = [];
    const open = (id) => [...agents].reverse().find(agent => agent.name === id);
    for (const event of events) {
        if (event.type === "agent.started") agents.push({ key: event.seq, name: event.agent_id, status: "running", started_at: event.timestamp, tools: [] });
        if (event.type === "agent.completed") {
            const agent = open(event.agent_id);
            if (agent) Object.assign(agent, { status: "completed", finished_at: event.timestamp });
        }
        if (event.type === "tool.started") {
            const agent = open(event.agent_id);
            if (agent) agent.tools.push({ key: event.tool_call_id || event.seq, name: event.payload.name, arguments: event.payload.arguments, status: "running", started_at: event.timestamp });
        }
        if (["tool.completed", "tool.failed"].includes(event.type)) {
            const tool = agents.flatMap(agent => agent.tools).find(item => item.key === event.tool_call_id);
            if (tool) Object.assign(tool, { status: event.type === "tool.completed" ? "completed" : "failed", finished_at: event.timestamp, summary: event.payload.summary || event.payload.message || "", sources: event.payload.sources || [] });
        }
    }
    if (!runIsActive(run)) {
        for (const agent of agents) {
            if (agent.status === "running") { agent.status = run.status; agent.finished_at = run.finished_at; }
            for (const tool of agent.tools) {
                if (tool.status === "running") { tool.status = run.status; tool.finished_at = run.finished_at; }
            }
        }
    }
    return agents;
}

export function sourceTarget(source) {
    if (!["hr.job", "hr.applicant", "jobhunter.chat.case", "jobhunter.chat.artifact", "jobhunter.chat.answer"].includes(source.model) || !Number.isInteger(source.record_id) || source.record_id <= 0) return null;
    if (source.attachment_id !== undefined && source.attachment_id !== null) {
        if (!Number.isInteger(source.attachment_id) || source.attachment_id <= 0) return null;
        return { url: `/web/content/${source.attachment_id}?download=false` };
    }
    return { model: source.model, id: source.record_id };
}
