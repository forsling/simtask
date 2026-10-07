// Exercise the Spec and Activity tabs of task and group details against a fake paged
// "activity" read: tab semantics and keyboard, the current-result card and its selection,
// paging, empty, retry and refresh states, Open full result outside the loaded pages, and
// the removed Result/Other results/sign-off/rejection feeds. A DOM double; no layout claim.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const roots = new Map();
const doc = {activeElement: null};
function element(tag = "div") {
  const classes = new Set();
  const n = {tag, children: [], textContent: "", hidden: false, disabled: false, dataset: {}, attributes: {}, style: {},
    tabIndex: 0, parentElement: null,
    get className() { return [...classes].join(" "); },
    set className(v) { classes.clear(); String(v || "").split(/\s+/).filter(Boolean).forEach(c => classes.add(c)); },
    set innerHTML(_) { throw new Error("Task text must never be parsed as HTML"); },
    classList: {add: c => classes.add(c), remove: c => classes.delete(c), toggle: (c, on) => (on ?? !classes.has(c)) ? classes.add(c) : classes.delete(c), contains: c => classes.has(c)},
    append(...children) { for (let c of children) {
      if (typeof c === "string") c = Object.assign(element("#text"), {textContent: c});
      if (c.tag === "#fragment") { this.append(...c.children); continue; }
      this.children.push(c); c.parentElement = this;
    } },
    replaceChildren(...children) { this.children.forEach(c => { c.parentElement = null; }); this.children = []; this.append(...children); },
    setAttribute(k, v) { this.attributes[k] = String(v); },
    getAttribute(k) { return this.attributes[k]; },
    querySelector(selector) { return descendants(this).slice(1).find(d => matches(d, selector)) || null; },
    closest() { return null; },
    focus() { doc.activeElement = this; }, scrollIntoView() { doc.scrolled = this.dataset.attempt || this.dataset.key; }, remove() {}, select() {},
  };
  return n;
}
function matches(n, selector) {
  const m = selector.match(/^(\w+)?\[data-([\w-]+)="([^"]*)"\]$/);
  if (!m) throw new Error("Unsupported selector " + selector);
  const key = m[2].replace(/-(\w)/g, (_, c) => c.toUpperCase());
  return (!m[1] || n.tag === m[1]) && n.dataset[key] === m[3];
}
function descendants(n) { return [n, ...(n.children || []).flatMap(descendants)]; }
function byId(id) {
  if (roots.has(id)) return roots.get(id);
  for (const root of roots.values()) {
    const found = descendants(root).find(n => n.id === id);
    if (found) return found;
  }
  const root = element();
  root.id = id;
  roots.set(id, root);
  return root;
}
const context = vm.createContext({
  document: {getElementById: byId, createElement: element, createElementNS: (_, tag) => element(tag),
    createDocumentFragment: () => element("#fragment"), addEventListener() {}, querySelectorAll: () => [],
    get activeElement() { return doc.activeElement; }},
  window: {addEventListener() {}}, setTimeout() {}, location: {hash: "", pathname: "/"},
  sessionStorage: {getItem: () => "", setItem() {}}, history: {replaceState() {}, pushState() {}},
  console,
});
const source = fs.readFileSync(path.join(__dirname, "../src/task_mcp/viewer_assets/app.js"), "utf8");
vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
const run = code => vm.runInContext(code, context);
const text = n => descendants(n).map(d => d.textContent).join(" ");
const settle = () => new Promise(setImmediate);
const buttons = (n, label) => descendants(n).filter(d => d.tag === "button" && (label === undefined || d.textContent === label));
const detail = () => byId("detail");
const panel = key => byId("panel-" + key);
const entries = () => descendants(panel("activity")).filter(d => d.tag === "li" && d.classList.contains("entry"));

