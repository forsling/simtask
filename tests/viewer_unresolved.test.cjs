// Exercise the Unresolved items section: its counted heading, full current items in stored
// order through the safe Markdown renderer, list semantics, one Design with agent action and
// its absence when no current item exists. This is a DOM double; it makes no layout claim.
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
    value: "", checked: false, required: false, dataset: {}, attributes: {},
    set innerHTML(_) { throw new Error("Task text must never be parsed as HTML"); },
    classList: {add() {}, remove() {}, toggle() {}},
    append(...children) { for (let child of children) {
      if (typeof child === "string") child = Object.assign(element("#text"), {textContent: child});
      this.children.push(child); if (child && typeof child === "object") child.parentElement = this;
    } },
    replaceChildren(...children) { this.children = []; this.append(...children); },
    closest(selector) {
      for (let n = this; n; n = n.parentElement) if (matches(n, selector)) return n;
      return null;
    },
    querySelector(selector) { return descendants(this).slice(1).find(n => matches(n, selector)) || null; },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    showModal() {}, close() {}, remove() {}, focus() {}, select() {},
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
    createTextNode: text => Object.assign(element("#text"), {textContent: text}), addEventListener() {}},
  window: {addEventListener() {}}, setTimeout() {}, location: {hash: ""},
  sessionStorage: {getItem: () => ""}, history: {replaceState() {}},
});
const source = fs.readFileSync(path.join(__dirname, "../src/task_mcp/viewer_assets/app.js"), "utf8");
vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
const run = code => vm.runInContext(code, context);
function descendants(node) {return [node, ...(node.children || []).flatMap(descendants)];}
const texts = node => descendants(node).map(n => n.textContent).join("");
const find = (node, cls) => descendants(node).filter(n => classes(n).includes(cls));

const LONG = [
  "Should `list_tasks(view=\"unresolved_items\")` keep its **current** ordering, or sort by `updated_at`?",
  "",
  "Context: the board relies on `tsk_0123456789abcdef0123456789abcdef` staying first. " + "Long design detail. ".repeat(80),
  "",
  "- Option A: keep `stored order`",
  "- Option B: sort by age",
  "",
  "```",
  "store.add_unresolved(task_id, text)",
  "```",
  "",
  "Final paragraph that must not be truncated: END-OF-QUESTION.",
].join("\n");
const base = {project_id: "prj_bbbb2222", revision: 3, spec_revision: 2, status: "open", standing: "decision",
  workstream_ids: ["wst_aaaa1111"], prerequisites: [], blocked_by: [], attempts: [], gate_proposals: [],
  body: "Spec", acceptance_criteria: "Criteria", updated_at: "2026-10-07T00:00:00Z"};
const tasks = {
  one: {...base, id: "one-item", title: "One", unresolved_items: [{id: "q1", text: "Only question?"}]},
  ordered: {...base, id: "ordered-items", title: "Ordered", unresolved_items: [
    {id: "q9", text: "Third stored, shown first"}, {id: "q2", text: "Second"}, {id: "q5", text: "Last"}]},
  long: {...base, id: "long-item", title: "Long", unresolved_items: [{id: "q1", text: LONG}]},
  // An answered item is removed from unresolved_items; its history lives in Activity.
  answered: {...base, id: "answered-task", title: "Answered", standing: "open", unresolved_items: []},
  deferred: {...base, id: "deferred-task", title: "Deferred", status: "deferred", standing: "deferred",
    unresolved_items: [{id: "q1", text: "Still open?"}, {id: "q2", text: "And this?"}]},
};

run(`state.project = "prj_bbbb2222"; state.stream = "wst_aaaa1111";
  state.streams = [{id: "wst_aaaa1111", project_id: "prj_bbbb2222", branch: "main"}];
  projectName = () => "Project"; streamName = () => "main"; ago = () => "now";
  activity = () => el("details", "fold activity-stub");
  toasts = []; toast = () => {}; requests = []; api = async (action, payload) => { requests.push({action, payload}); return {}; };`);
