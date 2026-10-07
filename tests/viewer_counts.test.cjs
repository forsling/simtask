// The shipped viewer on real server data (written by tests/test_standings.py): every
// workstream's sidebar count equals the rows its Signoff and Design sections show, on
// its own board, on All tasks and on a group board. Run through pytest, which passes
// the fixture path.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
if (!process.argv[2]) {
  console.log("viewer counts skipped: run through pytest tests/test_standings.py");
  process.exit(0);
}
const fixture = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const all = [];
function element(tag = "div") {
  const node = {tag, children: [], textContent: "", hidden: false, draggable: false, value: "",
    dataset: {}, style: {}, className: "", title: "", parentElement: null,
    get classList() {
      const self = this, names = () => self.className.split(/\s+/).filter(Boolean);
      const list = {
        add: (...c) => { self.className = [...new Set([...names(), ...c])].join(" "); },
        remove: (...c) => { self.className = names().filter(n => !c.includes(n)).join(" "); },
        toggle: (c, on) => { (on ?? !names().includes(c)) ? list.add(c) : list.remove(c); },
        contains: c => names().includes(c),
      };
      return list;
    },
    append(...children) { for (const child of children) {
      this.children.push(child); if (child && typeof child === "object") child.parentElement = this;
    } },
    prepend(...children) { this.append(...children); },
    replaceChildren(...children) { this.children = []; this.append(...children); },
    setAttribute() {}, querySelector() { return null; }, scrollIntoView() {}, showModal() {}, close() {}, remove() {},
  };
  all.push(node);
  return node;
}
const roots = new Map();
function get(id) {
  const found = [...all].reverse().find(node => node.id === id);
  if (found) return found;
  if (!roots.has(id)) roots.set(id, element());
  return roots.get(id);
}
const context = vm.createContext({
  document: {getElementById: get, createElement: element, createElementNS: (_, tag) => element(tag), createDocumentFragment: element,
    createTextNode: text => Object.assign(element(), {textContent: text}), addEventListener() {}, querySelectorAll: () => []},
  window: {addEventListener() {}}, setTimeout() {}, location: {hash: ""},
  sessionStorage: {getItem: () => ""}, history: {replaceState() {}, pushState() {}},
});
const source = fs.readFileSync(path.join(__dirname, "../src/task_mcp/viewer_assets/app.js"), "utf8");
vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
const run = code => vm.runInContext(code, context);
const kids = n => (n.children || []).filter(c => c && typeof c === "object");
const descendants = n => [n, ...kids(n).flatMap(descendants)];
const text = n => typeof n === "string" ? n : n.textContent + (n.children || []).map(text).join("");

context.fixture = fixture;
context.requests = [];
run(`
  state.project = fixture.project; state.projects = fixture.projects; state.streams = fixture.workstreams.items;
  selectTask = async () => {}; toast = (m) => { throw new Error("unexpected notice: " + m); };
  api = async (action, payload) => {
    requests.push(action);
    if (action === "workstreams") return fixture.workstreams;
    if (action === "tasks") return fixture.boards[payload.workstream_id || ""];
    if (action === "groups") return fixture.groups;
    throw new Error("unexpected " + action);
  };
`);
const streams = fixture.workstreams.items;
const navCount = id => {
  const s = streams.find(x => x.id === id);
  const item = descendants(get("nav")).find(n => n.classList.contains("nav-item") && text(n).startsWith(s.branch || s.name));
  const count = kids(item).find(n => n.classList.contains("count"));
  return count.classList.contains("attention") ? Number(count.textContent) : 0;
};
const inputRows = () => {
  let inInput = false, n = 0;
  for (const node of kids(get("list"))) {
    if (node.classList.contains("section-head")) inInput = /^(Signoff|Design)/.test(text(node));
    else if (inInput && node.classList.contains("row")) n++;
  }
  return n;
};
async function main() {
  const shown = {};
  for (const s of streams) {
    run(`state.groups = false; state.stream = ${JSON.stringify(s.id)};`);
    await run("reload()");
    assert.ok(run("state.rows.every(r => STANDINGS[r.standing])"), "every card carries a known standing");
    shown[s.branch] = inputRows();
    assert.equal(navCount(s.id), shown[s.branch], `${s.branch}: sidebar equals its Signoff and Design sections`);
  }
  assert.deepEqual(shown, fixture.expected);
  // Other boards show the same counts, read with the workstream list only.
  for (const [groups, stream] of [[false, null], ["project", null]]) {
    context.requests.length = 0;
    run(`state.groups = ${JSON.stringify(groups)}; state.stream = ${JSON.stringify(stream)};`);
    await run("reload()");
    for (const s of streams) assert.equal(navCount(s.id), shown[s.branch], `${s.branch} from ${groups || "All tasks"}`);
    assert.equal(context.requests.filter(a => a === "tasks").length, groups ? 0 : 1, "no card list is read to count");
  }
  console.log("viewer counts ok");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
