import test from "node:test";
import assert from "node:assert/strict";
import { renderMarkdown, mergeEventPage, activityEntries, sourceTarget } from "../static/src/chat_utils.js";

test("assistant formatting escapes source HTML and prevents active URLs", () => {
    const html = renderMarkdown('**Evidence**\n\n<script>alert(1)</script>\n[open](javascript:alert(1))\n<img src=x onerror=alert(1)>');
    assert.match(html, /<strong>Evidence<\/strong>/);
    assert.ok(!html.includes("<script>"));
    assert.ok(!html.includes("<img"));
    assert.ok(!html.includes('href="javascript:'));
    assert.match(html, /&lt;script&gt;/);
});

test("code blocks and inline code preserve literal markup", () => {
    const html = renderMarkdown('Use `**literal**`\n\n```python\nprint("<tag>")\n```\n\n- One\n- Two');
    assert.match(html, /<code>\*\*literal\*\*<\/code>/);
    assert.match(html, /<pre><code>print\(&quot;&lt;tag&gt;&quot;\)/);
    assert.match(html, /<ul><li>One<\/li><li>Two<\/li><\/ul>/);
});

test("event replay deduplicates and sorts actual sequence numbers", () => {
    const one = { seq: 1, type: "agent.started", agent_id: "Orchestrator", timestamp: "2026-10-31T03:00:00Z", payload: {} };
    const two = { seq: 2, type: "tool.started", agent_id: "Orchestrator", tool_call_id: "c1", timestamp: "2026-10-31T03:00:01Z", payload: { name: "search_jobs", arguments: { query: "Engineer" } } };
    const result = mergeEventPage([two], { events: [two, one], next_seq: 2 });
    assert.deepEqual(result.events.map(e => e.seq), [1, 2]);
    assert.equal(result.next_seq, 2);
    assert.equal(activityEntries({ status: "completed" }, []).length, 0);
});

test("terminal state settles unfinished activities without inventing steps", () => {
    const events = [
        { seq: 1, type: "agent.started", agent_id: "Orchestrator", timestamp: "2026-10-31T03:00:00Z", payload: {} },
        { seq: 2, type: "tool.started", agent_id: "Orchestrator", tool_call_id: "c1", timestamp: "2026-10-31T03:00:01Z", payload: { name: "get_job", arguments: { job_id: 3 } } },
    ];
    const entries = activityEntries({ status: "cancelled", finished_at: "2026-10-31T03:00:03Z" }, events);
    assert.equal(entries.length, 1);
    assert.equal(entries[0].status, "cancelled");
    assert.equal(entries[0].tools[0].status, "cancelled");
    assert.equal(entries[0].tools[0].name, "get_job");
    assert.deepEqual(activityEntries({ status: "completed", source_state: "unavailable" }, events), []);
});

test("source navigation derives safe destinations from verified references", () => {
    assert.deepEqual(sourceTarget({ model: "hr.applicant", record_id: 17, url: "javascript:alert(1)" }), { model: "hr.applicant", id: 17 });
    assert.deepEqual(sourceTarget({ model: "hr.job", record_id: 2, attachment_id: 8 }), { url: "/web/content/8?download=false" });
    assert.equal(sourceTarget({ model: "res.users", record_id: 1 }), null);
    assert.equal(sourceTarget({ model: "hr.job", record_id: -1 }), null);
    assert.equal(sourceTarget({ model: "hr.job", record_id: 2, attachment_id: "8\" onclick=bad" }), null);
});