// ---- A fake server: per-object histories, newest first, paged like Store.task_activity.
const server = {histories: {}, attempts: {}, requests: [], fail: null, limited: new Set()};
function history(id) { return server.histories[id] || []; }
context.fakeApi = async (action, data) => {
  server.requests.push({action, ...data});
  if (server.fail && server.fail(action, data)) throw new Error("Network down");
  if (action === "attempt") {
    if (!server.attempts[data.attempt_id]) throw new Error("unknown_attempt");
    return server.attempts[data.attempt_id];
  }
  if (action !== "activity") throw new Error("Unexpected " + action);
  const all = history(data.task_id);
  let before = data.cursor ?? Infinity;
  if (data.target) before = all.find(e => e.attempt_id === data.target && e.kind === "result").sequence + 1;
  // A scan-limited cursor yields a short page (as when hidden events fill the scan bound).
  const limit = server.limited.has(data.cursor ?? "first") ? 3 : 20;
  const rest = all.filter(e => e.sequence < before);
  const items = rest.slice(0, limit);
  const page = {task_id: data.task_id, items, newest_sequence: all[0]?.sequence ?? null};
  if (rest.length > limit) page.next_cursor = items[items.length - 1].sequence;
  if (limit < 20 && rest.length > limit) page.scan_limited = true;
  return page;
};
const entry = (sequence, kind, extra = {}) => ({sequence, kind, actor: "agent", timestamp: "2026-10-01T00:00:00Z",
  summary: `${kind} ${sequence}`, ...extra});
const result = (sequence, attempt_id, extra = {}) => entry(sequence, "result", {attempt_id, workstream_id: "wst_main",
  workstream_name: "main", spec_revision: 2, state: "rework", implementer: "builder", summary: "Built " + attempt_id, ...extra});
function busyHistory() {
  // 45 entries: results at 200 (newest), 150 and 101 (far outside the first page).
  const out = [];
  for (let s = 200; s > 155; s--) out.push(entry(s, "question_added", {text: "Question " + s}));
  out[0] = result(200, "att_new", {state: "passed"});
  out[10] = result(190, "att_mid", {spec_revision: 1});
  out[44] = result(156, "att_old", {state: "rework", workstream_id: "wst_alt", workstream_name: "alt"});
  return out;
}
const attempt = (id, extra = {}) => ({id, task_id: "busy", workstream_id: "wst_main", spec_revision: 2, state: "passed",
  implementer: "builder", summary: "Built " + id, evidence: "Evidence of " + id, verification: "Verified " + id,
  artifacts: [{kind: "commit", reference: "abc1234"}], review_note: "Review of " + id, reviewer: "checker",
  concerns: [{kind: "design", text: "Concern on " + id, source: "reviewer", author: "checker"}], created_at: "2026-10-01T00:00:00Z", ...extra});
server.attempts = {att_new: attempt("att_new"), att_mid: attempt("att_mid", {spec_revision: 1, state: "rework"}),
  att_old: attempt("att_old", {workstream_id: "wst_alt", state: "rework"})};

run(`api = fakeApi; ago = () => "now"; projectName = () => "Project";
  state.project = "prj_1"; state.stream = "wst_main";
  state.streams = [{id: "wst_main", project_id: "prj_1", branch: "main"}, {id: "wst_alt", project_id: "prj_1", branch: "alt"}];
  toast = (m, error) => { if (error) throw new Error(m); };`);
const task = (id, extra = {}) => ({id, title: "Task " + id, object_type: "task", project_id: "prj_1", revision: 3,
  spec_revision: 2, status: "open", standing: "signoff", workstream_ids: ["wst_main"], prerequisites: [], blocked_by: [],
  unresolved_items: [], gate_proposals: [], body: "The specification", acceptance_criteria: "The criteria",
  user_request: "The request", source: "user", updated_at: "2026-10-01T00:00:00Z",
  attempts: [
    {...attempt("att_old", {workstream_id: "wst_alt", state: "rework"}), created_at: "2026-09-01T00:00:00Z"},
    {...attempt("att_mid", {spec_revision: 1, state: "rework"}), created_at: "2026-09-02T00:00:00Z"},
    {...attempt("att_new"), created_at: "2026-09-03T00:00:00Z"},
  ],
  signoff_decisions: [], latest_rejection: {source: "review", verdict: "rework", reasons: "Old rejection", attempt_id: "att_mid",
    workstream_id: "wst_main", spec_revision: 1, timestamp: "2026-09-02T00:00:00Z"}, ...extra});
const render = t => { context.current = t; run("renderDetail(current)"); };
const activityRequests = () => server.requests.filter(r => r.action === "activity");