const render = t => {
  context.current = t;
  run("renderDetail(current)");
  const detail = get("detail");
  const sections = find(detail, "unresolved");
  return {detail, section: sections[0] || null, count: sections.length};
};

// The heading names the section and counts the current items, visibly and for screen readers.
for (const t of [tasks.one, tasks.ordered, tasks.long, tasks.deferred]) {
  const {section, count} = render(t);
  assert.equal(count, 1, t.id);
  assert.equal(section.tag, "section");
  assert.ok(classes(section).includes("block"));
  const heading = descendants(section).find(n => n.tag === "h3");
  assert.equal(heading.id, "unresolved-title");
  assert.equal(section.attributes["aria-labelledby"], "unresolved-title");
  assert.equal(heading.children[0].textContent, "Unresolved items");
  const n = t.unresolved_items.length;
  const badge = find(heading, "question-count")[0];
  assert.equal(badge.textContent, String(n), t.id);
  assert.equal(badge.attributes["aria-hidden"], "true");
  assert.equal(find(heading, "sr-only")[0].textContent, n === 1 ? "(1 current item)" : `(${n} current items)`);
  // Items are an ordered list labelled by the heading, one list item each, in stored order.
  const list = descendants(section).find(n => n.tag === "ol" && classes(n).includes("questions"));
  assert.equal(list.attributes["aria-labelledby"], "unresolved-title");
  assert.equal(list.children.length, n);
  list.children.forEach((li, i) => {
    assert.equal(li.tag, "li");
    assert.ok(classes(li).includes("question"));
    assert.ok(classes(li.children[0]).includes("md"), "item text goes through the Markdown renderer");
    assert.ok(texts(li).includes(t.unresolved_items[i].text.split("\n")[0].replace(/[`*]/g, "")), `${t.id} item ${i}`);
  });
}
const order = render(tasks.ordered).section;
const itemTexts = descendants(order).filter(n => n.tag === "li").map(texts);
assert.deepEqual(itemTexts, ["Third stored, shown first", "Second", "Last"]);

// A long item is shown complete: every paragraph, list, code block and identifier, untruncated.
const longText = texts(render(tasks.long).section);
for (const part of ["list_tasks(view=\"unresolved_items\")", "tsk_0123456789abcdef0123456789abcdef", "Option A: keep ",
  "stored order", "store.add_unresolved(task_id, text)", "END-OF-QUESTION", "Long design detail. ".repeat(80).trim()]) {
  assert.ok(longText.includes(part), part);
}
const longItem = descendants(render(tasks.long).section).find(n => n.tag === "li");
assert.ok(descendants(longItem).some(n => n.tag === "pre"), "code blocks keep their formatting");
assert.ok(descendants(longItem).some(n => n.tag === "ul"), "lists keep their formatting");
assert.ok(descendants(longItem).filter(n => n.tag === "p").length >= 3, "paragraphs stay separate");
assert.ok(!descendants(longItem).some(n => n.tag === "details"), "not collapsed by default");

// No current items: no section at all, while Activity (where answered items live) stays.
const answered = render(tasks.answered);
assert.equal(answered.count, 0);
assert.ok(!texts(answered.detail).includes("Unresolved items"));
assert.equal(find(answered.detail, "activity-stub").length, 1);

// Exactly one Design with agent action for an open task, beside the heading; none when closed.
const designs = section => descendants(section).filter(n => n.tag === "button" && n.textContent === "Design with agent");
for (const t of [tasks.one, tasks.ordered, tasks.long]) {
  const {detail, section} = render(t);
  assert.equal(designs(detail).length, 1, t.id);
  const head = designs(section)[0].parentElement;
  assert.ok(classes(head).includes("block-head") && classes(head).includes("prompt-host"));
  assert.equal(head.children[0].tag, "h3", "the heading precedes the button in tab and reading order");
}
const closed = render(tasks.deferred);
assert.equal(designs(closed.detail).length, 0);
const closedHead = descendants(closed.section).find(n => classes(n).includes("block-head"));
assert.ok(!classes(closedHead).includes("prompt-host"));
assert.equal(context.requests.length, 0, "Rendering writes nothing");
console.log("viewer unresolved items tests passed");
