// The shipped DEV marking against a DOM double: a viewer whose /api/ping says its
// database is not the live one shows the DEV badge in the list header (and, for phones,
// in the detail top bar) with the path in its tooltip and prefixes the tab title; the
// live database changes nothing. This is a rendering regression, not a layout test.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
function element(tag = "div") {
  return {tag, textContent: "", children: [], attributes: {}, dataset: {}, style: {}, className: "", title: "", hidden: false,
    classList: {add() {}, remove() {}, toggle() {}},
    append(...children) {this.children.push(...children.filter(c => c !== null && c !== undefined && c !== false));},
    replaceChildren(...children) {this.children = []; this.append(...children);},
    setAttribute(key, value) {this.attributes[key] = value;},
  };
}
const descendants = node => node && typeof node === "object" ? [node, ...(node.children || []).flatMap(descendants)] : [];
const source = fs.readFileSync(path.join(__dirname, "../src/simtask/viewer_assets/app.js"), "utf8");
function viewer(ping) {
  const roots = new Map();
  const get = id => {if (!roots.has(id)) roots.set(id, element()); return roots.get(id);};
  get("env").hidden = true; // as in index.html
  const requests = [];
  const context = vm.createContext({
    document: {title: "Tasks", getElementById: get, createElement: element, createElementNS: (_, tag) => element(tag), addEventListener() {}},
    window: {addEventListener() {}}, location: {hash: ""}, history: {replaceState() {}},
    sessionStorage: {getItem: () => "secret", setItem() {}}, setTimeout() {},
    fetch: async (url, options) => {requests.push([url, options]); return {ok: true, json: async () => ping};},
  });
  vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
  const run = code => vm.runInContext(code, context);
  run("iconButton = () => null;");
  return {context, get, requests, run};
}
async function main() {
  const database = "/home/me/workspace/simtask-change/.dev/tasks.sqlite3";
  const dev = viewer({service: "task-mcp-viewer", database, dev: true});
  await dev.run("environment()");
  // Objects from the page's realm differ by prototype, so compare them as JSON.
  assert.equal(JSON.stringify(dev.requests), JSON.stringify([["/api/ping", {headers: {"X-Task-Token": "secret"}}]]));
  assert.equal(dev.context.document.title, "[DEV] Tasks");
  const env = dev.get("env");
  assert.equal(env.hidden, false);
  const [badge, ...rest] = env.children;
  assert.deepEqual(rest, []);
  assert.equal(badge.textContent, "DEV");
  assert.equal(badge.className, "dev-badge");
  assert.ok(badge.title.includes(database), badge.title);
  assert.ok(badge.attributes["aria-label"].includes(database));
  // The detail top bar repeats the badge for phones, where the list header is hidden.
  const topbar = dev.run("topBar([node('span', 'Project')], [])");
  const repeated = descendants(topbar).filter(n => n.textContent === "DEV");
  assert.equal(repeated.length, 1);
  assert.equal(repeated[0].className, "dev-badge only-mobile");
  assert.ok(repeated[0].title.includes(database));
  // Marking again (a second ping) never stacks the prefix.
  await dev.run("environment()");
  assert.equal(dev.context.document.title, "[DEV] Tasks");

  const live = viewer({service: "task-mcp-viewer", database: "/home/me/.local/share/simtask/tasks.sqlite3", dev: false});
  await live.run("environment()");
  assert.equal(live.context.document.title, "Tasks");
  assert.equal(live.get("env").hidden, true);
  assert.deepEqual(live.get("env").children, []);
  assert.deepEqual(descendants(live.run("topBar([], [])")).filter(n => n.textContent === "DEV"), []);

  // An older server without the fields, or a failed ping, marks nothing.
  const old = viewer({service: "task-mcp-viewer"});
  await old.run("environment()");
  assert.equal(old.context.document.title, "Tasks");
  assert.equal(old.get("env").hidden, true);
  console.log("viewer dev marking ok");
}
main().catch(error => {console.error(error); process.exit(1);});
