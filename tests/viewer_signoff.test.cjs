// Exercise the shipped sign-off hand-off, status controls and history rendering with
// compact server ACKs. This verifies form/payload behavior; it makes no layout claim.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const all = [];
const roots = new Map();
function element(tag = "div") {
  const node = {tag, children: [], textContent: "", disabled: false, hidden: false, style: {},
    value: "", checked: false, required: false, dataset: {},
    get lastChild() { return this.children.at(-1); },
    set innerHTML(_) { throw new Error("Task text must never be parsed as HTML"); },
    classList: {add() {}, remove() {}, toggle() {}},
    append(...children) { for (const child of children) {
      this.children.push(child); if (child && typeof child === "object") child.parentElement = this;
    } },
    replaceChildren(...children) { this.children = []; this.append(...children); },
    setAttribute() {}, querySelector() { return null; }, showModal() {}, close() {}, remove() {}, focus() {}, select() {},
  };
  all.push(node);
  return node;
}
function get(id) {
  const found = [...all].reverse().find(node => node.id === id);
  if (found) return found;
  if (!roots.has(id)) roots.set(id, element());
  return roots.get(id);
}
const context = vm.createContext({
  document: {getElementById: get, createElement: element, createElementNS: (_, tag) => element(tag), createDocumentFragment: element,
    createTextNode: text => Object.assign(element(), {textContent: text}), addEventListener() {}},
  window: {addEventListener() {}}, setTimeout() {}, location: {hash: ""},
  sessionStorage: {getItem: () => ""}, history: {replaceState() {}},
});
const source = fs.readFileSync(path.join(__dirname, "../src/simtask/viewer_assets/app.js"), "utf8");
vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
const run = code => vm.runInContext(code, context);
function descendants(node) {return [node, ...(node.children || []).flatMap(descendants)];}
// Sign-off decisions and rejections live in Activity, not on Spec: a sign-off entry shows
// the full reasons and historical judgments of the task's recorded decision, as text.
async function testActivity() {
  run(`markdown = text => node("p", text); ago = () => "now"; streamName = () => "main";`);
  context.view = {task: {id: "history", object_type: "task", status: "open", spec_revision: 4, attempts: [], signoff_decisions: [
    {decision_ref: 11, decision: "defer", disposition: "deferred", purpose_judgment: "deferred", purpose_source: "user_verdict",
      result_judgment: "accepted", user_note: "Old defer reasons", result_note: "Old quality note"},
    {decision_ref: 12, decision: "drop", disposition: "dropped", reasons: 'New <img src=x onerror="alert(1)"> drop reasons'}]},
    feed: {open: new Map()}};
  const at = (sequence, kind, extra) => ({sequence, kind, actor: "actor-" + sequence, timestamp: "2026-10-04T00:00:00Z", ...extra});
  context.entries = [
    at(12, "signoff", {decision: "drop", disposition: "dropped", reasons: "Clipped", attempt_id: "a1", workstream_id: "w", spec_revision: 4}),
    at(11, "signoff", {decision: "defer", disposition: "deferred", reasons: "Old defer", attempt_id: "a1", workstream_id: "w", spec_revision: 4}),
    at(10, "signoff", {decision: "rework", disposition: "open", reasons: "Only in the entry", attempt_id: "a1", workstream_id: "w", spec_revision: 3}),
    at(9, "review", {verdict: "rework", reviewer: "Rev", reasons: "Reviewer reasons", attempt_id: "origin-attempt",
      workstream_id: "origin-branch", workstream_name: "origin-branch", spec_revision: 3}),
  ];
  const rows = run("entries.map(e => activityEntry(e, view))");
  const text = row => descendants(row).map(n => n.textContent).join(" ");
  assert.deepEqual(rows.map(r => r.children[0].children[0].textContent),
    ["Sign-off decision", "Sign-off decision", "Sign-off decision", "Independent review"]);
  assert.match(text(rows[0]), /Dropped/);
  assert.match(text(rows[0]), /New <img src=x onerror="alert\(1\)"> drop reasons/, "full recorded reasons, as text");
  assert.doesNotMatch(text(rows[0]), /Clipped/);
  assert.match(text(rows[1]), /Old defer reasons/);
  assert.match(text(rows[1]), /Historical purpose: deferred \(user verdict\)\./);
  assert.match(text(rows[1]), /Historical human result quality: accepted\./);
  assert.match(text(rows[1]), /Old quality note/);
  assert.match(text(rows[2]), /Only in the entry/);
  assert.match(text(rows[2]), /spec v3 · superseded \(now v4\)/);
  assert.match(text(rows[3]), /Sent back for changes/);
  assert.match(text(rows[3]), /by Rev/);
  assert.match(text(rows[3]), /Reviewer reasons/);
  assert.match(text(rows[3]), /origin-branch · spec v3 · superseded/);
  // Each links to the result it judged.
  assert.ok(rows.every(r => descendants(r).some(n => n.tag === "button" && n.textContent === "Show the result")));
}
const plain = value => JSON.parse(JSON.stringify(value));
const texts = nodes => nodes.flatMap(descendants).map(node => node.textContent).join(" ");
async function test() {
  context.task = {id: "tsk_0123abcd", title: "Purpose", revision: 8, spec_revision: 4, project_id: "project",
    workstream_ids: ["main"], status: "open", unresolved_items: [], prerequisites: [], attempts: [
      {id: "attempt", revision: 2, spec_revision: 4, state: "passed", workstream_id: "main"}]};
  run(`state.stream = "main"; state.streams = [{id: "main", project_id: "project", branch: "main"}];
    toasts = []; toast = (text, error = false) => toasts.push({text, error});
    requests = []; api = async (action, payload) => {requests.push({action, payload}); return {id: "tsk_0123abcd", revision: 9};};
    reload = async () => { reloaded = (typeof reloaded === "number" ? reloaded : 0) + 1; };`);

  // Sign-off: one button that copies a ready prompt for an agent and says so.
  const panel = run('nextStep(task, "signoff")');
  const signoff = descendants(panel).find(node => node.textContent === "Sign off with an agent");
  assert.ok(signoff);
  assert.match(texts([panel]), /Sign-off is a walkthrough with an agent/);
  assert.equal(run("signoffPrompt(task)"), "Sign off tsk_0123abcd — Purpose");
  context.navigator = {clipboard: {writeText: async text => { context.copied = text; }}};
  await signoff.onclick({currentTarget: signoff});
  assert.equal(context.copied, "Sign off tsk_0123abcd — Purpose");
  assert.deepEqual(plain(context.toasts.at(-1)), {text: "Copied. Paste it into your agent to start the sign-off.", error: false});
  assert.equal(context.requests.length, 0, "Copying the prompt writes nothing");
  // Without the Clipboard API (or when it refuses), the prompt is shown selected instead.
  context.navigator = {clipboard: {writeText: async () => { throw new Error("NotAllowedError"); }}};
  const host = element(); host.querySelector = () => null;
  let selected = false;
  const anchor = {closest: selector => selector === ".next" ? host : null};
  const created = all.length;
  await run("copyPrompt")("Sign off tsk_0123abcd — Purpose", anchor);
  const box = all.slice(created).find(node => node.className === "prompt-copy");
  assert.ok(box && host.children.includes(box));
  assert.equal(box.value, "Sign off tsk_0123abcd — Purpose");
  assert.equal(box.readOnly, true);
  assert.match(context.toasts.at(-1).text, /Couldn't copy automatically.*selected/);
  context.navigator = {};
  host.querySelector = () => Object.assign(box, {select() { selected = true; }, focus() {}});
  await run("copyPrompt")("Sign off again", anchor);
  assert.equal(box.value, "Sign off again"); assert.ok(selected);

  // Ask a question: any live task (open or sent back for rework), one short input, no checkbox.
  const menu = status => {
    run(`task.status = ${JSON.stringify(status)}; actionsMenu({getBoundingClientRect: () => ({bottom: 0, right: 0})}, task);`);
    const items = get("popover").children.map(node => node.textContent);
    run("closeMenu()");
    return items;
  };
  assert.deepEqual(plain(menu("open")), ["Ask a question", "Add to workstream", "Remove from workstream", "Defer", "Drop"]);
  assert.deepEqual(plain(menu("rework")), ["Ask a question", "Add to workstream", "Remove from workstream", "Defer", "Drop"]);
  assert.deepEqual(plain(menu("deferred")), ["Add to workstream", "Remove from workstream", "Resume"]);
  assert.deepEqual(plain(menu("dropped")), ["Add to workstream", "Remove from workstream", "Resume"]);
  run("task.workstream_ids = []");
  assert.deepEqual(plain(menu("open")), ["Ask a question", "Add to main", "Defer", "Drop"]);
  run("task.workstream_ids = ['main']");
  run("task.status = 'open'; askQuestion(task);");
  let fields = descendants(get("fields"));
  assert.equal(fields.filter(node => node.type === "checkbox").length, 0);
  assert.equal(get("field-text").tag, "input");
  assert.equal(get("field-text").required, true);
  context.values = new Map([["text", "Which variant?"]]);
  await run("submitAction(values)");
  assert.deepEqual(plain(context.requests.at(-1)), {action: "question", payload: {task_id: "tsk_0123abcd", expected_revision: 8, text: "Which variant?"}});

  // Defer and resume (from Later) take effect at once with a stand-in reason.
  await run("changeStatus(task, 'deferred')");
  assert.deepEqual(plain(context.requests.at(-1).payload), {task_id: "tsk_0123abcd", expected_revision: 8, disposition: "deferred", note: "Deferred in the browser."});
  assert.match(context.toasts.at(-1).text, /Deferred/);
  run("task.status = 'deferred';");
  await run("resumeTask(task)");
  assert.deepEqual(plain(context.requests.at(-1).payload), {task_id: "tsk_0123abcd", expected_revision: 8, disposition: "open", note: "Resumed in the browser."});
  // A stale revision saves nothing and says so plainly.
  run(`api = async () => { throw Object.assign(new Error("revision_conflict"), {conflict: true}); };`);
  await run("changeStatus(task, 'open')");
  assert.deepEqual(plain(context.toasts.at(-1)), {text: "This task changed elsewhere, so nothing was saved. Showing its latest version; try again if you still want to.", error: true});
  run(`api = async (action, payload) => {requests.push({action, payload}); return {id: "tsk_0123abcd", revision: 9};};`);

  // Drop asks "are you sure" with an optional reason and no checkbox.
  run("task.status = 'open'; dropTask(task);");
  assert.equal(get("dialog-title").textContent, "Drop this task?");
  assert.match(get("dialog-description").textContent, /Are you sure/);
  fields = descendants(get("fields"));
  assert.equal(fields.filter(node => node.type === "checkbox").length, 0);
  assert.equal(get("field-note").required, false);
  context.values = new Map([["note", ""]]);
  await run("submitAction(values)");
  assert.deepEqual(plain(context.requests.at(-1).payload), {task_id: "tsk_0123abcd", expected_revision: 8, disposition: "dropped", note: "Dropped in the browser."});
  context.values = new Map([["note", " No longer needed "]]);
  await run("submitAction(values)");
  assert.equal(context.requests.at(-1).payload.note, "No longer needed");

  // Bringing back a dropped task asks why, which the Store keeps as its instruction.
  run("task.status = 'dropped'; resumeTask(task);");
  assert.equal(get("field-note").required, true);
  assert.equal(descendants(get("fields")).filter(node => node.type === "checkbox").length, 0);
  context.values = new Map([["note", "   "]]);
  const count = context.requests.length;
  await assert.rejects(run("submitAction(values)"), error => error.local && /coming back/.test(error.message));
  assert.equal(context.requests.length, count);
  context.values = new Map([["note", "Needed for the release after all"]]);
  await run("submitAction(values)");
  assert.deepEqual(plain(context.requests.at(-1).payload), {task_id: "tsk_0123abcd", expected_revision: 8, disposition: "open",
    note: "Needed for the release after all", authorization: "Needed for the release after all"});

  // Spec no longer carries sign-off history or the latest rejection: those are in Activity.
  run(`task.status = "open"; task.unresolved_items = [{id: "q", text: "Open question"}]; task.blocked_by = []; task.gate_proposals = [];
    task.body = "Spec"; task.acceptance_criteria = "Criteria"; markdown = text => node("p", text);
    task.signoff_decisions = [
      {decision: "defer", disposition: "deferred", purpose_judgment: "deferred", purpose_source: "user_verdict", result_judgment: "accepted", user_note: "Old defer reasons", result_note: "Old quality note"},
      {decision: "drop", disposition: "dropped", reasons: "New drop reasons"}];
    task.latest_rejection = {source: "review", verdict: "rework", reasons: "Reviewer reasons", attempt_id: "origin-attempt", workstream_id: "origin-branch", spec_revision: 4, timestamp: "then"};
    activity = () => node("div"); rendered = body(task);`);
  const renderedText = texts(context.rendered);
  assert.doesNotMatch(renderedText, /Old defer reasons|New drop reasons|Reviewer reasons|Sign-off decisions|Latest rejection/);
  assert.match(renderedText, /Open question/);
  // Questions are read, not answered, here: the only control hands them to an agent. The
  // Notes section is separate: its controls add and open notes, never task changes.
  const notes = context.rendered.filter(node => (node.className || "").split(" ").includes("notes-block"));
  assert.equal(notes.length, 1);
  assert.deepEqual(plain(context.rendered.filter(node => !notes.includes(node)).flatMap(descendants).filter(node => node.tag === "button").map(node => node.textContent)),
    ["Design with agent"]);

  // The detail offers no create, edit, review or verdict controls; a done task offers none at all.
  run("projectName = () => 'Project'; streamName = () => 'main'; ago = () => 'now';");
  const buttons = () => descendants(get("detail")).filter(node => node.tag === "button").map(node => node.textContent || node.title);
  for (const status of ["open", "deferred", "dropped"]) {
    run(`task.status = ${JSON.stringify(status)}; renderDetail(task);`);
    const shown = buttons();
    assert.ok(shown.includes("Actions"), status);
    assert.ok(!shown.some(label => /Edit|New|Answer|Record my review|Approve|Request changes|Next agent action/.test(label)), status);
  }
  run("task.status = 'done'; task.unresolved_items = []; renderDetail(task);");
  assert.ok(!buttons().includes("Actions"));
}
testActivity().then(test).catch(error => {console.error(error); process.exitCode = 1;});
