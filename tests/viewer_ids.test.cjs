// Run the shipped task-ID UI against a DOM double, including clipboard fallback.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const roots = new Map(), notices = [], copied = [];
function element(tag = "div") {
  return {tag, textContent: "", children: [], attributes: {}, dataset: {}, style: {}, value: "",
    classList: {add() {}, remove() {}, toggle() {}},
    append(...children) {this.children.push(...children);},
    replaceChildren(...children) {this.children = children;},
    setAttribute(key, value) {this.attributes[key] = value;},
  };
}
const get = id => {if (!roots.has(id)) roots.set(id, element()); return roots.get(id);};
let selected;
const listeners = {};
const range = {selectNodeContents(label) {selected = label;}};
const selection = {removeAllRanges() {}, addRange(r) {assert.equal(r, range);}};
const context = vm.createContext({
  document: {getElementById: get, createElement: element, createRange: () => range,
    createElementNS: (_, tag) => element(tag), addEventListener() {}},
  window: {addEventListener(name, fn) {listeners[name] = fn;}, getSelection: () => selection},
  navigator: {clipboard: {async writeText(text) {copied.push(text);}}},
  location: {hash: ""}, history: {replaceState() {}}, sessionStorage: {getItem: () => ""}, setTimeout() {},
});
const source = fs.readFileSync(path.join(__dirname, "../src/simtask/viewer_assets/app.js"), "utf8");
vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
const run = code => vm.runInContext(code, context);
context.noticeSink = message => notices.push(message);
run("toast = noticeSink; topBar = () => null; nextStep = () => null; body = () => []; projectName = () => 'Project';");
const descendants = node => node && typeof node === "object" ? [node, ...(node.children || []).flatMap(descendants)] : [];
async function main() {
  for (const id of ["tsk_0123456789abcdef0123456789abcdef", "restore-readable-task-references-across-design-and-signoff-and-the-browser-viewer"]) {
    context.task = {id, title: "A title", standing: "ready", object_type: "task", status: "open", workstream_ids: [], unresolved_items: []};
    const card = run("row(task)");
    const labels = descendants(card).filter(n => n.className === "task-id");
    assert.equal(labels.length, 1);
    assert.equal(labels[0].textContent, id);
    assert.equal(labels[0].draggable, false);
    let stopped = false;
    labels[0].onclick({stopPropagation() {stopped = true;}});
    assert.ok(stopped);
    labels[0].closest = () => card;
    card.draggable = true;
    labels[0].onmousedown({stopPropagation() {}});
    assert.equal(card.draggable, false);
    listeners.mouseup();
    assert.equal(card.draggable, true);
    run("renderDetail(task)");
    const headerLabels = descendants(get("detail")).filter(n => n.className === "task-id");
    assert.equal(headerLabels.length, 1);
    assert.equal(headerLabels[0].textContent, id);
    const header = run("taskIdHeader(task.id)");
    await header.children[1].onclick({});
    assert.equal(copied.at(-1), id);
    context.navigator.clipboard.writeText = async () => {throw Error("Unavailable");};
    await header.children[1].onclick({});
    assert.equal(selected.textContent, id);
    assert.match(notices.at(-1), /selected/);
    context.navigator.clipboard.writeText = async text => copied.push(text);
    assert.equal(run("signoffPrompt(task)"), `Sign off ${id} — A title`);
  }
  run('state.rows = [{id: "reference-storage", title: "Unrelated title"}];');
  get("search").value = "reference-storage";
  assert.equal(run("filteredRows().length"), 1);
  const css = fs.readFileSync(path.join(__dirname, "../src/simtask/viewer_assets/style.css"), "utf8");
  assert.match(css, /\.task-id\s*\{[^}]*overflow-wrap:\s*anywhere[^}]*user-select:\s*text/s);
  assert.match(css, /\.task-id-header\s*\{[^}]*flex-wrap:\s*wrap/s);
  console.log("viewer public ID tests passed");
}
main().catch(error => {console.error(error); process.exit(1);});
