// Exercise the shipped Design with agent hand-off: which tasks offer it, where it sits,
// the prompt it copies and its manual-copy fallback. This is a DOM double; it verifies
// structure and behavior, and makes no layout claim.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const all = [];
const roots = new Map();
const classes = node => (node.className || "").split(/\s+/).filter(Boolean);
const matches = (node, selector) => selector.split(",").some(s => classes(node).includes(s.trim().replace(/^\./, "")));
function element(tag = "div") {
  const node = {tag, children: [], textContent: "", disabled: false, hidden: false, style: {},
    value: "", checked: false, required: false, dataset: {}, focused: false, selected: false,
    set innerHTML(_) { throw new Error("Task text must never be parsed as HTML"); },
    classList: {add() {}, remove() {}, toggle() {}},
    append(...children) { for (const child of children) {
      this.children.push(child); if (child && typeof child === "object") child.parentElement = this;
    } },
    replaceChildren(...children) { this.children = []; this.append(...children); },
    closest(selector) {
      for (let n = this; n; n = n.parentElement) if (matches(n, selector)) return n;
      return null;
    },
    querySelector(selector) { return descendants(this).slice(1).find(n => matches(n, selector)) || null; },
    setAttribute() {}, showModal() {}, close() {}, remove() {},
    focus() { this.focused = true; }, select() { this.selected = true; },
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
const source = fs.readFileSync(path.join(__dirname, "../src/task_mcp/viewer_assets/app.js"), "utf8");
vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
const run = code => vm.runInContext(code, context);
function descendants(node) {return [node, ...(node.children || []).flatMap(descendants)];}
const plain = value => JSON.parse(JSON.stringify(value));
const texts = node => descendants(node).map(n => n.textContent).join(" ");
const labels = node => descendants(node).filter(n => n.tag === "button").map(n => n.textContent || n.title);
const DESIGN = "Design with agent";

const BRIEF = "Design question: should stale workstreams be colour-coded by age or by last activity?";
const base = {project_id: "prj_bbbb2222", revision: 3, spec_revision: 2, status: "open", standing: "decision",
  workstream_ids: ["wst_aaaa1111"], prerequisites: [], blocked_by: [], attempts: [], gate_proposals: [],
  body: "Spec", acceptance_criteria: "Criteria", updated_at: "2026-10-07T00:00:00Z"};
const tasks = {
  brief: {...base, id: "colour-stale-workstreams", title: "Colour stale workstreams", unresolved_items: [{id: "q1", text: BRIEF}]},
  generic: {...base, id: "tsk_0123456789abcdef0123456789abcdef", title: "Fix the export", unresolved_items: [
    {id: "q1", text: "Which date format?"}, {id: "q2", text: "Keep the old column?"}]},
  idea: {...base, id: "dark-mode-toggle", title: "Dark mode toggle", workstream_ids: [], source: "user",
    unresolved_items: [{id: "q1", text: "Idea to process: turn into a proper brief or task with the user"}]},
  unassigned: {...base, id: "inbox-question", title: "Inbox question", workstream_ids: [],
    unresolved_items: [{id: "q1", text: "Which branch should this go to?"}]},
  plain: {...base, id: "no-questions", title: "No questions", standing: "open", unresolved_items: []},
  deferred: {...base, id: "deferred-task", title: "Deferred", status: "deferred", standing: "deferred",
    unresolved_items: [{id: "q1", text: "Still open?"}]},
  dropped: {...base, id: "dropped-task", title: "Dropped", status: "dropped", standing: "dropped",
    unresolved_items: [{id: "q1", text: "Still open?"}]},
  done: {...base, id: "done-task", title: "Done", status: "done", standing: "done", unresolved_items: []},
  signoff: {...base, id: "signoff-task", title: "Ready", standing: "signoff", unresolved_items: [],
    attempts: [{id: "attempt", revision: 2, spec_revision: 2, state: "passed", workstream_id: "wst_aaaa1111"}]},
};

async function test() {
  run(`state.project = "prj_bbbb2222"; state.stream = "wst_aaaa1111";
    state.streams = [{id: "wst_aaaa1111", project_id: "prj_bbbb2222", branch: "main"}];
    projectName = () => "Project"; streamName = () => "main"; ago = () => "now"; activity = () => node("div");
    markdown = text => node("p", text);
    toasts = []; toast = (text, error = false) => toasts.push({text, error});
    requests = []; api = async (action, payload) => { requests.push({action, payload}); return {}; };`);
  const render = t => {
    context.current = t;
    run("renderDetail(current)");
    const detail = get("detail");
    const banner = descendants(detail).find(n => classes(n).includes("next")) || null;
    const designs = descendants(detail).filter(n => n.tag === "button" && n.textContent === DESIGN);
    return {detail, banner, designs};
  };

  // Every open task with unresolved items offers exactly one Design with agent, beside the
  // Unresolved items heading and never in the next-step banner, whatever its membership.
  for (const key of ["brief", "generic", "idea", "unassigned"]) {
    const t = tasks[key];
    const {banner, designs} = render(t);
    assert.equal(designs.length, 1, key);
    const head = designs[0].parentElement;
    assert.ok(classes(head).includes("block-head") && classes(head).includes("prompt-host"), key);
    const heading = head.children.find(n => n.tag === "h3");
    assert.equal(texts(heading).trim(), "Unresolved items", key);
    assert.equal(head.parentElement.tag, "section", key);
    assert.ok(banner, key);
    assert.ok(!labels(banner).includes(DESIGN), `${key}: not duplicated in the next step`);
  }
  // Saved ideas keep their walkthrough; Unassigned tasks keep their placement action.
  assert.ok(labels(render(tasks.idea).banner).includes("Go through it with an agent"));
  const unassigned = render(tasks.unassigned).banner;
  assert.ok(labels(unassigned).includes("Add to main"));
  assert.match(texts(unassigned), /talk its unresolved items through with an agent first; that needs no workstream/);
  // A member with questions points at the heading's action instead of repeating it.
  assert.match(texts(render(tasks.generic).banner), /Design with agent, beside the questions, copies a prompt/);

  // Tasks without questions and closed tasks keep their existing next step and gain nothing.
  const expected = {plain: ["Open", []], deferred: ["Deferred", ["Resume"]], dropped: ["Dropped", ["Resume"]],
    done: ["Signed off", []], signoff: ["Ready for your sign-off", ["Sign off with an agent"]]};
  for (const [key, [title, buttons]] of Object.entries(expected)) {
    const {banner, designs} = render(tasks[key]);
    assert.equal(designs.length, 0, key);
    assert.equal(banner.children[0].children[0].textContent, title, key);
    assert.deepEqual(plain(labels(banner)), buttons, key);
  }
  // A closed task's questions are still shown for reading, without the hand-off.
  assert.match(texts(render(tasks.deferred).detail), /Still open\?/);

  // The prompt names the task exactly, asks for discussion and recorded decisions only,
  // routes briefs through task design and leaves other items to their context.
  for (const t of [tasks.brief, tasks.generic, tasks.idea]) {
    context.current = t;
    const prompt = run("designPrompt(current)");
    assert.ok(prompt.startsWith(`Design with me: ${t.id} — ${t.title}. `), prompt);
    assert.match(prompt, /Read its current specification and unresolved items/);
    assert.match(prompt, /work through each item with me, and record the decisions we agree on/);
    assert.match(prompt, /captured feature brief, design it with the task-design skill/);
    assert.match(prompt, /otherwise handle each item according to its own context, since not every question is a feature brief/);
    assert.match(prompt, /Discuss and record decisions only; do not implement anything\.$/);
    for (const q of t.unresolved_items) assert.ok(!prompt.includes(q.text), "The prompt names the task, not a copy of its questions");
  }
  context.current = tasks.generic;
  assert.ok(run("designPrompt(current)").includes("tsk_0123456789abcdef0123456789abcdef — Fix the export"));

  // Copying confirms and writes nothing.
  let {designs} = render(tasks.generic);
  context.navigator = {clipboard: {writeText: async text => { context.copied = text; }}};
  await designs[0].onclick({currentTarget: designs[0]});
  context.current = tasks.generic;
  assert.equal(context.copied, run("designPrompt(current)"));
  assert.deepEqual(plain(context.toasts.at(-1)), {text: "Copied. Paste it into your agent to start the design discussion.", error: false});
  assert.equal(context.requests.length, 0, "Copying the prompt writes nothing");

  // When the clipboard refuses, the prompt appears selected in the heading row, once.
  context.navigator = {clipboard: {writeText: async () => { throw new Error("NotAllowedError"); }}};
  ({designs} = render(tasks.brief));
  const head = designs[0].parentElement;
  await designs[0].onclick({currentTarget: designs[0]});
  const box = head.children.find(n => n.className === "prompt-copy");
  assert.ok(box, "fallback sits beside the heading");
  context.current = tasks.brief;
  assert.equal(box.value, run("designPrompt(current)"));
  assert.equal(box.readOnly, true);
  assert.ok(box.focused && box.selected);
  assert.match(context.toasts.at(-1).text, /Couldn't copy automatically.*selected/);
  assert.equal(context.toasts.at(-1).error, true);
  context.navigator = {};
  await designs[0].onclick({currentTarget: designs[0]});
  assert.equal(head.children.filter(n => n.className === "prompt-copy").length, 1);
  const banner = descendants(get("detail")).find(n => classes(n).includes("next"));
  assert.ok(!descendants(banner).some(n => n.className === "prompt-copy"), "the banner gains no fallback box");
  assert.equal(context.requests.length, 0, "A failed copy writes nothing either");
}
test().catch(error => {console.error(error); process.exitCode = 1;});