async function main() {
  server.histories.busy = busyHistory();
  const busy = task("busy");

  // ---- Tabs: identity, status and next step above both; Spec selected by default.
  render(busy);
  const content = detail().children[1];
  const order = content.children.map(c => c.attributes.role || c.className);
  assert.deepEqual(order, ["detail-head", "next tone-go", "tablist", "tabpanel", "tabpanel"]);
  assert.ok(buttons(content.children[1], "Sign off with an agent").length, "Sign off with an agent stays above the tabs");
  const tablist = byId("detail-tabs");
  assert.equal(tablist.attributes["aria-label"], "Task details");
  const [specTab, activityTab] = tablist.children;
  assert.deepEqual([specTab.textContent, activityTab.textContent], ["Spec", "Activity"]);
  assert.equal(specTab.attributes.role, "tab");
  assert.equal(specTab.attributes["aria-selected"], "true");
  assert.equal(activityTab.attributes["aria-selected"], "false");
  assert.equal(specTab.attributes["aria-controls"], "panel-spec");
  assert.equal(panel("spec").attributes["aria-labelledby"], "tab-spec");
  assert.deepEqual([specTab.tabIndex, activityTab.tabIndex], [0, -1]);
  assert.deepEqual([panel("spec").hidden, panel("activity").hidden], [false, true]);
  assert.equal(activityRequests().length, 0, "Activity loads only when shown");

  // ---- Spec: what was asked, the one current result; no competing result feeds.
  const spec = text(panel("spec"));
  for (const part of ["Specification", "The specification", "Acceptance criteria", "The criteria", "The request", "Current result"])
    assert.ok(spec.includes(part), part);
  for (const gone of ["Latest rejection", "Old rejection", "Other results", "Sign-off decisions", "Evidence of", "Verified ", "Review of", "Concern on"])
    assert.ok(!spec.includes(gone), gone);
  const card = descendants(panel("spec")).find(d => d.classList.contains("result-card"));
  assert.match(text(card), /Passed independent review; waiting for your sign-off/);
  assert.match(text(card), /Built att_new/);
  assert.match(text(card), /main/);
  assert.match(text(card), /spec v2 · current/);
  assert.match(text(card), /commit\s+abc1234/, "evidence handles as recorded");
  assert.equal(descendants(panel("spec")).filter(d => d.classList.contains("result-card")).length, 1);

  // ---- Keyboard: arrows, Home and End move between tabs and select them.
  const key = (tab, k) => { let prevented = false; tab.onkeydown({key: k, preventDefault() { prevented = true; }, stopPropagation() {}}); return prevented; };
  assert.equal(key(specTab, "ArrowRight"), true);
  await settle();
  assert.equal(byId("tab-activity").attributes["aria-selected"], "true");
  assert.equal(doc.activeElement, byId("tab-activity"));
  assert.deepEqual([panel("spec").hidden, panel("activity").hidden], [true, false]);
  assert.equal(key(byId("tab-activity"), "Home"), true);
  assert.equal(byId("tab-spec").attributes["aria-selected"], "true");
  assert.equal(key(byId("tab-spec"), "End"), true);
  assert.equal(byId("tab-activity").attributes["aria-selected"], "true");
  assert.equal(key(byId("tab-activity"), "ArrowRight"), true, "wraps around");
  assert.equal(byId("tab-spec").attributes["aria-selected"], "true");
  assert.equal(key(byId("tab-spec"), "x"), false);
  await settle();

  // ---- Activity: first page of 20, Load more 20 at a time, hidden when exhausted.
  byId("tab-activity").onclick();
  await settle();
  assert.equal(activityRequests().length, 1, "first page read once");
  assert.equal(entries().length, 20);
  assert.equal(entries()[0].dataset.attempt, "att_new");
  let shown = entries().map(e => Number(e.dataset.key.split(":")[0]));
  assert.deepEqual(shown, [...shown].sort((a, b) => b - a), "newest first");
  const newest = entries()[0];
  assert.match(text(newest), /Result/);
  assert.match(text(newest), /main · spec v2 · current/);
  assert.match(text(newest), /Current result/);
  assert.equal(buttons(panel("activity"), "Load more").length, 1);
  buttons(panel("activity"), "Load more")[0].onclick();
  await settle();
  assert.equal(entries().length, 40);
  assert.equal(activityRequests().at(-1).cursor, 181);
  assert.equal(doc.activeElement?.dataset.key, entries()[20].dataset.key, "focus moves to the first new entry");
  const mid = entries().find(e => e.dataset.attempt === "att_mid");
  assert.match(text(mid), /spec v1 · superseded \(now v2\)/);
  buttons(panel("activity"), "Load more")[0].onclick();
  await settle();
  assert.equal(entries().length, 45);
  assert.equal(buttons(panel("activity"), "Load more").length, 0, "Load more hidden when exhausted");
  assert.match(text(panel("activity")), /Beginning of history/);
  const old = entries().at(-1);
  assert.match(text(old), /alt \(other workstream\) · spec v2 · current/);
  assert.equal(new Set(entries().map(e => e.dataset.key)).size, 45, "each entry once");

  // ---- Opening a result reads its complete proof with the attempt read.
  const before = server.requests.length;
  buttons(old, "Show full result")[0].onclick();
  await settle();
  assert.deepEqual(server.requests.slice(before).map(r => [r.action, r.attempt_id]), [["attempt", "att_old"]]);
  let opened = entries().at(-1);
  for (const part of ["Evidence of att_old", "Verified att_old", "commit: abc1234", "Review of att_old", "Concern on att_old"])
    assert.ok(text(opened).includes(part), part);
  assert.equal(buttons(opened, "Hide full result")[0].attributes["aria-expanded"], "true");

  // ---- A background refresh keeps the tab, loaded entries and opened results; newer
  // entries are offered, not forced, and merge without duplicates.
  const reads = activityRequests().length;
  server.histories.busy = [result(202, "att_newer", {state: "review"}), entry(201, "review", {verdict: "pass", reviewer: "checker", attempt_id: "att_new", workstream_id: "wst_main", spec_revision: 2}), ...server.histories.busy];
  run("detailView.feed.checkedAt = 0");
  render({...busy, revision: 4});
  await settle();
  assert.equal(byId("tab-activity").attributes["aria-selected"], "true", "tab kept");
  assert.equal(entries().length, 45, "loaded entries kept");
  assert.ok(text(entries().at(-1)).includes("Evidence of att_old"), "opened result kept");
  assert.equal(activityRequests().length, reads + 1, "one quiet check for newer entries");
  assert.match(text(panel("activity")), /Newer activity is available/);
  buttons(panel("activity"), "Show newer")[0].onclick();
  await settle();
  assert.equal(entries().length, 47);
  assert.equal(entries()[0].dataset.attempt, "att_newer");
  assert.equal(new Set(entries().map(e => e.dataset.key)).size, 47);
  assert.doesNotMatch(text(panel("activity")), /Newer activity is available|in between/);

  // ---- A failing page keeps what is loaded and offers Retry in place.
  server.histories.retry = busyHistory();
  render(task("retry"));
  assert.equal(byId("tab-spec").attributes["aria-selected"], "true", "another object opens on Spec");
  byId("tab-activity").onclick();
  await settle();
  server.fail = (action, data) => action === "activity" && data.cursor === 181;
  buttons(panel("activity"), "Load more")[0].onclick();
  await settle();
  assert.equal(entries().length, 20, "loaded history kept");
  assert.match(text(panel("activity")), /Couldn't load more entries: Network down/);
  server.fail = null;
  buttons(panel("activity"), "Retry")[0].onclick();
  await settle();
  assert.equal(entries().length, 40);
  assert.doesNotMatch(text(panel("activity")), /Couldn't load/);
  // A failing first page offers Retry too.
  server.histories.first = busyHistory();
  render(task("first"));
  server.fail = action => action === "activity";
  byId("tab-activity").onclick();
  await settle();
  assert.match(text(panel("activity")), /Couldn't load activity: Network down/);
  server.fail = null;
  buttons(panel("activity"), "Retry")[0].onclick();
  await settle();
  assert.equal(entries().length, 20);

  // ---- Empty history.
  server.histories.empty = [];
  render(task("empty", {attempts: [], standing: "open"}));
  byId("tab-activity").onclick();
  await settle();
  assert.equal(entries().length, 0);
  assert.match(text(panel("activity")), /No activity yet/);
  assert.equal(buttons(panel("activity"), "Load more").length, 0);

  // ---- A short scan-limited page continues automatically to a full page.
  server.histories.limited = busyHistory();
  server.limited = new Set(["first"]);
  render(task("limited"));
  byId("tab-activity").onclick();
  await settle();
  assert.equal(entries().length, 20, "short scan-limited page followed");
  assert.deepEqual(activityRequests().slice(-2).map(r => r.cursor), [undefined, 198]);
  server.limited = new Set();

  // ---- Open full result outside the loaded page: the target page only, a gap above it.
  server.histories.target = busyHistory();
  const targetTask = task("target", {standing: "progress", attempts: [
    {...attempt("att_old", {state: "rework"}), workstream_id: "wst_main", created_at: "2026-09-03T00:00:00Z"}]});
  server.histories.target[44] = result(156, "att_old", {state: "rework"});
  render(targetTask);
  const targetCard = descendants(panel("spec")).find(d => d.classList.contains("result-card"));
  assert.match(text(targetCard), /With the agents: sent back for changes/);
  const start = server.requests.length;
  buttons(targetCard, "Open full result")[0].onclick();
  await settle(); await settle();
  const calls = server.requests.slice(start).map(r => r.action === "activity" ? `activity:${r.target || r.cursor || "first"}` : `${r.action}:${r.attempt_id}`);
  assert.deepEqual(calls, ["activity:first", "activity:att_old", "attempt:att_old"], "no intervening pages are read");
  assert.equal(byId("tab-activity").attributes["aria-selected"], "true");
  const focused = doc.activeElement;
  assert.equal(focused.dataset.attempt, "att_old");
  assert.ok(doc.scrolled === "att_old" && focused.classList.contains("targeted"));
  assert.ok(text(focused).includes("Evidence of att_old"), "opened with its full proof");
  assert.equal(entries().length, 21);
  const gap = descendants(panel("activity")).filter(d => d.classList.contains("gap"));
  assert.equal(gap.length, 1);
  buttons(gap[0], "Load entries in between")[0].onclick();
  await settle();
  assert.equal(activityRequests().at(-1).cursor, 181);
  assert.equal(entries().length, 41);
  buttons(descendants(panel("activity")).find(d => d.classList.contains("gap")), "Load entries in between")[0].onclick();
  await settle();
  assert.equal(entries().length, 45, "the gap closes");
  assert.equal(descendants(panel("activity")).filter(d => d.classList.contains("gap")).length, 0);
  assert.equal(new Set(entries().map(e => e.dataset.key)).size, 45);
  // A loaded result is focused in place without another page read.
  const reads2 = activityRequests().length;
  run("showTab('spec')");
  buttons(panel("spec"), "Open full result")[0].onclick();
  await settle();
  assert.equal(activityRequests().length, reads2);
  assert.equal(doc.activeElement.dataset.attempt, "att_old");

  // ---- Card selection: completed task shows the accepted result; no card without one.
  render(task("done", {status: "done", standing: "done", selected_attempt_id: "att_mid"}));
  const doneCard = descendants(panel("spec")).find(d => d.classList.contains("result-card"));
  assert.match(text(doneCard), /Accepted at sign-off/);
  assert.match(text(doneCard), /spec v1 · superseded/);
  render(task("plain", {standing: "open", attempts: [{...attempt("att_x", {state: "passed", spec_revision: 1})}]}));
  assert.ok(!descendants(panel("spec")).some(d => d.classList.contains("result-card")), "an older passed result stays in Activity");

  // ---- Groups: Spec keeps members and progress; Activity reads the group's own feed.
  server.histories.grp = [entry(30, "member_added", {member_id: "m1", title: "Member one", via: "create_task"}),
    entry(10, "created", {summary: "Group created: G"})];
  run("includedWorkstreams = async () => []");
  const group = {id: "grp", title: "Group", object_type: "group", body: "Context", acceptance_criteria: "Done", complete: false,
    progress: {total: 1, done: 0, by_project: {prj_1: {total: 1, done: 0}}}, member_details: [{id: "m1", title: "Member one", project_id: "prj_1", status: "open"}],
    included_workstreams: [], attempts: [], spec_revision: 1};
  render(group);
  assert.equal(byId("detail-tabs").attributes["aria-label"], "Group details");
  assert.equal(byId("tab-spec").attributes["aria-selected"], "true");
  assert.match(text(panel("spec")), /Members/);
  assert.match(text(panel("spec")), /Member one/);
  assert.match(text(panel("spec")), /Done when/);
  byId("tab-activity").onclick();
  await settle();
  assert.equal(activityRequests().at(-1).task_id, "grp");
  assert.equal(entries().length, 2);
  assert.match(text(entries()[0]), /Member added.*Member one \(m1\) · joined when it was created/);
}
main().catch(error => { console.error(error); process.exitCode = 1; });
