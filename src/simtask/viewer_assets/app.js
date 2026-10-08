"use strict";
// Every piece of task text is inserted with textContent or DOM nodes, never parsed as HTML.
const $ = (id) => document.getElementById(id);
// The private launch link carries the token as a bare fragment (#<token>). Location
// URLs are paths (/w/1c4684b6/t/a5dfba02) and never contain it, so drop any fragment
// at once; one that is not a token (such as an old #/project/... address) is ignored.
// Remember the stable token for this origin across tabs and browser restarts, so
// a bookmark of the clean location URL stays connected after the initial launch.
const launchHash = location.hash.slice(1);
const launchToken = /^[A-Za-z0-9_-]+$/.test(launchHash) ? launchHash : "";
function rememberedToken() {
  try { const saved = localStorage.getItem("task-token"); if (saved) return saved; } catch {}
  // Adopt a connection made before persistent browser storage was introduced.
  try { return sessionStorage.getItem("task-token") || ""; } catch { return ""; }
}
const token = launchToken || rememberedToken();
if (token) {
  // Storage may be unavailable in private or restricted browser contexts. The
  // full launch link still works there for the current page.
  try { localStorage.setItem("task-token", token); } catch {}
  try { sessionStorage.setItem("task-token", token); } catch {}
}
history.replaceState(null, "", location.pathname || "/");

// Where a task stands for the user. Cards show only this, never the agents' internal
// stage; whether work is a first attempt or a rework round belongs in the task history.
// The server decides each task's standing (Store._standing, one rule for the board, the
// task detail and the sidebar counts); the viewer only labels and sections it.
const STANDINGS = {
  signoff: { label: "Sign-off", tone: "go", badge: true },
  decision: { label: "Design", tone: "warn" },
  progress: { label: "In progress", tone: "info" },
  open: { label: "Open", tone: "ready" },
  deferred: { label: "Deferred", tone: "muted" },
  done: { label: "Done", tone: "done" },
  dropped: { label: "Dropped", tone: "muted" },
};
// Result states, shown in the task's detail and history only.
const VIEWS = {
  review: { label: "In review", tone: "info" },
  rework: { label: "Sent back", tone: "warn" },
  passed: { label: "Review passed", tone: "go" },
  human_review: { label: "Human reviewed", tone: "go" },
  done: { label: "Done", tone: "done" },
  open: { label: "Open", tone: "ready" },
  ...STANDINGS,
};
const SECTIONS = [
  { key: "signoff", title: "Signoff", standings: ["signoff"], attention: true },
  { key: "progress", title: "In progress", standings: ["progress"] },
  { key: "open", title: "Open", standings: ["open"] },
  { key: "design", title: "Design", standings: ["decision"], attention: true },
  { key: "later", title: "Later", standings: ["deferred"], collapsible: true },
  { key: "done", title: "Done", standings: ["done", "dropped"], collapsible: true },
];
const collapsed = new Set(["done"]);
const CLOSED = ["done", "dropped", "deferred"];
// A fetched task's standing, which the server gives for the results in view (this
// workstream's, or every workstream's on project boards). A server without it (an older
// backend) leaves the standing visibly unknown instead of guessing.
const taskStanding = (t) => t.standing || "unknown";
// The most recent live pick of a fetched task in view (this workstream's, or any on
// project boards); the server lists only picks that still count.
function currentPick(t) {
  return (t.picks || []).find((p) => !state.stream || p.workstream_id === state.stream) || null;
}
// A quick idea saved from the browser: held by the Store's "Idea to process" item until
// an agent goes through it with the user.
const IDEA_PREFIX = "Idea to process:";
function isIdea(t) {
  return !CLOSED.includes(t.status) && (t.unresolved_items || []).some((i) => i.text.startsWith(IDEA_PREFIX));
}

const state = {
  projects: [],
  streams: [],
  rows: [],
  project: null,
  stream: null,
  // The project's Unassigned view: its tasks in no workstream (never with a stream).
  unassigned: false,
  groups: false,
  linkedGroup: null,
  task: null,
  selected: null,
  generation: 0,
  // Board loads are cancelled only by a newer board load or a scope change, so a
  // row click (which bumps generation for the detail pane) cannot leave the list stale.
  listGeneration: 0,
  loadedAt: 0,
  // Drag-and-drop edits only this loaded workstream order, guarded by its revision.
  orderStream: null,
  orderRevision: null,
  orderSaving: false,
  // Archived workstreams stay out of navigation unless shown (remembered per tab).
  showArchived: (() => { try { return sessionStorage.getItem("task-viewer-show-archived") === "1"; } catch { return false; } })(),
  // The workstream whose board state.rows holds (null for project and group boards).
  boardStream: null,
  // Why the last workstream list (and its Needs input counts) failed to load, if it did.
  countsError: null,
  // What the detail pane shows instead of the selected task: null, {kind: "scope"} (the
  // open workstream's and project's notes) or {kind: "note", id, back}. Quiet refreshes
  // keep it; navigation and a task click close it.
  panel: null,
  // The server's /api/ping answer when its database is not the live one (a dev copy,
  // SIMTASK_DB or --db): {database, dev: true}; null on the live database.
  environment: null,
};
let submitAction = null;
let submissionPending = false;

/* ---------- small DOM helpers ---------- */

function node(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
function el(tag, cls, ...children) {
  const e = node(tag, undefined, cls);
  e.append(...children.filter((c) => c !== null && c !== undefined && c !== false));
  return e;
}
function button(text, fn, cls = "btn") {
  const b = node("button", text, cls);
  b.type = "button";
  b.onclick = fn;
  return b;
}
const ICONS = {
  search: "M11 19a8 8 0 1 1 0-16 8 8 0 0 1 0 16zM21 21l-4.3-4.3",
  refresh: "M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6",
  close: "M18 6 6 18M6 6l12 12",
  back: "M15 18l-6-6 6-6",
  chevron: "M9 6l6 6-6 6",
  menu: "M4 6h16M4 12h16M4 18h16",
  layers: "M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5",
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  branch: "M6 3v12M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a9 9 0 0 1-9 9",
  check: "M20 6 9 17l-5-5",
  link: "M7 17 17 7M8 7h9v9",
};
function icon(name, size = 16) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", "i");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", ICONS[name]);
  svg.append(path);
  return svg;
}
function iconButton(name, label, fn, cls = "icon-btn") {
  const b = button("", fn, cls);
  b.append(icon(name));
  b.setAttribute("aria-label", label);
  b.title = label;
  return b;
}
function pill(view, text) {
  const v = VIEWS[view] || { label: view, tone: "muted" };
  return node("span", text || v.label, "pill tone-" + v.tone);
}
function toast(text, error = false) {
  const t = node("div", text, "toast" + (error ? " error" : ""));
  $("toasts").append(t);
  setTimeout(() => t.classList.add("out"), error ? 6000 : 2600);
  setTimeout(() => t.remove(), error ? 6400 : 3000);
}
function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  if (s < 86400 * 14) return Math.floor(s / 86400) + "d ago";
  return new Date(iso).toLocaleDateString();
}
// "12 minutes ago" in plain words, for when an agent picked a task up.
function pickedAgo(iso) {
  const m = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000));
  if (m < 1) return "just now";
  if (m < 60) return `${m} minute${m === 1 ? "" : "s"} ago`;
  const h = Math.floor(m / 60);
  return `${h} hour${h === 1 ? "" : "s"} ago`;
}
// Long branch names read better with the prefix dimmed: codex/ + session-resilience.
function branchLabel(name) {
  const cut = name.lastIndexOf("/");
  const wrap = node("span", undefined, "branch");
  if (cut > 0) wrap.append(node("span", name.slice(0, cut + 1), "prefix"));
  wrap.append(node("span", name.slice(cut + 1)));
  wrap.title = name;
  return wrap;
}

// Activity entry kinds (the server's ACTIVITY_KINDS) in plain words.
const ACTIVITY_LABELS = {
  created: "Created",
  imported: "Imported",
  corrected: "Corrected",
  updated: "Edited",
  accepted: "Specification accepted",
  question_added: "Question added",
  question_resolved: "Question answered",
  prerequisite_added: "Prerequisite added",
  prerequisite_removed: "Prerequisite removed",
  proposal_dismissed: "Gate proposal dismissed",
  workstream_added: "Added to workstream",
  workstream_removed: "Removed from workstream",
  disposition: "Status changed",
  decomposed: "Split into a group",
  member_added: "Member added",
  result: "Result",
  review: "Independent review",
  human_review: "Your review",
  signoff: "Sign-off decision",
};

/* ---------- safe Markdown-ish rendering ---------- */

function inline(text) {
  const frag = document.createDocumentFragment();
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(\[[^\]]+\]\([^)\s]+\))/g;
  let last = 0,
    m;
  while ((m = re.exec(text))) {
    if (m.index > last) frag.append(text.slice(last, m.index));
    const t = m[0];
    if (m[1]) frag.append(node("code", t.slice(1, -1)));
    else if (m[2]) frag.append(node("strong", t.slice(2, -2)));
    else if (m[3]) frag.append(node("em", t.slice(1, -1)));
    else {
      // Links are shown, never followed: no remote content from task text.
      const [, label, url] = t.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      frag.append(label, node("span", " (" + url + ")", "url"));
    }
    last = m.index + t.length;
  }
  if (last < text.length) frag.append(text.slice(last));
  return frag;
}
function markdown(text, { checklist = false } = {}) {
  const root = node("div", undefined, "md");
  text = (text || "").replace(/\r\n?/g, "\n").trim();
  if (!text) {
    root.append(node("p", "Nothing written yet.", "empty-text"));
    return root;
  }
  // A single run-on line of criteria joined by semicolons reads far better as a list.
  if (checklist && !text.includes("\n") && text.split(/;\s+/).length >= 3) {
    const ul = node("ul", undefined, "checks");
    text
      .replace(/\.$/, "")
      .split(/;\s+/)
      .forEach((part) => {
        const li = node("li");
        li.append(inline(part.charAt(0).toUpperCase() + part.slice(1)));
        ul.append(li);
      });
    root.append(ul);
    return root;
  }
  const lines = text.split("\n");
  let para = [],
    lists = [];
  const flushPara = () => {
    if (para.length) {
      const p = node("p");
      p.append(inline(para.join(" ")));
      root.append(p);
      para = [];
    }
  };
  const closeLists = () => {
    lists = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      flushPara();
      closeLists();
      const body = [];
      while (++i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i]);
      root.append(el("pre", "", node("code", body.join("\n"))));
      continue;
    }
    if (!line.trim()) {
      flushPara();
      closeLists();
      continue;
    }
    const heading = line.match(/^\s*(#{1,6})\s+(.*)$/);
    if (heading) {
      flushPara();
      closeLists();
      const h = node("h4", undefined, "md-h" + Math.min(heading[1].length, 3));
      h.append(inline(heading[2]));
      root.append(h);
      continue;
    }
    const item = line.match(/^(\s*)([-*+•]|\d+[.)])\s+(.*)$/);
    if (item) {
      flushPara();
      const depth = Math.floor(item[1].replace(/\t/g, "  ").length / 2);
      const ordered = /\d/.test(item[2]);
      while (lists.length > depth + 1) lists.pop();
      if (lists.length < depth + 1) {
        const list = node(ordered ? "ol" : "ul");
        const parent = lists.length ? lists[lists.length - 1].lastElementChild : null;
        (parent || root).append(list);
        lists.push(list);
      }
      const li = node("li");
      li.append(inline(item[3]));
      lists[lists.length - 1].append(li);
      continue;
    }
    if (lists.length && /^\s{2,}\S/.test(line)) {
      lists[lists.length - 1].lastElementChild.append(" ", inline(line.trim()));
      continue;
    }
    if (/^\s*>/.test(line)) {
      flushPara();
      closeLists();
      const q = node("blockquote");
      q.append(inline(line.replace(/^\s*>\s?/, "")));
      root.append(q);
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line)) {
      flushPara();
      closeLists();
      const rows = [line];
      while (i + 1 < lines.length && /^\s*\|.*\|\s*$/.test(lines[i + 1])) rows.push(lines[++i]);
      root.append(el("pre", "", node("code", rows.join("\n"))));
      continue;
    }
    closeLists();
    para.push(line.trim());
  }
  flushPara();
  return root;
}

/* ---------- API ---------- */

async function api(action, data = {}) {
  const r = await fetch("/api/" + action, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Task-Token": token },
    body: JSON.stringify(data),
  });
  const result = await r.json();
  if (!r.ok) {
    const e = new Error(result.error || "Request failed");
    e.conflict = r.status === 409;
    throw e;
  }
  return result;
}
// Every page of a listing, 100 at a time; maxPages bounds a read that is only a convenience.
async function pages(action, data = {}, maxPages = Infinity) {
  let items = [],
    offset = 0,
    read = 0;
  do {
    const p = await api(action, { ...data, limit: 100, offset });
    items.push(...p.items);
    offset = p.next_offset;
  } while (offset !== null && ++read < maxPages);
  return items;
}
async function taskBoard(data) {
  let items = [], offset = 0, revision = null;
  do {
    const page = await api("tasks", { ...data, limit: 100, offset });
    if (revision !== null && page.workstream_order_revision !== revision)
      throw new Error("Workstream order changed while loading. Refresh to see the current order.");
    revision = page.workstream_order_revision;
    items.push(...page.items);
    offset = page.next_offset;
  } while (offset !== null);
  return { items, workstream_order_revision: revision };
}
function projectName(id) {
  return state.projects.find((p) => p.id === id)?.name || "Other project";
}
function streamName(id) {
  const s = state.streams.find((s) => s.id === id);
  return s?.branch || s?.name || "another workstream";
}
// Every workstream of a project (or all), archived ones included: names, links and
// memberships still resolve; navigation decides what to show.
function workstreams(project) {
  return pages("workstreams", { ...(project ? { project } : {}), include_archived: true });
}
function isArchived(stream) {
  return !!stream?.archive?.archived;
}
function currentArchived() {
  return isArchived(state.streams.find((s) => s.id === state.stream));
}
// The default workstream of a project: its first one that is not archived.
function defaultStream(streams) {
  return streams.find((s) => !isArchived(s))?.id || null;
}
// Tasks waiting for the user's input: those its Signoff and Design sections show. The
// workstream list carries each workstream's count of every standing, by the same server
// rule that gives the board its standings, so it is read with the workstreams on every
// board refresh (group boards included) and no card list is read to count. The open
// board counts its loaded rows, so its sidebar entry always matches the sections shown.
// null: not counted (a server without standing counts).
const INPUT = SECTIONS.filter((s) => s.attention).flatMap((s) => s.standings);
function needsYou(stream) {
  const counts = stream.status?.standings;
  if (!counts) return null;
  if (!state.groups && state.boardStream === stream.id) return state.rows.filter((r) => INPUT.includes(r.standing)).length;
  return INPUT.reduce((n, s) => n + (counts[s] || 0), 0);
}
// A task's detail, with its standing for the results in view.
function details(id) {
  return api("details", { ids: [id], ...(state.stream ? { workstream_id: state.stream } : {}) });
}

/* ---------- location URLs ---------- */

// Short, token-free paths name the selected project, view and task or group:
//   /p/<project>      All tasks         /p/<project>/t/<task>
//   /p/<project>/u    Unassigned        /p/<project>/u/t/<task>
//   /p/<project>/g    Task groups       /p/<project>/g/<group>
//   /w/<workstream>   a workstream      /w/<workstream>/t/<task>
//   /g/<group>        a group in the view it implies (its project's groups, or shared)
//   /sg               Shared groups     /sg/<group>
// A workstream or group implies its project, so only project-wide views name one.
// Readable public task/group IDs appear in full. Legacy IDs and project/workstream
// Legacy IDs appear as their first 8 hex characters, or in full when that prefix is ambiguous
// where the address is resolved: tasks within the view's board, projects among all
// projects, workstreams and groups across the database (through "resolve-prefix").
// New public task/group IDs stay complete under /id/, including all-hex names.
const SHORT = 8;
const hexOf = (id) => String(id).replace(/^[a-z]+_/, "");
const longIds = new Set(); // IDs shown in full because their short prefix is ambiguous
const checkedIds = new Set(); // workstream and group IDs whose short prefix was checked
const groupHomes = new Map(); // group ID -> "shared", or the project whose groups it implies
function shortId(id, pool = []) {
  if (!/^[a-z]+_[0-9a-f]{1,32}$/.test(id)) return id;
  const hex = hexOf(id), short = hex.slice(0, SHORT);
  return longIds.has(id) || pool.some((o) => o !== id && /^[a-z]+_[0-9a-f]{32}$/.test(o) && hexOf(o).startsWith(short)) ? hex : short;
}
const taskRouteId = (id, pool) => /^tsk_[0-9a-f]{32}$/.test(id) ? shortId(id, pool) : "id/" + id;
function routePath() {
  if (!state.project) return "";
  const ids = (list) => list.map((x) => x.id);
  const project = "/p/" + shortId(state.project, ids(state.projects));
  if (state.groups) {
    const group = state.selected;
    if (!group) return state.groups === "shared" ? "/sg" : project + "/g";
    const id = taskRouteId(group, ids(state.rows));
    const home = groupHomes.get(group);
    if (home && home === (state.groups === "shared" ? "shared" : state.project)) return "/g/" + id;
    return (state.groups === "shared" ? "/sg/" : project + "/g/") + id;
  }
  const base = state.stream ? "/w/" + shortId(state.stream, ids(state.streams)) : state.unassigned ? project + "/u" : project;
  return state.selected ? base + "/t/" + taskRouteId(state.selected, ids(state.rows)) : base;
}
// A parsed location ({view, project, stream, group, task} as public IDs/hex prefixes), {} for the
// default location, or null for an unrecognized address.
function parseRoute(path) {
  if (path === "/") return {};
  const parts = path.split("/").slice(1);
  const id = (s) => /^[0-9a-f]{1,32}$/.test(s || "");
  const publicId = (s) => typeof s === "string" && s.length <= 96 && /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(s);
  // /p/<project>/u[/t/<task> | /t/id/<public-id>]: the project's Unassigned view.
  if (parts[0] === "p" && id(parts[1]) && parts[2] === "u") {
    const [, project, , t, x, y, ...more] = parts;
    if (more.length) return null;
    if (t === undefined) return { view: "unassigned", project };
    if (t === "t" && id(x) && y === undefined) return { view: "unassigned", project, task: x };
    if (t === "t" && x === "id" && publicId(y)) return { view: "unassigned", project, task: y, publicId: true };
    return null;
  }
  const [a, b, c, d, e, ...rest] = parts;
  if (rest.length) return null;
  if ((a === "p" || a === "w") && id(b) && d === "id" && publicId(e)) {
    if (c === "t") return {view: a === "p" ? "all" : "workstream", [a === "p" ? "project" : "stream"]: b, task: e, publicId: true};
    if (a === "p" && c === "g") return {view: "groups", project: b, group: e, publicId: true};
  }
  if (e !== undefined) return null;
  if ((a === "g" || a === "sg") && b === "id" && publicId(c) && d === undefined) return {view: a === "g" ? "group" : "shared-groups", group: c, publicId: true};
  if (a === "p" && id(b)) {
    if (c === undefined) return { view: "all", project: b };
    if (c === "t" && id(d)) return { view: "all", project: b, task: d };
    if (c === "g" && d === undefined) return { view: "groups", project: b };
    if (c === "g" && id(d)) return { view: "groups", project: b, group: d };
  } else if (a === "w" && id(b)) {
    if (c === undefined) return { view: "workstream", stream: b };
    if (c === "t" && id(d)) return { view: "workstream", stream: b, task: d };
  } else if (a === "g" && id(b) && c === undefined) return { view: "group", group: b };
  else if (a === "sg" && c === undefined) {
    if (b === undefined) return { view: "shared-groups" };
    if (id(b)) return { view: "shared-groups", group: b };
  }
  return null;
}
// Resolve a legacy prefix or marked exact public group ID; report ambiguity explicitly.
async function resolvePrefix(kind, prefix, publicId = false) {
  const { items } = await api("resolve-prefix", { kind, prefix, ...(publicId ? {match: "public_id"} : {}) });
  if (items.length !== 1) return { ambiguous: items.length > 1 };
  const found = items[0];
  // A short prefix that matched once proves the 8-character one unique; keep a longer
  // spelling until checkPrefix confirms that the short one would do.
  if (publicId) return found;
  if (prefix.length <= SHORT) {
    checkedIds.add(found.id);
    longIds.delete(found.id);
  } else if (!checkedIds.has(found.id)) longIds.add(found.id);
  return found;
}
// Spell a written workstream or group ID in full only if its short prefix is ambiguous
// across the database. Checked once per ID, after the address is written; the
// current address is respelled in place if it still names that ID.
function checkPrefix(kind, id) {
  if (checkedIds.has(id) || !/^[a-z]+_[0-9a-f]{32}$/.test(id)) return;
  checkedIds.add(id);
  const hex = hexOf(id), short = hex.slice(0, SHORT);
  api("resolve-prefix", { kind, prefix: short }).then(({ items }) => {
    const ambiguous = items.length > 1;
    if (ambiguous === longIds.has(id)) return;
    if (ambiguous) longIds.add(id);
    else longIds.delete(id);
    const parts = (location.pathname || "/").split("/");
    // The workstream is /w/<id>...; the group ends /g/<id>, /sg/<id> or /p/<project>/g/<id>.
    const at = kind === "workstream" ? (parts[1] === "w" ? 2 : 0) : ["g", "sg"].includes(parts[1]) || parts[3] === "g" ? parts.length - 1 : 0;
    if (!at || parts[at] !== (ambiguous ? short : hex)) return;
    parts[at] = ambiguous ? hex : short;
    shownPath = parts.join("/");
    history.replaceState(null, "", shownPath);
  }, () => checkedIds.delete(id));
}
// Learn which view a group's /g/ address implies, from its details.
function learnGroupHome(t) {
  const projects = Object.keys(t.progress?.by_project || {});
  const home = projects.length > 1 ? "shared" : projects[0] || t.origin_project_id;
  if (home) groupHomes.set(t.id, home);
  return home;
}
// The location this tab last wrote or opened; back/forward to anything else opens it.
let shownPath = null;
// Record the current location: "push" for a deliberate navigation, "replace" for
// automatic selection, fallbacks and loads.
function syncRoute(mode = "replace") {
  const path = routePath();
  if (!path) return;
  if (path !== (location.pathname || "/")) history[mode === "push" ? "pushState" : "replaceState"](null, "", path);
  shownPath = path;
  if (state.groups && state.selected) checkPrefix("group", state.selected);
  else if (!state.groups && state.stream) checkPrefix("workstream", state.stream);
}
// Open a location URL (on load, back/forward or an edited address). Anything that no
// longer exists, or a prefix matching several items, falls back to the nearest valid
// view with a short notice.
async function openLocation(path, { initial = false } = {}) {
  const generation = ++state.generation;
  const stale = () => generation !== state.generation;
  const route = parseRoute(path);
  const notices = [];
  if (!route) notices.push("That address is not a viewer location.");
  let view = route?.view || null, projectId = null, stream = null, group = null, task = route?.task || null;
  if (route?.project) {
    const match = (list) => list.filter((p) => hexOf(p.id).startsWith(route.project));
    let found = match(state.projects);
    if (!found.length) {
      state.projects = await pages("projects");
      if (stale()) return;
      found = match(state.projects);
    }
    if (found.length === 1) projectId = found[0].id;
    else {
      notices.push(found.length ? "That address matches more than one project." : "That project no longer exists.");
      view = task = null;
    }
  }
  if (route?.stream) {
    const found = await resolvePrefix("workstream", route.stream);
    if (stale()) return;
    if (found.id) [stream, projectId] = [found.id, found.project_id];
    else {
      notices.push(found.ambiguous ? "That address matches more than one workstream." : "That workstream no longer exists.");
      view = "all";
    }
  }
  if (route?.group && view) {
    const found = await resolvePrefix("group", route.group, route.publicId);
    if (stale()) return;
    if (found.id) group = found.id;
    else notices.push(found.ambiguous ? "That address matches more than one task group." : "That task group no longer exists.");
  }
  let origin = null;
  if (view === "group") {
    view = "groups";
    if (group) {
      // A group implies its view: shared across projects, else its project's groups.
      const t = (await api("details", { ids: [group] })).items[0];
      if (stale()) return;
      const home = learnGroupHome(t);
      origin = t.origin_project_id;
      if (home === "shared") view = "shared-groups";
      else projectId = home;
    }
  }
  if (projectId && !state.projects.some((p) => p.id === projectId)) {
    state.projects = await pages("projects");
    if (stale()) return;
  }
  // Views that name no project keep the open one (or the group's), else the first.
  const project = [projectId, state.project, origin].map((id) => state.projects.find((p) => p.id === id)).find(Boolean) || state.projects[0];
  if (!project) return void notices.forEach((n) => toast(n));
  const streams = await workstreams(project.id);
  if (stale()) return;
  const groups = view === "groups" ? "project" : view === "shared-groups" ? "shared" : false;
  if (!view) stream = defaultStream(streams);
  if (!groups) group = null;
  closeDrawer();
  // On a narrow screen a reloaded task reopens its detail; back/forward shows the list.
  if (initial && (task || group)) $("shell").classList.add("detail-open");
  state.project = project.id;
  state.streams = streams;
  state.stream = stream;
  state.unassigned = view === "unassigned" && !groups && !stream;
  state.groups = groups;
  state.linkedGroup = group;
  state.selected = group;
  state.task = null;
  const name = groups === "shared" ? "Shared task groups" : groups ? `${project.name} task groups` : state.stream ? `${project.name} · ${streamName(state.stream)}` : `${project.name} · ${boardName()}`;
  notices.forEach((n) => toast(`${n} Showing ${name}.`));
  // Keep the requested address until the board resolves its exact ID or legacy prefix.
  if (!task) syncRoute();
  renderNav();
  await reload({ requested: groups ? null : task, publicId: !!route?.publicId });
}
function onLocationChange() {
  // A launch link pasted into this tab: reload so startup adopts and strips its token.
  if (location.hash.length > 1) return location.reload();
  const path = location.pathname || "/";
  if (path === shownPath) return;
  shownPath = path;
  if (!state.projects.length || $("shell").classList.contains("stopped")) return;
  closeMenu();
  openLocation(path).catch((e) => toast(e.message, true));
}
window.addEventListener("popstate", onLocationChange);
window.addEventListener("hashchange", onLocationChange);

/* ---------- environment ---------- */

// The server decides from its database path whether this is the live database. Any
// other one marks every view DEV, with the path in the badge's tooltip, and the tab
// title [DEV], so a dev viewer (./run.sh --dev, on the tailnet too) is never taken for
// the live one. Silent when the ping fails: the board's own errors are reported.
async function environment() {
  const r = await fetch("/api/ping", { headers: { "X-Task-Token": token } });
  if (!r.ok) return;
  const info = await r.json();
  if (!info.dev) return;
  state.environment = info;
  if (!document.title.startsWith("[DEV] ")) document.title = "[DEV] " + document.title;
  const env = $("env");
  env.replaceChildren(devBadge());
  env.hidden = false;
}
// The DEV badge for a header, or null on the live database.
function devBadge(cls = "") {
  if (!state.environment?.dev) return null;
  const b = node("span", "DEV", "dev-badge" + (cls ? " " + cls : ""));
  b.title = "Dev viewer, not the live database: " + state.environment.database;
  b.setAttribute("aria-label", "Dev viewer on " + state.environment.database);
  return b;
}

/* ---------- navigation ---------- */

async function boot() {
  $("menu").append(icon("menu"));
  $("refresh").append(icon("refresh"));
  $("close").append(icon("close"));
  $("search-icon").append(icon("search", 15));
  environment().catch(() => {});
  try {
    state.projects = await pages("projects");
    if (!state.projects.length) {
      renderNav();
      $("list").replaceChildren(
        emptyState("No projects yet", "Initialize a project through simtask to get started."),
      );
      $("detail").replaceChildren();
      return;
    }
    shownPath = location.pathname || "/";
    await openLocation(shownPath, { initial: true });
  } catch (e) {
    toast(e.message, true);
  }
}
async function chooseProject(id, { keepSelection = false } = {}) {
  const generation = ++state.generation;
  state.project = id;
  state.groups = false;
  state.unassigned = false;
  state.linkedGroup = null;
  if (!keepSelection) {
    state.selected = null;
    state.task = null;
  }
  const streams = await workstreams(id);
  if (generation !== state.generation) return;
  state.streams = streams;
  state.stream = keepSelection ? null : defaultStream(streams);
  syncRoute("push");
  renderNav();
  await reload();
}
function renderNav() {
  const nav = $("nav");
  nav.replaceChildren(node("div", "Projects", "nav-label"));
  state.projects.forEach((p) => {
    const active = p.id === state.project && !state.groups;
    const b = button("", () => {
      closeDrawer();
      if (p.id !== state.project || state.groups) chooseProject(p.id).catch((e) => toast(e.message, true));
    }, "nav-item project" + (p.id === state.project ? " current" : ""));
    b.append(icon("folder"), node("span", p.name, "grow"));
    b.title = p.canonical_path || p.name;
    nav.append(b);
    if (p.id !== state.project) return;
    const sub = el("div", "nav-sub");
    const all = button("", () => changeScope(null), "nav-item" + (active && !state.stream && !state.unassigned ? " active" : ""));
    all.append(node("span", "All tasks", "grow"));
    // Directly below All tasks: the project's tasks in no workstream.
    const unassigned = button("", () => chooseUnassigned(), "nav-item" + (active && state.unassigned ? " active" : ""));
    unassigned.append(node("span", "Unassigned", "grow"));
    unassigned.title = "Tasks in this project that belong to no workstream";
    sub.append(all, unassigned);
    const groups = button("", () => chooseGroups("project"), "nav-item" + (state.groups === "project" ? " active" : ""));
    groups.append(icon("layers", 14), node("span", "Task groups", "grow"));
    sub.append(groups);
    // Archived workstreams are hidden unless shown; an opened one stays visible.
    const archived = state.streams.filter(isArchived);
    state.streams.forEach((s) => {
      const old = isArchived(s);
      if (old && !state.showArchived && !(active && state.stream === s.id)) return;
      const b = button("", () => changeScope(s.id), "nav-item" + (active && state.stream === s.id ? " active" : "") + (old ? " archived" : ""));
      b.append(icon("branch", 14), branchLabel(s.branch || s.name));
      // A workstream with tasks that need you shows that count; with none, its task total;
      // uncounted, a question mark. Counts from a failed refresh are marked out of date.
      const n = old ? 0 : needsYou(s), total = s.status?.scoped_count ?? 0;
      const outdated = state.countsError && !old ? " stale" : "";
      if (old) b.append(node("span", "archived", "tag-archived"));
      else if (n === null) b.append(node("span", "?", "count unknown"));
      else if (n) b.append(node("span", String(n), "count attention" + outdated));
      else b.append(node("span", String(total), "count" + outdated));
      b.title = `${s.branch || s.name} · ${total} task${total === 1 ? "" : "s"}` +
        (old ? ` · archived: ${s.archive.reason}` : n === null ? " · tasks that need you are not counted; restart the viewer" : n ? ` · ${n} need you` : " · none need you") +
        (outdated ? ` · may be out of date: the last refresh failed (${state.countsError}); refresh to try again` : "");
      sub.append(b);
    });
    if (state.countsError) {
      const retry = button("", () => reload({ quiet: true }), "nav-item counts-stale");
      retry.append(node("span", "Counts may be out of date · Retry", "grow"));
      retry.title = `The last refresh of the workstream counts failed (${state.countsError}).`;
      sub.append(retry);
    }
    if (archived.length) {
      const toggle = button(state.showArchived ? "Hide archived" : `Show ${archived.length} archived`, () => {
        state.showArchived = !state.showArchived;
        try { sessionStorage.setItem("task-viewer-show-archived", state.showArchived ? "1" : "0"); } catch {}
        renderNav();
      }, "nav-item nav-toggle");
      toggle.setAttribute("aria-pressed", String(state.showArchived));
      sub.append(toggle);
    }
    nav.append(sub);
  });
  nav.append(node("div", "Across projects", "nav-label"));
  const g = button("", () => chooseGroups("shared"), "nav-item" + (state.groups === "shared" ? " active" : ""));
  g.append(icon("layers"), node("span", "Shared task groups", "grow"));
  nav.append(g);
}
async function chooseGroups(mode) {
  closeDrawer();
  state.groups = mode;
  state.linkedGroup = null;
  state.stream = null;
  state.unassigned = false;
  state.selected = null;
  state.task = null;
  syncRoute("push");
  renderNav();
  await reload();
}
function groupProjectCount(group) {
  return group.project_count ?? Object.keys(group.progress?.by_project || {}).length;
}
function groupKind(group) {
  const projects = groupProjectCount(group);
  return projects > 1 ? "Shared group" : projects === 1 ? "Project group" : "Empty group";
}
async function changeScope(id, { unassigned = false } = {}) {
  closeDrawer();
  state.groups = false;
  state.linkedGroup = null;
  state.stream = id;
  state.unassigned = !id && unassigned;
  state.selected = null;
  state.task = null;
  syncRoute("push");
  renderNav();
  await reload();
}
// The project's Unassigned view: a placement filter over its tasks, not a status.
function chooseUnassigned() {
  return changeScope(null, { unassigned: true });
}
// The project-wide task list's name: All tasks, or its Unassigned view.
function boardName() {
  return state.unassigned ? "Unassigned" : "All tasks";
}
async function reload({ quiet = false, requested = null, publicId = false } = {}) {
  const generation = ++state.listGeneration;
  // A row click or navigation that starts while this board loads owns the detail pane
  // (and bumps state.generation); auto-selecting here would cancel it.
  const detailGeneration = state.generation;
  const project = state.project;
  const groups = state.groups;
  const stream = state.stream;
  const unassigned = !groups && !stream && state.unassigned;
  const stale = () => generation !== state.listGeneration || project !== state.project || groups !== state.groups || stream !== state.stream || unassigned !== (!state.groups && !state.stream && state.unassigned);
  if (!quiet) {
    $("list").replaceChildren(skeleton());
    state.panel = null;
  }
  $("idea").hidden = !!groups || !project;
  $("notes-open").hidden = !project || groups === "shared";
  $("heading").replaceChildren(
    groups === "shared" ? "Shared task groups" : groups ? "Task groups" : state.stream ? branchLabel(streamName(state.stream)) : boardName(),
  );
  // The workstreams, with their Needs input counts, are read again on every board,
  // group boards included. A failed read keeps the board and the previous list, and
  // marks the counts out of date until a later refresh succeeds.
  const streamsLoad = project
    ? workstreams(project).then((items) => ({ items }), (error) => ({ error }))
    : Promise.resolve({ items: state.streams });
  try {
    // Unassigned is paged on the server over the matching tasks only.
    const board = groups ? null : await taskBoard(unassigned ? { project, unassigned: true } : { project, workstream_id: state.stream });
    const loaded = groups
      ? await pages("groups", groups === "project" ? { project } : {})
      : board.items;
    const rows = groups === "shared" ? loaded.filter((g) => groupProjectCount(g) > 1) : loaded;
    const listed = await streamsLoad;
    if (stale()) return;
    // Task cards carry the server's standing; group boards list groups, which have none.
    state.rows = rows;
    state.boardStream = groups ? null : stream;
    const streams = listed.items || state.streams;
    state.streams = streams;
    if (listed.error && !state.countsError) toast(`Couldn't refresh the workstream counts: ${listed.error.message}. They may be out of date; refresh to try again.`, true);
    state.countsError = listed.error ? listed.error.message : null;
    state.loadedAt = Date.now();
    state.orderStream = !groups && state.stream ? state.stream : null;
    state.orderRevision = state.orderStream ? board.workstream_order_revision : null;
    $("subheading").textContent = groups
      ? `${groups === "project" ? projectName(project) : "Across projects"} · ${rows.length} group${rows.length === 1 ? "" : "s"}`
      : `${projectName(state.project)} · ${rows.length} task${rows.length === 1 ? "" : "s"}${unassigned ? " in no workstream" : ""}`;
    $("subheading").title = unassigned ? "Tasks here belong to no workstream. Add one to a workstream to have it built there; it then leaves Unassigned." : "";
    const shownStream = !groups && state.stream ? streams.find((s) => s.id === state.stream) : null;
    if (isArchived(shownStream)) {
      // A short tag beside the name keeps the subheading's actions visible; the reason
      // is its tooltip.
      const tag = node("span", "Archived", "tag-archived");
      tag.title = "Archived: " + shownStream.archive.reason;
      $("heading").append(tag);
    }
    renderNav();
    renderList();
    if (detailGeneration !== state.generation) return;
    if (requested) {
      // Exact public IDs and legacy prefixes resolve separately within this board.
      // A task that left the view (or an ambiguous prefix) falls back to the first task.
      const matches = publicId ? rows.filter((r) => r.id === requested) : rows.filter((r) => /^tsk_[0-9a-f]{32}$/.test(r.id) && hexOf(r.id).startsWith(requested));
      if (matches.length === 1) state.selected = matches[0].id;
      else {
        const where = stream ? streamName(stream) : unassigned ? "Unassigned" : "this project";
        toast((matches.length ? `That address matches more than one task in ${where}.` : `That task is not in ${where}.`) + (rows.length ? " Showing the first task." : ""));
      }
    }
    if (state.selected && (rows.some((r) => r.id === state.selected) || (groups && state.linkedGroup === state.selected))) await selectTask(state.selected, { quiet });
    else if (rows.length) {
      const first = orderedRows()[0] || rows[0];
      await selectTask(first.id);
    } else {
      state.selected = null;
      syncRoute();
      $("detail").classList.remove("loading");
      if (state.panel) return void (await renderPanel());
      $("detail").replaceChildren(
        state.groups
          ? emptyState(groups === "shared" ? "No shared task groups" : "No task groups", groups === "shared" ? "Task groups with members in more than one project appear here." : "Task groups belonging to or included in this project appear here.")
          : unassigned
            ? emptyState("No unassigned tasks", "Every task in this project belongs to a workstream. Tasks that belong to no workstream, such as ideas saved with + Idea, appear here until they are assigned or processed.")
            : emptyState("Nothing here yet", "Tasks appear here when an agent adds them. Use + Idea to save a quick idea, or pick another workstream."),
      );
    }
  } catch (e) {
    if (stale()) return;
    if (detailGeneration === state.generation) $("detail").classList.remove("loading");
    toast(e.message, true);
    $("list").replaceChildren(emptyState("Couldn't load tasks", "Refresh to try again."));
  }
}

/* ---------- list ---------- */

function filteredRows() {
  const query = $("search").value.trim().toLowerCase();
  return state.rows.filter((r) => `${r.id} ${r.title} ${r.summary || ""}`.toLowerCase().includes(query));
}
function orderedRows() {
  if (state.groups) return filteredRows();
  const rows = filteredRows();
  return SECTIONS.flatMap((s) =>
    collapsed.has(s.key) && !$("search").value ? [] : rows.filter((r) => s.standings.includes(r.standing)),
  );
}
function taskIdLabel(id) {
  const label = node("code", id, "task-id");
  label.setAttribute("aria-label", "Task ID: " + id);
  // Selecting/copying the identifier should not open the card or start a drag.
  label.onclick = (event) => event.stopPropagation();
  label.onmousedown = (event) => {
    event.stopPropagation();
    const card = label.closest?.(".row");
    if (card?.draggable) {
      card.draggable = false;
      window.addEventListener("mouseup", () => { card.draggable = true; }, { once: true });
    }
  };
  label.draggable = false;
  return label;
}
function taskIdHeader(id) {
  const label = taskIdLabel(id);
  const copy = button("Copy ID", async () => {
    try {
      await navigator.clipboard.writeText(id);
      toast("Task ID copied.");
    } catch {
      const range = document.createRange();
      range.selectNodeContents(label);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      toast("Task ID selected. Press Ctrl+C (⌘C on a Mac) to copy it.");
    }
  }, "small");
  return el("div", "task-id-header", label, copy);
}
function row(r) {
  const b = button("", () => selectTask(r.id, { open: true, entry: "push" }), "row" + (r.id === state.selected ? " selected" : ""));
  b.dataset.id = r.id;
  b.setAttribute("role", "listitem");
  b.setAttribute("aria-current", r.id === state.selected ? "true" : "false");
  if (state.groups) {
    const total = r.progress?.total || 0,
      done = r.progress?.done || 0;
    b.append(
      node("span", "", "dot tone-" + (r.complete ? "done" : "info")),
      el("span", "row-main", node("span", r.title, "row-title"), taskIdLabel(r.id), r.summary ? node("small", r.summary + (r.summary_stale ? " · Summary predates current spec" : ""), "row-summary muted") : null, node("span", `${groupKind(r)} · ${groupProjectCount(r)} project${groupProjectCount(r) === 1 ? "" : "s"}`, "muted"), progressBar(done, total)),
      node("span", `${done}/${total}`, "row-meta"),
    );
  } else {
    // Only Sign-off has a badge on cards; the orange dot identifies Design. The badge sits
    // inline after the title, so it never narrows or truncates it.
    const v = STANDINGS[r.standing] || { label: r.standing || "Unknown", tone: "muted" };
    b.classList.toggle("closed", r.standing === "done" || r.standing === "dropped");
    if (orderEditable()) {
      b.draggable = true;
      b.classList.add("draggable");
      // Positions are workstream-wide, so a section shows no numbering of its own.
      b.title = `${v.label} · position ${r.workstream_order_key} in ${streamName(state.stream)}. Drag to reorder.`;
    } else b.title = v.label;
    const title = node("span", r.title, "row-title");
    if (v.badge) title.append(" ", node("span", v.label, "badge tone-" + v.tone));
    // In All tasks, a task in no workstream says so, so that one under Open is not taken
    // for work an agent can pick up. Inline after the title, it never truncates it.
    if (!state.stream && !state.unassigned && Array.isArray(r.workstream_ids) && !r.workstream_ids.length) {
      const tag = node("span", "Unassigned", "badge tone-placement");
      tag.title = "In no workstream: no agent picks it up until it is added to one.";
      title.append(" ", tag);
    }
    const main = el("span", "row-main", title, taskIdLabel(r.id));
    b.append(node("span", "", "dot tone-" + v.tone), main);
  }
  return b;
}
function renderList() {
  const list = $("list");
  const rows = filteredRows();
  const searching = !!$("search").value.trim();
  list.replaceChildren();
  if (state.groups) {
    list.append(...rows.map(row));
  } else {
    SECTIONS.forEach((s) => {
      const items = rows.filter((r) => s.standings.includes(r.standing));
      if (!items.length) return;
      const isCollapsed = collapsed.has(s.key) && !searching;
      const head = button("", () => {
        if (!s.collapsible) return;
        collapsed.has(s.key) ? collapsed.delete(s.key) : collapsed.add(s.key);
        renderList();
      }, "section-head" + (s.collapsible ? " toggle" : "") + (isCollapsed ? " collapsed" : ""));
      if (s.collapsible) head.append(icon("chevron", 12));
      head.append(node("span", s.title), node("span", String(items.length), "count" + (s.attention ? " attention" : "")));
      head.setAttribute("aria-expanded", String(!isCollapsed));
      list.append(head);
      if (!isCollapsed) list.append(...items.map(row));
    });
    // Standings the list does not know about still deserve a place.
    const known = SECTIONS.flatMap((s) => s.standings);
    const other = rows.filter((r) => !known.includes(r.standing));
    if (other.length) list.append(node("div", "Other", "section-head"), ...other.map(row));
  }
  if (!rows.length && state.rows.length)
    list.append(emptyState("No matches", "Try a different search."));
}
function progressBar(done, total) {
  const bar = node("span", undefined, "progress");
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-valuemin", "0");
  bar.setAttribute("aria-valuemax", String(total));
  bar.setAttribute("aria-valuenow", String(done));
  const fill = node("span");
  fill.style.width = total ? `${(100 * done) / total}%` : "0";
  bar.append(fill);
  return bar;
}
function skeleton() {
  const wrap = node("div", undefined, "skeleton");
  for (let i = 0; i < 6; i++) wrap.append(node("div"));
  return wrap;
}
function emptyState(title, text) {
  return el("div", "empty", node("h3", title), node("p", text));
}

/* ---------- drag-and-drop ordering ---------- */

// Only a named workstream has an order to edit; project-wide and group views do not.
function orderEditable() {
  return !state.groups && !!state.stream && state.orderStream === state.stream && state.orderRevision !== null;
}
// The complete new order, or null for an invalid or unchanged drop. Rows hidden by
// search or collapsed sections stay in state.rows, so they keep their relative order.
function droppedOrder(ids, movedId, targetId, placement) {
  if (movedId === targetId || !ids.includes(movedId) || !ids.includes(targetId)) return null;
  if (placement !== "before" && placement !== "after") return null;
  const next = ids.filter((id) => id !== movedId);
  next.splice(next.indexOf(targetId) + (placement === "after" ? 1 : 0), 0, movedId);
  return next.every((id, i) => id === ids[i]) ? null : next;
}
function sectionTitle(r) {
  return SECTIONS.find((s) => s.standings.includes(r.standing))?.title || "Other";
}
const drag = { id: null, stream: null, source: null, marked: null };
function dropHint(text) {
  $("drop-hint").textContent = text || "";
  $("drop-hint").hidden = !text;
}
function unmarkDrop() {
  drag.marked?.classList.remove("drop-before", "drop-after");
  drag.marked = null;
}
function endDrag() {
  unmarkDrop();
  drag.source?.classList.remove("dragging");
  drag.id = drag.stream = drag.source = null;
  if (!state.orderSaving) dropHint("");
}
// The row under the pointer and which half of it: upper half places before, lower after.
function dropTarget(e) {
  const target = e.target?.closest?.(".row");
  if (!target?.dataset.id || target.dataset.id === drag.id) return null;
  const box = target.getBoundingClientRect();
  return { el: target, id: target.dataset.id, placement: e.clientY < box.top + box.height / 2 ? "before" : "after" };
}
function describeDrop(movedId, targetId, placement, next) {
  const moved = state.rows.find((r) => r.id === movedId);
  const target = state.rows.find((r) => r.id === targetId);
  if (!next) return `“${moved.title}” is already ${placement} “${target.title}”. Dropping here changes nothing.`;
  let text = `Place ${placement} “${target.title}” · position ${next.indexOf(movedId) + 1} of ${next.length} in ${streamName(drag.stream || state.stream)}`;
  if (sectionTitle(moved) !== sectionTitle(target))
    text += ` · it stays under ${sectionTitle(moved)}, because status sets the section`;
  return text;
}
async function commitDrop(movedId, targetId, placement) {
  if (!orderEditable() || state.orderSaving) return false;
  const next = droppedOrder(state.rows.map((r) => r.id), movedId, targetId, placement);
  if (!next) return false;
  const stream = state.stream, revision = state.orderRevision, before = state.rows;
  const byId = new Map(before.map((r) => [r.id, r]));
  const optimistic = next.map((id, i) => ({ ...byId.get(id), workstream_order_key: i + 1 }));
  state.orderSaving = true;
  state.listGeneration++; // a board load already in flight predates this save; discard it
  state.rows = optimistic;
  renderList();
  dropHint("Saving the new order…");
  let saved = false;
  try {
    // One prefix through the moved task; the store keeps every later member's order.
    const ack = await api("reorder", {
      workstream_id: stream,
      task_ids: next.slice(0, next.indexOf(movedId) + 1),
      expected_order_revision: revision,
    });
    saved = true;
    // The shown order is now the recorded one; adopt its revision so the board stays
    // consistent even if the reconciling reload below never completes.
    if (state.rows === optimistic && state.orderStream === stream && state.orderRevision === revision)
      state.orderRevision = Number.isInteger(ack?.workstream_order_revision) ? ack.workstream_order_revision : null;
    toast("Order saved");
  } catch (e) {
    // Never keep an unconfirmed order: put back the loaded rows and disable dragging
    // until a reconciling reload supplies the recorded order and its revision.
    if (state.orderStream === stream) state.orderRevision = null;
    if (state.rows === optimistic) state.rows = before;
    toast(e.conflict
      ? `${streamName(stream)} changed elsewhere, so this move was not saved. Showing the current order; drag again if you still want it.`
      : `Couldn't confirm the new order (${e.message}). Showing the saved order.`, true);
  } finally {
    state.orderSaving = false;
    dropHint("");
    if (!state.groups && state.stream === stream) renderList();
  }
  // Reconcile with recorded state either way.
  if (state.stream === stream && !state.groups) await reload({ quiet: true });
  return saved;
}
$("list").ondragstart = (e) => {
  const source = e.target?.closest?.(".row");
  if (!source || !orderEditable() || state.orderSaving || !state.rows.some((r) => r.id === source.dataset.id)) {
    e.preventDefault();
    return;
  }
  endDrag();
  drag.id = source.dataset.id;
  drag.stream = state.stream;
  drag.source = source;
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData("application/x-task-mcp-task", drag.id);
  source.classList.add("dragging");
  dropHint("Drop on the upper or lower half of another task to place it before or after that task.");
};
$("list").ondragover = (e) => {
  if (!drag.id || drag.stream !== state.stream) return;
  const target = dropTarget(e);
  unmarkDrop();
  if (!target) return dropHint("Drop on another task to place it before or after that task.");
  const next = droppedOrder(state.rows.map((r) => r.id), drag.id, target.id, target.placement);
  dropHint(describeDrop(drag.id, target.id, target.placement, next));
  if (!next) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  target.el.classList.add("drop-" + target.placement);
  drag.marked = target.el;
};
$("list").ondragleave = (e) => {
  if (drag.id && !$("list").contains(e.relatedTarget)) {
    unmarkDrop();
    dropHint("Drop on another task to place it before or after that task.");
  }
};
$("list").ondrop = (e) => {
  if (!drag.id) return;
  e.preventDefault();
  const moved = drag.id, stream = drag.stream, target = dropTarget(e);
  endDrag();
  if (target && stream === state.stream) commitDrop(moved, target.id, target.placement).catch((error) => toast(error.message, true));
};
$("list").ondragend = endDrag;

/* ---------- detail ---------- */

async function includedWorkstreams(groupId) {
  const included = [];
  for (const w of await workstreams()) {
    let found = w.groups.includes(groupId);
    if (!found && w.groups_has_more) {
      let offset = 0;
      do {
        const p = await api("workstream-status", {
          workstream_id: w.id, include_scope: true, include_archived: true, limit: 100, offset,
        });
        found = p.scope.groups.ids.includes(groupId);
        offset = p.scope.groups.next_offset;
      } while (!found && offset !== null);
    }
    if (found) included.push(w);
  }
  return included;
}

async function selectTask(id, { open = false, quiet = false, entry = "replace" } = {}) {
  const generation = ++state.generation;
  if (!quiet) state.panel = null;
  state.selected = id;
  syncRoute(entry);
  document.querySelectorAll(".row").forEach((r) => {
    const on = r.dataset.id === id;
    r.classList.toggle("selected", on);
    r.setAttribute("aria-current", String(on));
    if (on) r.scrollIntoView({ block: "nearest" });
  });
  if (open) $("shell").classList.add("detail-open");
  if (!quiet) $("detail").classList.add("loading");
  try {
    const t = (await details(id)).items[0];
    if (state.selected !== id || generation !== state.generation) return;
    if (t.object_type === "group") {
      // Now that the group's projects are known, its address may shorten to /g/<group>.
      if (state.groups && !groupHomes.has(id)) {
        learnGroupHome(t);
        syncRoute();
      }
      t.included_workstreams = await includedWorkstreams(t.id);
      if (state.selected !== id || generation !== state.generation) return;
    }
    state.task = t;
    // A refresh keeps an open note or Notes panel, read again.
    if (state.panel) return void (await renderPanel());
    const scroll = $("detail").scrollTop;
    renderDetail(t);
    $("detail").scrollTop = quiet ? scroll : 0;
    loadNoteLists();
  } catch (e) {
    if (generation === state.generation) toast(e.message, true);
  } finally {
    if (generation === state.generation) $("detail").classList.remove("loading");
  }
}
function section(title, content, extra) {
  return el("section", "block", el("h3", "block-title", node("span", title), extra), content);
}
// listBack: false leaves out the phone-only Back to list where the crumbs already lead back.
function topBar(crumbs, actions, { listBack = true } = {}) {
  const back = listBack ? iconButton("back", "Back to list", () => $("shell").classList.remove("detail-open"), "icon-btn only-mobile") : null;
  // On a phone the detail pane replaces the list, so the DEV badge is repeated here.
  return el("div", "topbar", back, devBadge("only-mobile"), el("div", "crumbs", ...crumbs), el("div", "top-actions", ...actions));
}
function renderDetail(t) {
  const d = $("detail");
  pendingNoteLists = [];
  if (t.object_type === "group") return renderGroup(t);
  const standing = taskStanding(t);
  // A completed task cannot change, so it offers no actions.
  const actions = [];
  if (t.status !== "done") {
    const menu = button("Actions", (e) => actionsMenu(e.currentTarget, t));
    menu.setAttribute("aria-haspopup", "menu");
    menu.title = "Ask a question, change workstreams, or defer, resume or drop this task";
    actions.push(menu);
  }
  const crumbs = [node("span", projectName(t.project_id))];
  if (state.stream) crumbs.push(node("span", "/", "sep"), branchLabel(streamName(state.stream)));
  else if (state.unassigned) crumbs.push(node("span", "/", "sep"), node("span", "Unassigned"));

  const head = el(
    "header",
    "detail-head",
    node("h2", t.title, "title"),
    taskIdHeader(t.id),
    el(
      "div",
      "meta",
      pill(standing),
      t.workstream_ids?.length
        ? el("span", "meta-item", icon("branch", 13), "In " + t.workstream_ids.map(streamName).join(", "))
        : node("span", "Unassigned", "meta-item warn"),
      node("span", "Updated " + ago(t.updated_at), "meta-item"),
      t.parent_group
        ? button(t.parent_group.title, () => openGroup(t.parent_group.id), "chip")
        : null,
    ),
  );
  if (t.summary) {
    head.append(node("p", t.summary, "muted"));
    if (t.summary_stale) head.append(node("p", "Descriptive summary predates the current specification.", "muted"));
  }
  // Identity, status and the next step stay above both tabs.
  d.replaceChildren(topBar(crumbs, actions), el("div", "content", head, nextStep(t, standing), ...detailTabs(t, body(t))));
}
function currentAttempt(t, requiredStates = null) {
  if (!requiredStates && t.status === "done" && t.selected_attempt_id)
    return t.attempts.find((a) => a.id === t.selected_attempt_id) || null;
  return [...t.attempts].sort((a, b) =>
    (b.created_at || "").localeCompare(a.created_at || "") || a.id.localeCompare(b.id)
  ).find((a) =>
    a.spec_revision === t.spec_revision &&
    (!state.stream || a.workstream_id === state.stream) &&
    (!requiredStates || requiredStates.includes(a.state))
  ) || null;
}
// What, if anything, the user can do next, stated plainly. The standing is where the
// task is for the user (see taskStanding); prose names no attempt IDs, agent labels or
// internal workflow stages. The browser shows and steers: agents write and decide with
// the user, so sign-off hands a ready prompt to an agent instead of recording a verdict.
function nextStep(t, standing) {
  const member = t.workstream_ids?.length && !(state.stream && !t.workstream_ids.includes(state.stream));
  const blockers = (t.prerequisites || []).filter((p) => p.blocking);
  const blocking = blockers.length > 0;
  // A result that passed review stays with the user for sign-off even while a
  // prerequisite is open; the walkthrough weighs the blocker.
  const passed = standing === "signoff" ? currentAttempt(t, ["passed", "human_review"]) : null;
  let title, text, buttons = [], extra = null, signoff = false;
  if (t.status === "done") {
    title = "Signed off";
    text = "This task is complete. A new requirement becomes a new task.";
  } else if (standing === "deferred" || standing === "dropped") {
    title = standing === "deferred" ? "Deferred" : "Dropped";
    text = standing === "deferred"
      ? "Its workstreams, details and results are kept for when it resumes."
      : "Its workstreams, details and results are kept. Resume brings it back.";
    buttons.push(button("Resume", () => resumeTask(t), "btn primary"));
  } else if (isIdea(t)) {
    title = "An idea waiting to be processed";
    text = "Nothing is built from an idea as it stands. Go through it with an agent: together you turn it into a proper brief or task, split it up, or drop it.";
    extra = node("p", "Copy the prompt, paste it into your agent, and decide together.", "muted");
    const prompt = ideaPrompt(t);
    buttons.push(button("Go through it with an agent", (e) => copyPrompt(prompt, e.currentTarget, "walkthrough"), "btn primary"));
  } else if (!member && !passed) {
    // A result that passed review keeps its sign-off action in any placement; Add to
    // workstream stays in the Actions menu.
    title = "Add this task to a workstream";
    text = "Choose the branch where this task should be built. Its questions and prerequisites still apply.";
    if (canDesign(t)) text += " You can talk its unresolved items through with an agent first; that needs no workstream.";
    buttons.push(button(membershipLabel(t), () => addToWorkstreamTask(t).catch((e) => toast(e.message, true)), "btn primary"));
  } else if (t.unresolved_items.length) {
    const n = t.unresolved_items.length;
    title = n === 1 ? "An unresolved item needs your answer" : `${n} unresolved items need your answers`;
    text = "Work on this task waits until each question below is settled. Design with agent, beside the questions, copies a prompt for your agent, who records the answers with you.";
  } else if (passed) {
    signoff = true;
    title = "Ready for your sign-off";
    text = passed.state === "human_review" ? "You reviewed the delivered work below yourself." : "The delivered work below passed independent review.";
    if (blocking) {
      const names = blockers.map((p) => `“${p.title}”`).join(", ");
      text += blockers.length === 1
        ? ` A prerequisite is still open: ${names}. Weigh it in the walkthrough.`
        : ` ${blockers.length} prerequisites are still open: ${names}. Weigh them in the walkthrough.`;
    }
    extra = node("p", "Sign-off is a walkthrough with an agent: copy the prompt, paste it into your agent, and decide together.", "muted");
    const prompt = signoffPrompt(t);
    buttons.push(button("Sign off with an agent", (e) => copyPrompt(prompt, e.currentTarget), "btn primary"));
  } else if (blocking) {
    title = "Waiting on prerequisites";
    text = "Work on this task waits for the prerequisites below: reviewed by default, or signed off where marked.";
  } else if (standing === "signoff") {
    return null;
  } else if (standing === "progress") {
    title = "In progress";
    const pick = currentPick(t), when = pick && pickedAgo(pick.picked_at);
    if (currentAttempt(t, ["review"]))
      text = "A result is recorded and is with the agents for independent review." + (pick?.action === "review" ? ` A reviewer picked it up ${when}.` : "");
    else if (currentAttempt(t, ["rework"]))
      text = "A result was sent back for changes and the agents are fixing it." + (pick ? ` An agent picked it up ${when}.` : "");
    else text = `An agent picked this up ${when || "recently"}. No result is recorded yet.`;
  } else {
    title = "Open";
    text = "No result is recorded yet. An agent can pick this up.";
  }
  if (!signoff && !isIdea(t) && (!member || t.unresolved_items.length || blocking || ["deferred", "dropped"].includes(t.status))) {
    if (t.attempts.some((x) => x.spec_revision === t.spec_revision && (!state.stream || x.workstream_id === state.stream)))
      text += " A recorded result is kept in Activity; it does not change this.";
  }
  const tone = (STANDINGS[standing] || STANDINGS.open).tone;
  return el(
    "div",
    "next tone-" + (t.status === "done" ? "done" : tone),
    el("div", "next-text", node("strong", title), node("p", text), extra),
    buttons.length ? el("div", "next-actions", ...buttons) : null,
  );
}
// Plain text an agent understands: the full task ID and its title.
function signoffPrompt(t) {
  return `Sign off ${t.id} — ${t.title}`;
}
function ideaPrompt(t) {
  return `Go through my ideas, starting with ${t.id} — ${t.title}`;
}
// Any open task with unresolved items can be talked through with an agent, whatever its
// workstreams; deferred, dropped and completed tasks keep only their lifecycle actions.
function canDesign(t) {
  return !CLOSED.includes(t.status) && (t.unresolved_items || []).length > 0;
}
// The prompt names the task, not its questions, so the agent reads the current ones.
// It asks for discussion and recorded decisions only; it authorizes no implementation.
function designPrompt(t) {
  return `Design with me: ${t.id} — ${t.title}. ` +
    "Read its current specification and unresolved items, work through each item with me, and record the decisions we agree on. " +
    "If the task is a captured feature brief, design it with the task-design skill; otherwise handle each item according to its own context, since not every question is a feature brief. " +
    "Discuss and record decisions only; do not implement anything.";
}
// Copy a prompt for the user to paste into an agent. The browser launches no agent.
// The Clipboard API needs a secure context (127.0.0.1 is one); when it is missing or
// refused, the prompt is shown selected beside the button so the user can copy it.
async function copyPrompt(text, anchor, purpose = "sign-off") {
  try {
    if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
    await navigator.clipboard.writeText(text);
    toast(`Copied. Paste it into your agent to start the ${purpose}.`);
  } catch {
    const host = anchor?.closest?.(".prompt-host") || anchor?.closest?.(".next");
    let box = host?.querySelector(".prompt-copy");
    if (host && !box) {
      box = node("input", undefined, "prompt-copy");
      box.readOnly = true;
      box.setAttribute("aria-label", "Prompt to copy");
      host.append(box);
    }
    if (box) {
      box.value = text;
      box.focus();
      box.select();
    }
    toast("Couldn't copy automatically. The prompt is selected: press Ctrl+C (⌘C on a Mac) to copy it.", true);
  }
}
// The task's current unresolved items, in stored order and in full: a plain list with
// thin dividers under a counted heading. Answered items leave this list; their history
// stays in Activity. The section is omitted when no current item exists.
function unresolvedSection(t) {
  const items = t.unresolved_items;
  const n = items.length;
  const list = el("ol", "questions");
  list.setAttribute("aria-labelledby", "unresolved-title");
  items.forEach((q) => list.append(el("li", "question", markdown(q.text))));
  // The count is shown as a badge; assistive technology reads it as words with the heading.
  const count = node("span", String(n), "question-count");
  count.setAttribute("aria-hidden", "true");
  const title = el("h3", "block-title", node("span", "Unresolved items"), count,
    node("span", n === 1 ? "(1 current item)" : `(${n} current items)`, "sr-only"));
  title.id = "unresolved-title";
  // The one Design with agent action sits beside this heading, not in the next step.
  // Its row also hosts the selectable prompt when the clipboard cannot be written.
  const prompt = canDesign(t) && designPrompt(t);
  const head = el("div", prompt ? "block-head prompt-host" : "block-head", title,
    prompt && button("Design with agent", (e) => copyPrompt(prompt, e.currentTarget, "design discussion"), "btn small"));
  const out = el("section", "block unresolved", head, list);
  out.setAttribute("aria-labelledby", "unresolved-title");
  return out;
}
function body(t) {
  const out = [];
  if (t.unresolved_items.length) out.push(unresolvedSection(t));
  if (t.blocked_by.length) {
    const list = el("div", "links");
    t.prerequisites.forEach((p) => {
      const b = button("", async () => {
        if (p.object_type === "group") await openGroup(p.id);
        else await navigateMember(p);
      }, "link-row");
      const project = p.object_type === "group" ? "Global group" : `${p.project_name} · ${p.project_id}`;
      b.append(
        el("span", "grow prerequisite-ref", node("span", p.title), node("small", `${p.id} · ${project} · ${p.milestone === "signoff" ? "Sign-off required" : "Review required"}`, "muted")),
        pill(p.satisfied ? "done" : "open", p.satisfied ? (p.complete ? "Satisfied · complete" : "Satisfied · reviewed") : `Blocking · ${p.state}`),
        icon("link", 14),
      );
      list.append(b);
    });
    out.push(section("Prerequisites", list));
  }
  // The one result shown on Spec; every result, review and decision is in Activity.
  const current = relevantResult(t);
  if (current) out.push(resultCard(current, t));
  out.push(section("Specification", markdown(t.body)));
  const criteria = markdown(t.acceptance_criteria, { checklist: true });
  criteria.classList.add("criteria");
  out.push(section("Acceptance criteria", criteria));
  if (t.user_request) out.push(section(`Request (${t.source || "unknown"} origin)`, markdown(t.user_request)));
  out.push(notesSection({ kind: "task", id: t.id }, { empty: "No notes reference this task yet." }));
  if (t.gate_proposals.length) {
    const list = el("div", "proposals");
    t.gate_proposals.forEach((p) =>
      list.append(el("div", "proposal", pill("open", p.state || p.status || "proposed"), markdown(p.gate_type === "prerequisite" ? `${p.detail} · ${p.milestone === "signoff" ? "Sign-off required" : "Review required"} (nonblocking proposal)` : (p.text || p.kind || "Gate proposal")))),
    );
    out.push(el("details", "fold", node("summary", `Gate proposals (${t.gate_proposals.length})`), list));
  }
  return out;
}
// The result Spec shows, if any: for a task awaiting sign-off, the reviewed result for
// the current specification in view; for a completed task, the accepted result; otherwise
// the latest current-specification result still with the agents (under review or sent
// back). "In view" is the open workstream, or every workstream on project-wide boards.
function relevantResult(t) {
  if (t.status === "done") return currentAttempt(t);
  if (taskStanding(t) === "signoff") {
    const reviewed = currentAttempt(t, ["passed", "human_review"]);
    if (reviewed) return reviewed;
  }
  return currentAttempt(t, ["review", "rework"]);
}
function resultWords(a, t) {
  if (t.status === "done") return "Accepted at sign-off";
  return {
    passed: "Passed independent review; waiting for your sign-off",
    human_review: "You reviewed it; waiting for your sign-off",
    review: "With the agents: waiting for independent review",
    rework: "With the agents: sent back for changes",
  }[a.state] || VIEWS[a.state]?.label || a.state;
}
// Whether a result was built against the current specification.
function specWords(revision, t) {
  if (revision === undefined || revision === null) return "";
  return revision === t.spec_revision ? `spec v${revision} · current` : `spec v${revision} · superseded (now v${t.spec_revision})`;
}
function workstreamWords(id, name) {
  return (name || streamName(id)) + (state.stream && id && id !== state.stream ? " (other workstream)" : "");
}
// A compact card for the current result: what was built, beside what was asked. Full
// evidence, verification, review and concerns stay in its Activity entry.
function resultCard(a, t) {
  const tone = t.status === "done" ? "done" : (VIEWS[a.state] || {}).tone || "muted";
  const title = el("h3", "block-title", node("span", "Current result"));
  title.id = "current-result-title";
  const card = el("article", "result-card",
    el("div", "result-state", node("span", resultWords(a, t), "pill tone-" + tone)),
    a.summary ? markdown(a.summary) : null,
    el("p", "result-meta muted", el("span", "meta-item", icon("branch", 13), workstreamWords(a.workstream_id)), node("span", specWords(a.spec_revision, t), a.spec_revision === t.spec_revision ? "" : "warn-text-inline")),
  );
  if (a.artifacts?.length) {
    const handles = el("ul", "handles");
    handles.setAttribute("aria-label", "Evidence handles");
    a.artifacts.forEach((ref) => handles.append(el("li", "", node("span", ref.kind, "handle-kind"), node("code", ref.reference))));
    card.append(handles);
  }
  card.append(el("div", "result-actions", button("Open full result", () => openFullResult(t, a.id).catch((e) => toast(e.message, true)), "btn small")));
  const out = el("section", "block current-result", title, card);
  out.setAttribute("aria-labelledby", "current-result-title");
  return out;
}
function attemptCard(a, t) {
  const other = state.stream && a.workstream_id !== state.stream;
  const superseded = a.spec_revision !== t.spec_revision;
  const card = el(
    "article",
    "attempt",
    el(
      "div",
      "attempt-head",
      pill(a.state),
      node("span", a.implementer, "who"),
      node("span", "on " + streamName(a.workstream_id) + (other ? " (other workstream)" : ""), "muted"),
      node("span", ago(a.created_at), "muted push"),
    ),
    markdown(a.summary),
  );
  if (superseded) card.append(node("p", `Built against spec v${a.spec_revision}; the spec has changed since.`, "warn-text"));
  card.append(el("div", "sub", node("h4", "Evidence"), markdown(a.evidence)));
  if (a.artifacts) card.append(el("div", "sub", node("h4", "Durable artifacts"),
    ...a.artifacts.map((ref) => node("p", `${ref.kind}: ${ref.reference}`))));
  if (a.verification) card.append(el("div", "sub", node("h4", "Actual verification"), markdown(a.verification)));
  if (a.review_note) card.append(el("div", "sub", node("h4", "Review by " + (a.reviewer || "reviewer")), markdown(a.review_note)));
  if (a.human_review_note) card.append(el("div", "sub", node("h4", "Your review"), markdown(a.human_review_note)));
  const concerns = concernPanel(a);
  if (concerns) card.append(concerns);
  return card;
}
// Concerns on a result, with their provenance.
function concernPanel(a) {
  if (!a.concerns?.length) return null;
  const panel = el("div", "sub", node("h4", "Worth-doing and approach concerns"),
    node("p", `Result ${a.id} · ${streamName(a.workstream_id)} (${a.workstream_id}) · spec ${a.spec_revision}. Concerns do not block review or sign-off.`, "muted"));
  for (const concern of a.concerns) {
    const kind = concern.kind === "value" ? "Worth-doing concern" : "Approach concern";
    const source = concern.source === "implementer" ? "Implementer" : "Reviewer";
    panel.append(node("h4", `${kind} · ${source} ${concern.author}`), markdown(concern.text));
  }
  return panel;
}
function renderGroup(t) {
  const d = $("detail");
  const p = t.progress;
  const projects = Object.keys(p.by_project).length;
  const members = el("div", "links");
  t.member_details.forEach((m) => {
    const b = button("", () => navigateMember(m), "link-row");
    b.append(
      node("span", "", "dot tone-" + (m.status === "done" ? "done" : m.status === "open" ? "ready" : "muted")),
      node("span", m.title, "grow"),
      node("span", projectName(m.project_id), "muted"),
      icon("link", 14),
    );
    members.append(b);
  });
  if (!t.member_details.length) members.append(node("p", "No members yet. An empty group is not complete.", "empty-text"));
  const workstreams = el("div", "links");
  t.included_workstreams.forEach((w) => {
    const b = button("", () => navigateWorkstream(w), "link-row");
    b.append(
      icon("branch", 14),
      node("span", w.project_name, "muted"),
      branchLabel(w.branch || w.name),
      ...(isArchived(w) ? [node("span", "archived", "tag-archived")] : []),
      icon("link", 14),
    );
    b.title = `${w.project_name} · ${w.branch || w.name} · ${w.checkout_path}` + (isArchived(w) ? ` · archived: ${w.archive.reason}` : "");
    workstreams.append(b);
  });
  if (!t.included_workstreams.length)
    workstreams.append(node("p", "Not included as a group in any workstream. Member tasks may be scoped individually.", "empty-text"));
  d.replaceChildren(
    topBar([node("span", groupKind(t)), state.groups === "project" && !state.rows.some((r) => r.id === t.id) ? node("span", "Opened from a task link", "muted") : null], []),
    el(
      "div",
      "content",
      el(
        "header",
        "detail-head",
        node("h2", t.title, "title"),
        taskIdHeader(t.id),
        el("div", "meta", pill(t.complete ? "done" : "review", t.complete ? "Complete" : "In progress"), node("span", `${p.done} of ${p.total} signed off · ${projects} project${projects === 1 ? "" : "s"}`, "meta-item")),
        progressBar(p.done, p.total),
      ),
      ...detailTabs(t, [
        t.summary ? section(t.summary_stale ? "Descriptive summary (predates current specification)" : "Descriptive summary", node("p", t.summary)) : null,
        section("Context", markdown(t.body)),
        section("Done when", markdown(t.acceptance_criteria, { checklist: true })),
        notesSection({ kind: "group", id: t.id }, { empty: "No notes reference this group yet." }),
        section("Included in workstreams", workstreams),
        section("Members", members),
        node("p", "Group progress counts members in every project. It doesn't mean one workstream delivered them all.", "footnote"),
      ]),
    ),
  );
}
async function navigateWorkstream(w) {
  closeDrawer();
  const generation = ++state.generation;
  const streams = await workstreams(w.project_id);
  if (generation !== state.generation) return;
  state.project = w.project_id;
  state.streams = streams;
  state.stream = w.id;
  state.unassigned = false;
  state.groups = false;
  state.linkedGroup = null;
  state.selected = null;
  state.task = null;
  syncRoute("push");
  renderNav();
  await reload();
}
async function navigateMember(m) {
  state.selected = m.id;
  await chooseProject(m.project_id, { keepSelection: true });
}
async function openGroup(id) {
  const generation = ++state.generation;
  const t = (await api("details", { ids: [id] })).items[0];
  if (generation !== state.generation) return;
  learnGroupHome(t);
  const projects = Object.keys(t.progress.by_project);
  if (projects.length === 1 && projects[0] !== state.project) {
    const streams = await workstreams(projects[0]);
    if (generation !== state.generation) return;
    state.project = projects[0];
    state.streams = streams;
  }
  state.groups = projects.length > 1 ? "shared" : "project";
  state.linkedGroup = id;
  state.stream = null;
  state.unassigned = false;
  state.selected = id;
  syncRoute("push");
  renderNav();
  await reload();
}
/* ---------- Spec and Activity tabs ---------- */

// The selected task's or group's tab and Activity history. Selecting another object
// opens Spec; a refresh of the same object keeps its tab, its loaded history, the results
// opened in it and the scroll position.
let detailView = { id: null, tab: "spec", feed: null, box: null, task: null };
function viewFor(id) {
  if (detailView.id !== id) detailView = { id, tab: "spec", feed: null, box: null, task: null };
  return detailView;
}
const DETAIL_TABS = [
  { key: "spec", label: "Spec" },
  { key: "activity", label: "Activity" },
];
// An ARIA tablist (arrow keys, Home and End move between the tabs) and its two panels.
// Spec holds what was asked and where it stands; Activity holds the history.
function detailTabs(t, specContent) {
  const view = viewFor(t.id);
  view.task = t;
  const list = el("div", "tabs");
  list.id = "detail-tabs";
  list.setAttribute("role", "tablist");
  list.setAttribute("aria-label", t.object_type === "group" ? "Group details" : "Task details");
  const panels = [];
  for (const { key, label } of DETAIL_TABS) {
    const selected = view.tab === key;
    const tab = button(label, () => showTab(key), "tab");
    tab.id = "tab-" + key;
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", String(selected));
    tab.setAttribute("aria-controls", "panel-" + key);
    tab.tabIndex = selected ? 0 : -1;
    tab.onkeydown = (e) => tabKey(e, key);
    list.append(tab);
    const panel = key === "spec" ? el("div", "tab-panel", ...specContent) : el("div", "tab-panel", activity(t));
    panel.id = "panel-" + key;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", tab.id);
    panel.tabIndex = 0;
    panel.hidden = !selected;
    panels.push(panel);
  }
  return [list, ...panels];
}
function tabKey(e, key) {
  const keys = DETAIL_TABS.map((x) => x.key);
  const i = keys.indexOf(key);
  const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: keys.length - 1 }[e.key];
  if (next === undefined) return;
  e.preventDefault();
  e.stopPropagation();
  showTab(keys[(next + keys.length) % keys.length], { focus: true });
}
function showTab(key, { focus = false } = {}) {
  const view = detailView;
  if (!view.id) return;
  view.tab = key;
  for (const { key: other } of DETAIL_TABS) {
    const tab = $("tab-" + other), panel = $("panel-" + other);
    if (!tab || !panel) continue;
    const on = other === key;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
    panel.hidden = !on;
  }
  if (focus) $("tab-" + key)?.focus?.();
  revealPanel(key);
  if (key === "activity") startFeed(view);
}
// Scrolled past the tabs, a newly shown panel starts just below them (they stay pinned).
function revealPanel(key) {
  const pane = $("detail"), panel = $("panel-" + key), tabs = $("detail-tabs");
  if (!panel?.getBoundingClientRect || !tabs?.getBoundingClientRect) return;
  const offset = panel.getBoundingClientRect().top - tabs.getBoundingClientRect().bottom;
  if (offset < 0) pane.scrollTop += offset;
}

/* ---------- Activity ---------- */

// Activity is the server's paged history (the viewer's "activity" read): meaningful
// entries only, newest first, ACTIVITY_PAGE per page. The feed is a list of loaded
// segments, newest first. Usually there is one; opening a result outside the loaded
// pages adds the page that starts with it, and "Show newer" adds the newest page. A gap
// between two segments loads with the upper segment's cursor until they meet.
const ACTIVITY_PAGE = 20;
const activityKey = (e) => [e.sequence, e.kind, e.attempt_id || "", e.member_id || ""].join(":");
// One page of at most ACTIVITY_PAGE entries. A short page whose scan reached the server's
// bound (scan_limited) continues automatically from its cursor, so hidden events never
// leave the user with a short page.
async function activityPage(task_id, from = {}) {
  let page = await api("activity", { task_id, ...from });
  let items = page.items || [], cursor = page.next_cursor ?? null;
  for (let follow = 0; page.scan_limited && cursor !== null && items.length < ACTIVITY_PAGE && follow < 50; follow++) {
    page = await api("activity", { task_id, cursor });
    items = items.concat(page.items || []);
    cursor = page.next_cursor ?? null;
  }
  if (items.length > ACTIVITY_PAGE) {
    // Cut at a sequence boundary: an import and its results stay together.
    let cut = ACTIVITY_PAGE;
    while (cut > 0 && items[cut].sequence === items[cut - 1].sequence) cut--;
    if (!cut) cut = items.findIndex((e) => e.sequence !== items[0].sequence);
    if (cut > 0) {
      cursor = items[cut - 1].sequence;
      items = items.slice(0, cut);
    }
  }
  return { items, cursor };
}
// Entries of two loads, newest first, each once; a later copy replaces an earlier one.
function mergeItems(a, b) {
  const byKey = new Map();
  for (const e of [...a, ...b]) byKey.set(activityKey(e), e);
  return [...byKey.values()].sort((x, y) => y.sequence - x.sequence);
}
// Sort segments newest first and join those with nothing unloaded between them.
function normalizeSegments(segments) {
  const top = (s) => (s.items.length ? s.items[0].sequence : s.cursor - 1);
  const sorted = segments.filter((s) => s.items.length || s.cursor !== null).sort((a, b) => top(b) - top(a));
  const out = [];
  for (const s of sorted) {
    const a = out[out.length - 1];
    const bottom = a?.items.length ? a.items[a.items.length - 1].sequence : null;
    if (a && (a.cursor === null || a.cursor <= top(s) + 1 || (bottom !== null && bottom <= top(s)))) {
      a.items = mergeItems(a.items, s.items);
      a.cursor = a.cursor === null || s.cursor === null ? null : Math.min(a.cursor, s.cursor);
    } else out.push(s);
  }
  return out;
}
function addSegment(feed, page) {
  feed.segments = normalizeSegments([...feed.segments, { items: page.items, cursor: page.cursor }]);
}
const topSequence = (feed) => feed.segments[0]?.items[0]?.sequence ?? null;
function findResult(feed, attemptId) {
  return feed.segments.some((s) => s.items.some((e) => e.kind === "result" && e.attempt_id === attemptId));
}
function newFeed() {
  return { segments: [], loaded: false, busy: null, pending: null, error: null, newer: false,
    open: new Map(), checkedAt: 0, focus: null, status: "" };
}
// The Activity panel's content, rebuilt from the feed state on every render.
function activity(t) {
  const view = viewFor(t.id);
  view.task = t;
  if (!view.feed) view.feed = newFeed();
  const box = el("div", "activity");
  box.setAttribute("aria-busy", "false");
  view.box = box;
  renderFeed(view);
  if (view.tab === "activity") startFeed(view);
  return box;
}
// Load the first page when Activity is first shown; afterwards check for newer entries.
function startFeed(view) {
  const feed = view.feed;
  if (!feed || feed.busy) return;
  if (!feed.loaded) {
    if (!feed.error) loadFirst(view);
  } else checkNewer(view);
}
// Run one load at a time; a failure keeps everything loaded and offers Retry there.
function feedRequest(view, busy, work, retry) {
  const feed = view.feed;
  if (feed.busy) return Promise.resolve(false);
  feed.busy = busy;
  feed.error = null;
  renderFeed(view);
  feed.pending = (async () => {
    try {
      await work(feed);
      return true;
    } catch (e) {
      feed.error = { ...busy, message: e.message, retry };
      return false;
    } finally {
      feed.busy = null;
      if (detailView === view) renderFeed(view);
    }
  })();
  return feed.pending;
}
function loadFirst(view) {
  return feedRequest(view, { kind: "first" }, async (feed) => {
    const page = await activityPage(view.id);
    feed.segments = normalizeSegments([{ items: page.items, cursor: page.cursor }]);
    feed.loaded = true;
    feed.newer = false;
    feed.checkedAt = Date.now();
    feed.status = page.items.length ? `${page.items.length} entries loaded.` : "No activity yet.";
  }, () => loadFirst(view));
}
// Load the next page below a segment: Load more at the end, or a gap between segments.
function loadOlder(view, segment) {
  return feedRequest(view, { kind: "more", segment }, async (feed) => {
    const page = await activityPage(view.id, { cursor: segment.cursor });
    if (!feed.segments.includes(segment)) return;
    const known = new Set(segment.items.map(activityKey));
    segment.items = mergeItems(segment.items, page.items);
    segment.cursor = page.cursor;
    feed.segments = normalizeSegments(feed.segments);
    const first = page.items.find((e) => !known.has(activityKey(e)));
    feed.focusEntry = first ? activityKey(first) : null;
    feed.status = page.items.length ? `${page.items.length} more entries loaded.` : "No more entries.";
  }, () => loadOlder(view, segment));
}
function loadNewer(view) {
  return feedRequest(view, { kind: "newer" }, async (feed) => {
    const page = await activityPage(view.id);
    addSegment(feed, page);
    feed.newer = false;
    feed.checkedAt = Date.now();
    feed.status = "Newer entries loaded.";
  }, () => loadNewer(view));
}
// Offer "Show newer" when the server has entries above the loaded ones. A quiet check:
// a failure leaves the feed as it is and the next refresh checks again.
async function checkNewer(view) {
  const feed = view.feed;
  if (!feed.loaded || feed.busy || feed.newer || Date.now() - feed.checkedAt < 3000) return;
  feed.checkedAt = Date.now();
  try {
    const page = await api("activity", { task_id: view.id });
    const newest = page.newest_sequence ?? null, top = topSequence(feed);
    if (newest !== null && (top === null || newest > top) && !feed.newer) {
      feed.newer = true;
      feed.status = "Newer activity is available.";
      if (detailView === view) renderFeed(view);
    }
  } catch {
    // Nothing to show: the loaded history stays and a later refresh checks again.
  }
}
// Open full result: show Activity and that result's entry, opened. A result outside the
// loaded pages is fetched with the page that starts with it, not the history above it.
async function openFullResult(t, attemptId) {
  const view = viewFor(t.id);
  view.task = t;
  if (!view.feed) view.feed = newFeed();
  showTab("activity");
  const feed = view.feed;
  while (feed.busy) await feed.pending;
  if (detailView !== view) return;
  const retry = () => openFullResult(t, attemptId);
  if (!feed.loaded && !(await loadFirst(view))) {
    if (feed.error) feed.error.retry = retry;
    return renderFeed(view);
  }
  if (!findResult(feed, attemptId)) {
    const found = await feedRequest(view, { kind: "target" }, async (feed) => {
      addSegment(feed, await activityPage(view.id, { target: attemptId }));
      feed.status = "The result and the entries before it are loaded.";
    }, retry);
    if (!found || detailView !== view) return;
  }
  feed.focus = attemptId;
  if (feed.open.get(attemptId)?.attempt) return renderFeed(view);
  await toggleResult(view, attemptId, { open: true });
}
// Opening a result entry reads its complete proof (the attempt read): evidence,
// artifacts, verification, review and concerns.
async function toggleResult(view, attemptId, { open = false } = {}) {
  const feed = view.feed;
  if (feed.open.has(attemptId) && !open) {
    feed.open.delete(attemptId);
    return renderFeed(view);
  }
  const slot = { loading: true };
  feed.open.set(attemptId, slot);
  renderFeed(view);
  try {
    slot.attempt = await api("attempt", { attempt_id: attemptId });
  } catch (e) {
    slot.error = e.message;
  }
  slot.loading = false;
  if (detailView === view && feed.open.get(attemptId) === slot) renderFeed(view);
}
function feedButton(label, key, fn, busy = false) {
  const b = button(busy ? "Loading…" : label, fn, "btn small");
  b.dataset.focusKey = key;
  b.disabled = busy;
  return b;
}
function renderFeed(view) {
  const { feed, box } = view;
  if (!feed || !box) return;
  const pane = $("detail");
  const scroll = pane?.scrollTop;
  const focused = document.activeElement?.dataset?.focusKey;
  const busy = feed.busy;
  const failed = (kind, segment) => {
    const e = feed.error;
    if (!e || e.kind !== kind || (segment && e.segment !== segment)) return null;
    const what = { first: "activity", more: "more entries", newer: "newer entries", target: "that result" }[kind];
    return el("p", "feed-error", node("span", `Couldn't load ${what}: ${e.message}`), feedButton("Retry", "retry", () => e.retry()));
  };
  const status = node("p", feed.status, "sr-only");
  status.setAttribute("role", "status");
  const parts = [status];
  if (feed.newer || busy?.kind === "newer")
    parts.push(el("div", "feed-bar", node("span", "Newer activity is available."), feedButton("Show newer", "newer", () => loadNewer(view), busy?.kind === "newer")));
  parts.push(failed("newer"));
  if (!feed.loaded) {
    parts.push(failed("first") || node("p", "Loading activity…", "feed-loading muted"));
  } else if (!feed.segments.length) {
    parts.push(el("div", "feed-empty", node("p", "No activity yet."),
      node("p", "Results, reviews, decisions, questions and changes appear here, newest first. Routine reads are not shown.", "muted")));
  } else {
    const list = el("ol", "timeline activity-feed");
    list.setAttribute("aria-label", "Activity, newest first");
    feed.segments.forEach((segment, i) => {
      segment.items.forEach((e) => list.append(activityEntry(e, view)));
      if (i < feed.segments.length - 1)
        list.append(el("li", "gap", node("span", "Entries between these are not loaded yet.", "muted"),
          feedButton("Load entries in between", "gap:" + i, () => loadOlder(view, segment), busy?.segment === segment), failed("more", segment)));
    });
    parts.push(list);
    const last = feed.segments[feed.segments.length - 1];
    if (last.cursor !== null)
      parts.push(el("div", "feed-more", feedButton("Load more", "more", () => loadOlder(view, last), busy?.segment === last), failed("more", last)));
    else parts.push(node("p", "Beginning of history.", "feed-end muted"));
  }
  parts.push(failed("target"));
  box.setAttribute("aria-busy", String(!!busy));
  box.replaceChildren(...parts.filter(Boolean));
  if (pane && scroll !== undefined) pane.scrollTop = scroll;
  // Keep keyboard focus where it was, move it to the first newly loaded entry, or to
  // the result that Open full result asked for.
  const find = (selector) => box.querySelector?.(selector);
  if (feed.focus) {
    const target = find(`li[data-attempt="${feed.focus}"]`);
    feed.targeted = feed.focus;
    feed.focus = null;
    if (target) {
      target.classList.add("targeted");
      target.scrollIntoView?.({ block: "start" });
      target.focus?.({ preventScroll: true });
    }
  } else if (feed.focusEntry) {
    find(`li[data-key="${feed.focusEntry}"]`)?.focus?.({ preventScroll: true });
    feed.focusEntry = null;
  } else if (focused) find(`[data-focus-key="${focused}"]`)?.focus?.({ preventScroll: true });
}
const DECISIONS = {
  approve: "Approved",
  rework: "Sent back for rework",
  revise: "Design to be revised",
  drop: "Dropped",
  defer: "Deferred",
};
const MEMBER_VIA = {
  create_task: "joined when it was created",
  update_task: "joined by an edit",
  add_group_member: "added to the group",
};
function activityEntry(e, view) {
  const t = view.task;
  const li = el("li", "entry kind-" + e.kind);
  li.dataset.key = activityKey(e);
  // Focus on an entry survives a re-render (an opened result's proof arriving, say).
  li.dataset.focusKey = "entry:" + li.dataset.key;
  if (e.kind === "result") li.dataset.attempt = e.attempt_id;
  if (e.kind === "result" && view.feed.targeted === e.attempt_id) li.classList.add("targeted");
  li.tabIndex = -1;
  const time = node("time", ago(e.timestamp), "muted push");
  if (e.timestamp) {
    time.title = new Date(e.timestamp).toLocaleString();
    time.setAttribute("datetime", e.timestamp);
  }
  li.append(el("div", "event", node("span", ACTIVITY_LABELS[e.kind] || e.kind, "what"), node("span", e.actor || "", "muted who"), time));
  li.append(...entryBody(e, view).filter(Boolean));
  return li;
}
const entryNote = (text, cls = "event-note") => (text ? node("p", text, cls) : null);
function resultMeta(e, t) {
  const parts = [workstreamWords(e.workstream_id, e.workstream_name), specWords(e.spec_revision, t)].filter(Boolean);
  const meta = node("p", parts.join(" · "), "entry-meta muted");
  if (e.spec_revision !== undefined && e.spec_revision !== t.spec_revision) meta.classList.add("superseded");
  return meta;
}
function entryBody(e, view) {
  const t = view.task;
  switch (e.kind) {
    case "result": {
      // The attempt's state now, from the task read; the entry's own if it is not there.
      const state = (t.attempts || []).find((a) => a.id === e.attempt_id)?.state || e.state;
      const current = t.object_type !== "group" && relevantResult(t)?.id === e.attempt_id;
      const slot = view.feed.open.get(e.attempt_id);
      const toggle = button(slot ? "Hide full result" : "Show full result", () => toggleResult(view, e.attempt_id), "btn small");
      toggle.dataset.focusKey = "result:" + e.attempt_id;
      toggle.setAttribute("aria-expanded", String(!!slot));
      toggle.setAttribute("aria-controls", "proof-" + e.attempt_id);
      const imported = e.imported ? node("span", "Imported", "tag") : null;
      if (imported) imported.title = "Imported in the TASKS.md migration";
      const out = [
        el("div", "entry-line", state ? pill(state) : null, imported, current ? node("span", "Current result", "tag current") : null,
          e.implementer ? node("span", "by " + e.implementer, "muted") : null),
        entryNote(e.summary),
        resultMeta(e, t),
        el("div", "entry-actions", toggle),
      ];
      if (slot) {
        const proof = el("div", "entry-proof",
          slot.loading ? node("p", "Loading the full result…", "muted") : null,
          slot.error ? el("p", "feed-error", node("span", `Couldn't load the full result: ${slot.error}`),
            feedButton("Retry", "retry:" + e.attempt_id, () => toggleResult(view, e.attempt_id, { open: true }))) : null,
          slot.attempt ? attemptCard(slot.attempt, t) : null);
        proof.id = "proof-" + e.attempt_id;
        out.push(proof);
      }
      return out;
    }
    case "review": {
      const verdict = e.verdict === "pass" ? pill("passed", "Passed") : e.verdict === "rework" ? pill("rework", "Sent back for changes") : pill("open", e.verdict || "Recorded");
      return [el("div", "entry-line", verdict, e.reviewer ? node("span", "by " + e.reviewer, "muted") : null), entryNote(e.reasons), resultMeta(e, t), resultLink(e, view)];
    }
    case "human_review":
      return [entryNote(e.reasons), resultMeta(e, t), resultLink(e, view)];
    case "signoff": {
      // Full reasons and historical judgments from the task's recorded decisions.
      const d = (t.signoff_decisions || []).find((x) => x.decision_ref === e.sequence);
      const words = DECISIONS[e.decision] || e.decision || "Decision";
      return [
        el("div", "entry-line", node("strong", words), e.disposition ? node("span", "· task " + e.disposition, "muted") : null),
        d ? markdown(d.reasons ?? d.user_note ?? "") : entryNote(e.reasons),
        d?.purpose_judgment ? entryNote(`Historical purpose: ${d.purpose_judgment} (${(d.purpose_source || "unknown").replaceAll("_", " ")}).`) : null,
        d?.result_judgment ? entryNote(`Historical human result quality: ${d.result_judgment.replaceAll("_", " ")}.`) : null,
        d?.result_note ? markdown(d.result_note) : null,
        e.spec_revision !== undefined ? resultMeta(e, t) : null,
        resultLink(e, view),
      ];
    }
    case "question_added":
      return [entryNote(e.text || e.summary)];
    case "question_resolved":
      return [entryNote(e.text || e.summary), e.note ? entryNote("Answer: " + e.note) : null];
    case "workstream_added":
    case "workstream_removed":
      return [entryNote(e.workstream_name || streamName(e.workstream_id))];
    case "disposition":
      return [entryNote(`${e.from || "?"} → ${e.to || "?"}`), entryNote(e.note)];
    case "prerequisite_added":
      return [entryNote(e.blocked_by_id + (e.milestone === "signoff" ? " · Sign-off required" : e.milestone ? " · Review required" : "")), entryNote(e.note)];
    case "member_added":
      return [entryNote(`${e.title || e.member_id}${e.title && e.member_id ? ` (${e.member_id})` : ""} · ${MEMBER_VIA[e.via] || "added"}`)];
    default:
      return [entryNote(e.summary), e.summary?.includes(e.note || "\u0000") ? null : entryNote(e.note)];
  }
}
// A review or decision links to the result it judged (opened in place, or fetched).
function resultLink(e, view) {
  if (!e.attempt_id || view.task.object_type === "group") return null;
  const b = button("Show the result", () => openFullResult(view.task, e.attempt_id).catch((err) => toast(err.message, true)), "link-btn");
  b.dataset.focusKey = "link:" + e.sequence;
  return el("div", "entry-actions", b);
}

/* ---------- notes ---------- */

// Titled notes are separate records that reference tasks, groups, workstreams and projects
// explicitly. The viewer lists the active notes where they are referenced (task and group
// details, and the Notes panel of the open workstream and project), opens one in the detail
// pane, and adds, edits, archives and unarchives notes through the Store's note calls at the
// note's revision. References come only from the picker; note text is never searched for them.
const NOTE_KINDS = { task: "Task", group: "Group", workstream: "Workstream", project: "Project" };
const NOTE_PAGE = 20;
const NOTE_TITLE_LIMIT = 120;
const NOTE_TEXT_LIMIT = 4000;
// Store refusals of a note's own fields, shown in plain words with the draft kept.
const NOTE_REFUSALS = /^(invalid_note_title|note_title_too_long|invalid_note_text|note_too_long|references_required|invalid_reference|unknown_reference|reference_kind_mismatch): /;
// Note lists that also show archived notes, by "kind:id", while this tab is open.
const archivedNoteLists = new Set();
// Each note list's last first page, by "kind:id", with whether it included archived
// notes. A re-render of the same list (a refresh) shows it at once while it is read
// again, so the Spec tab keeps its height and scroll position.
const shownNoteLists = new Map();
// Names of referenced entities this tab has read, by ID.
const referenceNames = new Map();
// Note lists rendered but not yet read. Rendering reads nothing; whoever shows the pane
// calls loadNoteLists() once the lists are in the page.
let pendingNoteLists = [];
const localError = (message) => Object.assign(new Error(message), { local: true });

function fullTime(iso) {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
}
function timeNode(iso, text) {
  const t = node("time", text, "muted");
  t.dateTime = iso;
  t.title = fullTime(iso);
  return t;
}
function noteKind(item) {
  return item.object_type === "group" ? "group" : "task";
}
// A reference's display name and project, from what this tab has loaded.
function referenceInfo(ref) {
  if (ref.kind === "project") {
    const p = state.projects.find((x) => x.id === ref.id);
    if (p) return { title: p.name, project_id: p.id };
  } else if (ref.kind === "workstream") {
    const s = state.streams.find((x) => x.id === ref.id);
    if (s) return { title: s.branch || s.name, project_id: s.project_id };
  } else {
    const t = [state.task, ...state.rows].find((x) => x?.id === ref.id && noteKind(x) === ref.kind);
    if (t) return { title: t.title, project_id: t.project_id };
  }
  return referenceNames.get(ref.id) || null;
}
function rememberWorkstreams(list) {
  list.forEach((w) => referenceNames.set(w.id, { title: w.branch || w.name, project_id: w.project_id }));
}
// Read the names of references this tab has not loaded: task and group details 20 at a
// time, every workstream, and the project list. A failed read leaves the bare ID shown.
async function nameReferences(refs) {
  const missing = refs.filter((r) => !referenceInfo(r));
  const ids = missing.filter((r) => r.kind === "task" || r.kind === "group").map((r) => r.id);
  for (let i = 0; i < ids.length; i += 20) {
    try {
      (await api("details", { ids: ids.slice(i, i + 20) })).items.forEach((t) =>
        referenceNames.set(t.id, { title: t.title, project_id: t.project_id }));
    } catch {}
  }
  if (missing.some((r) => r.kind === "workstream")) {
    try { rememberWorkstreams(await workstreams()); } catch {}
  }
  if (missing.some((r) => r.kind === "project")) {
    try { state.projects = await pages("projects"); } catch {}
  }
}
// Open the referenced task, group, workstream or project where it lives.
function openReference(ref) {
  const info = referenceInfo(ref);
  const go = ref.kind === "project" ? () => chooseProject(ref.id)
    : ref.kind === "workstream" ? () => navigateWorkstream({ id: ref.id, project_id: info.project_id })
    : ref.kind === "group" ? () => openGroup(ref.id)
    : () => navigateMember({ id: ref.id, project_id: info.project_id });
  return go().catch((e) => toast(e.message, true));
}

// The notes that reference one entity, by title and update time, newest-updated first.
// Archived notes appear only after Show archived. back says where an opened note's Back
// returns: null for the task or group in view, "scope" for the Notes panel.
function notesSection(ref, { title = "Notes", back = null, empty = "No notes reference this yet." } = {}) {
  const key = ref.kind + ":" + ref.id;
  const headingId = `notes-${ref.kind}-title`;
  // A neutral count: notes are context, not something waiting for the user.
  const count = node("span", "", "question-count note-count");
  count.hidden = true;
  const heading = el("h3", "block-title", node("span", title), count);
  heading.id = headingId;
  const list = el("div", "links note-list");
  list.setAttribute("role", "list");
  list.setAttribute("aria-labelledby", headingId);
  list.hidden = true;
  const status = node("p", "Loading notes…", "empty-text");
  const toggle = button("", () => {
    if (archivedNoteLists.has(key)) archivedNoteLists.delete(key);
    else archivedNoteLists.add(key);
    load();
  }, "btn small");
  toggle.hidden = true;
  const add = button("+ Note", () => noteDialog(null, { references: [ref], saved: (ack) => openNote(ack.id, back) }), "btn small");
  add.title = `Add a note that references this ${ref.kind}`;
  let next = null, turn = 0;
  const more = button("Show more", () => load(next), "btn small note-more");
  more.hidden = true;
  function show(page, offset, archived) {
    if (!offset) list.replaceChildren();
    page.items.forEach((n) => list.append(noteRow(n, back)));
    list.hidden = !page.total;
    next = page.next_offset;
    more.hidden = next === null;
    count.textContent = String(page.total);
    count.hidden = !page.total;
    const hidden = page.archived_hidden || 0;
    toggle.hidden = !archived && !hidden;
    toggle.textContent = archived ? "Hide archived" : `Show ${hidden} archived`;
    toggle.setAttribute("aria-pressed", String(archived));
    status.textContent = page.total ? "" : archived ? "No notes reference this, active or archived." : empty;
    status.hidden = !!page.total;
  }
  async function load(offset = 0) {
    const mine = ++turn;
    const archived = archivedNoteLists.has(key);
    try {
      const page = await api("note-list", { reference: ref, include_archived: archived, limit: NOTE_PAGE, offset });
      if (mine !== turn) return;
      if (!offset) shownNoteLists.set(key, { page, archived });
      show(page, offset, archived);
    } catch (e) {
      if (mine !== turn) return;
      status.textContent = `Couldn't load notes: ${e.message}`;
      status.hidden = false;
    }
  }
  const shown = shownNoteLists.get(key);
  if (shown && shown.archived === archivedNoteLists.has(key)) show(shown.page, 0, shown.archived);
  pendingNoteLists.push(() => load());
  const out = el("section", "block notes-block",
    el("div", "block-head", heading, el("div", "block-actions", toggle, add)), status, list, more);
  out.setAttribute("aria-labelledby", headingId);
  return out;
}
function loadNoteLists() {
  const loads = pendingNoteLists;
  pendingNoteLists = [];
  return Promise.all(loads.map((load) => load()));
}
function noteRow(n, back) {
  const b = button("", () => openNote(n.id, back), "link-row note-row");
  b.setAttribute("role", "listitem");
  b.append(node("span", n.title, "grow note-title"));
  if (n.archived) b.append(node("span", "archived", "tag-archived"));
  b.append(timeNode(n.updated_at, "Updated " + ago(n.updated_at)));
  return b;
}

// The open workstream's and project's notes, in the detail pane.
function openScopeNotes() {
  if (!state.project || state.groups === "shared") return;
  state.generation++;
  state.panel = { kind: "scope" };
  closeMenu();
  $("shell").classList.add("detail-open");
  $("detail").classList.remove("loading");
  renderScopeNotes();
  $("detail").scrollTop = 0;
}
function renderScopeNotes() {
  pendingNoteLists = [];
  const crumbs = [node("span", projectName(state.project))];
  if (state.stream) crumbs.push(node("span", "/", "sep"), branchLabel(streamName(state.stream)));
  crumbs.push(node("span", "/", "sep"), node("span", "Notes"));
  const lists = [];
  if (state.stream)
    lists.push(notesSection({ kind: "workstream", id: state.stream }, { title: "Workstream notes", back: "scope", empty: "No notes reference this workstream yet." }));
  lists.push(notesSection({ kind: "project", id: state.project }, { title: "Project notes", back: "scope", empty: "No notes reference this project yet." }));
  const actions = state.selected ? [button(state.groups ? "Back to group" : "Back to task", () => selectTask(state.selected), "btn small")] : [];
  $("detail").replaceChildren(topBar(crumbs, actions), el("div", "content",
    el("header", "detail-head",
      node("h2", `Notes · ${state.stream ? streamName(state.stream) : projectName(state.project)}`, "title"),
      node("p", state.stream
        ? "Notes that reference this workstream, and those that reference its project. Agents read and write the same notes through simtask."
        : "Notes that reference this project. Agents read and write the same notes through simtask.", "muted")),
    ...lists));
  return loadNoteLists();
}
function renderPanel() {
  if (state.panel?.kind === "note") return openNote(state.panel.id, state.panel.back, { quiet: true });
  return renderScopeNotes();
}

// One note in the detail pane: its title, full text, references and times.
async function openNote(id, back = null, { quiet = false } = {}) {
  const generation = ++state.generation;
  state.panel = { kind: "note", id, back };
  $("shell").classList.add("detail-open");
  if (!quiet) $("detail").classList.add("loading");
  try {
    const note = (await api("note-read", { ids: [id] })).items[0];
    await nameReferences(note.references);
    if (generation !== state.generation) return;
    const scroll = $("detail").scrollTop;
    renderNote(note, back);
    $("detail").scrollTop = quiet ? scroll : 0;
  } catch (e) {
    if (generation === state.generation) toast(e.message, true);
  } finally {
    if (generation === state.generation) $("detail").classList.remove("loading");
  }
}
function closeNote(back) {
  if (back === "scope") return openScopeNotes();
  state.panel = null;
  if (state.selected) return selectTask(state.selected);
  return reload({ quiet: true });
}
function renderNote(note, back) {
  const backLabel = back === "scope" ? "Notes" : state.task?.title || "Back";
  const backLink = button("", () => closeNote(back), "crumb-link");
  backLink.append(icon("back", 14), node("span", backLabel));
  backLink.title = back === "scope" ? "Back to the notes list" : "Back to " + backLabel;
  const edit = button("Edit", () => noteDialog(note, { saved: () => openNote(note.id, back, { quiet: true }) }), "btn small");
  const archive = button(note.archived ? "Unarchive" : "Archive", () => archiveNote(note, back, !note.archived), "btn small");
  archive.title = note.archived ? "List this note again" : "Hide this note from note lists; it can be shown and unarchived later";
  const refs = el("div", "links");
  refs.setAttribute("role", "list");
  note.references.forEach((r) => {
    const info = referenceInfo(r);
    const b = button("", () => openReference(r), "link-row");
    b.setAttribute("role", "listitem");
    b.disabled = !info;
    const where = info && info.project_id && r.kind !== "project" && info.project_id !== state.project ? " · " + projectName(info.project_id) : "";
    b.append(node("span", NOTE_KINDS[r.kind], "ref-kind"),
      el("span", "grow prerequisite-ref note-ref", node("span", info?.title || r.id), node("small", r.id + where, "muted")),
      icon("link", 14));
    b.title = info ? `Open this ${r.kind}` : "Not found in this viewer";
    refs.append(b);
  });
  const id = node("code", note.id, "task-id");
  id.setAttribute("aria-label", "Note ID: " + note.id);
  const meta = (label, iso, who) => el("span", "meta-item", label + " ", timeNode(iso, fullTime(iso)), who ? " by " + who : "");
  const head = el("header", "detail-head",
    node("h2", note.title, "title"),
    el("div", "task-id-header", id),
    el("div", "meta",
      note.archived ? pill("dropped", "Archived") : pill("open", "Active"),
      meta("Created", note.created_at, note.created_by),
      meta("Updated", note.updated_at, note.updated_by)));
  $("detail").replaceChildren(
    topBar([backLink], [edit, archive], { listBack: false }),
    el("div", "content", head,
      note.archived ? node("p", "Archived: note lists hide it until archived notes are shown. Unarchive lists it again.", "muted") : null,
      section("Text", markdown(note.text)),
      section(`References (${note.references.length})`, refs)));
}
// Archive and unarchive take effect at once, at the revision shown. A note changed
// meanwhile saves nothing and reloads.
async function archiveNote(note, back, archived) {
  if (submissionPending) return;
  submissionPending = true;
  try {
    await api("note-update", { note_id: note.id, expected_revision: note.revision, archived });
    toast(archived ? "Note archived. Note lists hide it until you show archived notes." : "Note unarchived. It is listed again.");
  } catch (e) {
    toast(e.conflict ? "This note changed elsewhere, so nothing was saved. Showing its current version; try again if you still want to." : e.message, true);
  } finally {
    submissionPending = false;
  }
  await openNote(note.id, back, { quiet: true });
}

// Choices for the reference picker: what is in view first (the task or group, its group,
// the open workstream and project), then this view's cards, the project's workstreams and
// every project. referenceCandidates adds every workstream and the project's groups and
// tasks; otherProjectTasks adds the other projects' tasks once the user starts typing.
function candidate(kind, item) {
  const title = kind === "workstream" ? item.branch || item.name : kind === "project" ? item.name : item.title;
  return { kind, id: item.id, title, project_id: kind === "project" ? item.id : item.project_id };
}
function contextCandidates() {
  const out = [];
  const t = state.task;
  if (t) {
    out.push(candidate(noteKind(t), t));
    if (t.parent_group) out.push(candidate("group", { project_id: t.project_id, ...t.parent_group }));
  }
  const stream = state.streams.find((s) => s.id === state.stream);
  if (stream) out.push(candidate("workstream", stream));
  const project = state.projects.find((p) => p.id === state.project);
  if (project) out.push(candidate("project", project));
  state.rows.forEach((r) => out.push(candidate(state.groups ? "group" : noteKind(r), r)));
  state.streams.filter((s) => !isArchived(s)).forEach((s) => out.push(candidate("workstream", s)));
  state.projects.forEach((p) => out.push(candidate("project", p)));
  return out;
}
function uniqueCandidates(list) {
  const seen = new Set();
  return list.filter((c) => c.id && !seen.has(c.id) && seen.add(c.id));
}
async function referenceCandidates() {
  const extra = [];
  try {
    const all = await workstreams();
    rememberWorkstreams(all);
    all.filter((w) => !isArchived(w)).forEach((w) => extra.push(candidate("workstream", w)));
  } catch {}
  if (state.project) {
    try { (await pages("groups", { project: state.project })).forEach((g) => extra.push(candidate("group", g))); } catch {}
    // The project's tasks outside this view, so a task in another workstream can be found by
    // title. All tasks already shows every one of them.
    const allTasks = !state.stream && !state.unassigned && !state.groups;
    if (!allTasks) extra.push(...await projectTasks(state.project));
  }
  return uniqueCandidates([...contextCandidates(), ...extra]);
}
// One project's tasks as picker choices, through the board's read-only tasks action (the
// note API is unchanged). A failed read leaves them out; a complete ID can still be looked up.
async function projectTasks(project, maxPages) {
  try {
    return (await pages("tasks", { project }, maxPages)).map((t) => candidate(noteKind(t), t));
  } catch {
    return [];
  }
}
// Other projects' tasks, at most OTHER_PROJECT_PAGES pages (100 each) per project.
const OTHER_PROJECT_PAGES = 5;
async function otherProjectTasks() {
  const out = [];
  for (const p of state.projects) if (p.id !== state.project) out.push(...await projectTasks(p.id, OTHER_PROJECT_PAGES));
  return out;
}
// The explicit references of a note being written: chips that can be removed, and a search
// over the choices above. A complete ID that is not listed is looked up as a task or group.
function referencePicker(get, set) {
  const wrap = el("div", "field note-refs");
  const label = node("span", "References", "field-label");
  label.id = "refs-label";
  const chips = el("ul", "ref-chips");
  chips.setAttribute("aria-labelledby", "refs-label");
  const searchLabel = node("label", "Add a reference");
  searchLabel.htmlFor = "field-ref-search";
  const search = node("input");
  search.id = "field-ref-search";
  search.type = "search";
  search.autocomplete = "off";
  search.spellcheck = false;
  search.placeholder = "Search titles and names, or paste a complete ID";
  const hint = node("p", "Type to search by title: this project's tasks and groups, other projects' tasks, and every workstream and project. A task or group that isn't found can be added by its complete ID. The text is never scanned for references.", "field-hint");
  hint.id = "field-ref-search-hint";
  search.setAttribute("aria-describedby", hint.id);
  const options = el("div", "ref-options");
  options.setAttribute("role", "group");
  options.setAttribute("aria-label", "Matching references");
  const status = node("p", "", "field-hint ref-status");
  status.setAttribute("role", "status");
  // This project's choices come first; other projects' tasks follow once they are read.
  let here = contextCandidates(), there = [];
  let candidates = uniqueCandidates(here);
  let first = null;
  const nameOf = (r) => referenceInfo(r)?.title || candidates.find((c) => c.id === r.id)?.title || r.id;
  function choose(c) {
    if (!get().some((r) => r.id === c.id)) set([...get(), { kind: c.kind, id: c.id }]);
    referenceNames.set(c.id, { title: c.title, project_id: c.project_id });
    search.value = "";
    status.textContent = `Added ${NOTE_KINDS[c.kind].toLowerCase()} “${c.title}”.`;
    render();
    search.focus();
  }
  async function lookUp(id) {
    const known = candidates.find((c) => c.id === id);
    if (known) return choose(known);
    status.textContent = `Looking up ${id}…`;
    try {
      const t = (await api("details", { ids: [id] })).items[0];
      choose(candidate(noteKind(t), t));
    } catch {
      status.textContent = `No task, group, workstream or project has the ID “${id}”. Use its complete ID.`;
    }
  }
  function render() {
    const refs = get();
    chips.replaceChildren(...refs.map((r) => {
      const name = nameOf(r);
      const remove = iconButton("close", `Remove reference: ${NOTE_KINDS[r.kind]} ${name}`, () => {
        set(get().filter((x) => x.id !== r.id));
        status.textContent = `Removed ${NOTE_KINDS[r.kind].toLowerCase()} “${name}”.`;
        render();
        search.focus();
      }, "icon-btn chip-remove");
      const chip = el("li", "ref-chip", node("span", NOTE_KINDS[r.kind], "ref-kind"), node("span", name, "ref-title"), remove);
      chip.title = `${NOTE_KINDS[r.kind]} ${r.id}`;
      return chip;
    }));
    if (!refs.length) chips.append(el("li", "ref-empty warn-text", "None yet. Add at least one: a note is found only through what it references."));
    const query = search.value.trim().toLowerCase();
    const words = query.split(/\s+/).filter(Boolean);
    const chosen = new Set(refs.map((r) => r.id));
    // Nothing is suggested until the user types, which keeps the dialog short.
    const found = !words.length ? [] : candidates
      .filter((c) => !chosen.has(c.id))
      .filter((c) => words.every((w) => `${NOTE_KINDS[c.kind]} ${c.title} ${c.id} ${projectName(c.project_id)}`.toLowerCase().includes(w)))
      .slice(0, 8);
    const buttons = found.map((c) => {
      const b = button("", () => choose(c), "ref-option");
      const where = c.kind !== "project" && c.project_id && c.project_id !== state.project ? " · " + projectName(c.project_id) : "";
      b.append(node("span", NOTE_KINDS[c.kind], "ref-kind"), el("span", "grow prerequisite-ref note-ref", node("span", c.title), node("small", c.id + where, "muted")));
      b.setAttribute("aria-label", `Add ${NOTE_KINDS[c.kind].toLowerCase()} ${c.title}`);
      return b;
    });
    const raw = search.value.trim();
    first = found[0] ? () => choose(found[0]) : null;
    // A typed complete ID that is not listed can be looked up; a plain word that already
    // matches something is taken as a search.
    const idLike = /^[A-Za-z][A-Za-z0-9_-]*$/.test(raw) && (/[-_0-9]/.test(raw) || !found.length);
    if (idLike && !candidates.some((c) => c.id === raw) && !chosen.has(raw)) {
      buttons.push(button(`Look up “${raw}” by ID`, () => lookUp(raw), "ref-option lookup"));
      first ||= () => lookUp(raw);
    }
    options.replaceChildren(...buttons);
    options.hidden = !buttons.length;
  }
  // Other projects' tasks are read only once the user starts typing, once per picker.
  let elsewhere = null;
  const merge = () => {
    candidates = uniqueCandidates([...here, ...there]);
    render();
  };
  search.oninput = () => {
    status.textContent = "";
    if (!elsewhere && search.value.trim()) elsewhere = otherProjectTasks().then((list) => { there = list; merge(); });
    render();
  };
  // Enter adds the first match instead of submitting the form.
  search.onkeydown = (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    first?.();
  };
  wrap.append(label, chips, searchLabel, search, options, status, hint);
  $("fields").append(wrap);
  render();
  referenceCandidates().then((list) => { here = list; merge(); });
  return { render };
}

const NO_REFERENCES = "Add at least one reference. A note is found only through the tasks, groups, workstreams or projects it references.";
// Add or edit a note: its title, text and references are saved together in one call. An
// edit carries the revision it read; if the note changed meanwhile, nothing is saved: the
// note reloads and its current version is shown beside the draft, for the user to edit
// that version or deliberately keep the draft.
function noteDialog(note, { references = [], saved } = {}) {
  if (submissionPending) return;
  openDialog(note ? "Edit note" : "Add a note",
    note
      ? "Change the title, text or references. They are saved together."
      : "A note is found through the tasks, groups, workstreams or projects it references. Agents read the same notes through simtask.",
    note ? "Save note" : "Add note");
  $("dialog").classList.add("wide");
  const title = field("title", "Title", { maxLength: NOTE_TITLE_LIMIT });
  const text = field("text", "Text", { maxLength: NOTE_TEXT_LIMIT, multiline: true,
    hint: "Up to 4,000 characters, shown with simple Markdown: paragraphs, lists, headings, code, bold and italic." });
  text.rows = 8;
  let chosen = (note ? note.references : references).map((r) => ({ kind: r.kind, id: r.id }));
  let revision = note ? note.revision : null;
  if (note) {
    title.value = note.title;
    text.value = note.text;
  }
  const picker = referencePicker(() => chosen, (next) => {
    chosen = next;
    // Adding a reference answers the missing-reference error at once.
    if (next.length && $("form-error").textContent === NO_REFERENCES) $("form-error").textContent = "";
  });
  const adopt = (latest, replace) => {
    revision = latest.revision;
    if (!replace) return;
    title.value = latest.title;
    text.value = latest.text;
    chosen = latest.references.map((r) => ({ kind: r.kind, id: r.id }));
    picker.render();
  };
  submitAction = async (values) => {
    const payload = {
      title: values.get("title") || "",
      text: values.get("text") || "",
      references: chosen.map((r) => ({ kind: r.kind, id: r.id })),
    };
    if (!payload.title.trim()) throw localError(`Give the note a title of up to ${NOTE_TITLE_LIMIT} characters.`);
    if (!payload.text.trim()) throw localError("Write the note's text, up to 4,000 characters.");
    if (!payload.references.length)
      throw localError(NO_REFERENCES);
    let ack;
    try {
      ack = note
        ? await api("note-update", { note_id: note.id, expected_revision: revision, ...payload })
        : await api("note-create", payload);
    } catch (e) {
      if (note && e.conflict) {
        await showNoteConflict(note.id, adopt);
        throw localError("This note changed elsewhere, so nothing was saved. Its current version is shown below; your draft is still in the form.");
      }
      const refusal = NOTE_REFUSALS.exec(e.message);
      if (refusal) throw localError("Nothing was saved. " + e.message.slice(refusal[0].length).replace(/^./, (c) => c.toUpperCase()) + ".");
      throw e;
    }
    return {
      afterSave: () => {
        toast(note ? (ack.changed ? "Note saved." : "No changes to save.") : "Note added.");
        return saved?.(ack);
      },
    };
  };
}
// After a stale save: read the current note, show it in place of the stale one, and set it
// beside the draft with two explicit choices. Nothing is overwritten without one.
async function showNoteConflict(id, adopt) {
  let latest;
  try {
    latest = (await api("note-read", { ids: [id] })).items[0];
  } catch (e) {
    $("conflict").replaceChildren(node("p", `Couldn't read the current version: ${e.message}`, "warn-text"));
    return;
  }
  await nameReferences(latest.references);
  if (state.panel?.kind === "note" && state.panel.id === id) renderNote(latest, state.panel.back);
  const refs = latest.references.map((r) => `${NOTE_KINDS[r.kind]} ${referenceInfo(r)?.title || r.id}`).join(" · ");
  const done = (message) => {
    $("form-error").textContent = message;
    $("conflict").replaceChildren();
  };
  $("conflict").replaceChildren(el("div", "conflict-box",
    node("strong", "Current version: " + latest.title),
    node("p", `Updated ${fullTime(latest.updated_at)} by ${latest.updated_by}${latest.archived ? " · archived" : ""}`, "muted"),
    node("p", "References: " + (refs || "none")),
    markdown(latest.text),
    el("div", "conflict-actions",
      button("Edit the current version", () => {
        adopt(latest, true);
        done("The form now holds the current version. Make your change again, then save.");
      }, "btn small"),
      button("Keep my draft", () => {
        adopt(latest, false);
        done("Saving now replaces the current version with your draft.");
      }, "btn small"))));
  $("conflict").scrollIntoView?.({ block: "nearest" });
}

/* ---------- menus ---------- */

function actionsMenu(anchor, t) {
  const pop = $("popover");
  if (!pop.hidden && pop.dataset.for === t.id) return closeMenu();
  const item = (label, fn, cls = "") =>
    button(label, () => {
      closeMenu();
      fn();
    }, "menu-item " + cls);
  const items = [];
  // Questions belong to live work, including tasks sent back for rework; done and
  // dropped tasks get none, and deferred tasks need Resume first.
  if (!["done", "dropped", "deferred"].includes(t.status)) items.push(item("Ask a question", () => askQuestion(t)));
  items.push(item(membershipLabel(t), () => addToWorkstreamTask(t).catch((e) => toast(e.message, true))));
  if (t.workstream_ids?.length) items.push(item("Remove from workstream", () => removeFromWorkstreamTask(t).catch(e => toast(e.message, true))));
  if (t.status === "deferred" || t.status === "dropped") items.push(item("Resume", () => resumeTask(t)));
  else items.push(item("Defer", () => changeStatus(t, "deferred")), item("Drop", () => dropTask(t), "danger"));
  items.forEach((b) => b.setAttribute("role", "menuitem"));
  pop.replaceChildren(...items);
  pop.dataset.for = t.id;
  pop.hidden = false;
  const r = anchor.getBoundingClientRect();
  pop.style.top = r.bottom + 6 + "px";
  pop.style.left = Math.max(8, r.right - pop.offsetWidth) + "px";
  items[0].focus();
}
function closeMenu() {
  $("popover").hidden = true;
  $("popover").dataset.for = "";
}
document.addEventListener("click", (e) => {
  if (!$("popover").hidden && !$("popover").contains(e.target) && !e.target.closest(".top-actions")) closeMenu();
});
function closeDrawer() {
  $("shell").classList.remove("drawer-open", "detail-open");
}

/* ---------- dialogs ---------- */

// A labelled short input, or a multiline details area, with an optional hint below.
function field(name, label, { required = true, maxLength = 500, multiline = false, hint } = {}) {
  const wrap = el("div", "field");
  const l = node("label", label);
  l.htmlFor = "field-" + name;
  const i = node(multiline ? "textarea" : "input");
  if (multiline) i.rows = 4;
  i.id = "field-" + name;
  i.name = name;
  i.required = required;
  i.maxLength = maxLength;
  i.autocomplete = "off";
  wrap.append(l, i);
  if (hint) {
    const h = node("p", hint, "field-hint");
    h.id = "field-" + name + "-hint";
    i.setAttribute("aria-describedby", h.id);
    wrap.append(h);
  }
  $("fields").append(wrap);
  return i;
}
function openDialog(title, description, saveLabel, danger = false) {
  if (submissionPending) return;
  closeMenu();
  $("submit").disabled = $("cancel").disabled = $("close").disabled = false;
  $("dialog-title").textContent = title;
  $("dialog-description").textContent = description;
  $("dialog-description").hidden = !description;
  $("fields").replaceChildren();
  $("form-error").textContent = "";
  $("conflict").replaceChildren();
  $("submit").textContent = saveLabel;
  $("submit").className = "btn " + (danger ? "danger" : "primary");
  $("dialog").classList.remove("wide");
  $("dialog").showModal();
  setTimeout(() => $("fields").querySelector("input,select")?.focus(), 0);
}
function confirmation(text) {
  const l = node("label", undefined, "check");
  const i = node("input");
  i.type = "checkbox";
  i.required = true;
  l.append(i, node("span", text));
  $("fields").append(l);
}
// Submit a change to one task with the revision this dialog read. If the task changed
// meanwhile, the Store refuses it; the dialog keeps the user's input and shows the
// current task before they choose to submit against it.
function taskAction(t, action, extra) {
  let revision = t.revision;
  submitAction = async values => {
    try {
      return await api(action, { task_id: t.id, expected_revision: revision, ...extra(values) });
    } catch (error) {
      if (error.conflict) $("conflict").replaceChildren(button("Show the current task", async () => {
        const latest = (await details(t.id)).items[0];
        $("conflict").replaceChildren(el("div", "conflict-box", node("strong", latest.title),
          node("p", "Status: " + (STANDINGS[taskStanding(latest)]?.label || latest.status)),
          markdown(latest.body), markdown(latest.acceptance_criteria),
          node("p", latest.workstream_ids?.length ? "Currently in " + latest.workstream_ids.map(streamName).join(", ") : "Currently unassigned"),
          button("I've checked it — use this version", () => {
            revision = latest.revision;
            $("form-error").textContent = "Check your choice, then submit again.";
            $("conflict").replaceChildren();
          }, "btn small")));
      }, "btn small"));
      throw error;
    }
  };
}
// Names the open workstream only when the task is not in it yet.
function membershipLabel(t) {
  const target = state.streams.find((w) => w.id === state.stream && w.project_id === t.project_id);
  return target && !t.workstream_ids?.includes(target.id) ? `Add to ${target.branch || target.name}` : "Add to workstream";
}
async function membershipDialog(t, adding) {
  if (submissionPending) return;
  // Archived workstreams take no new tasks here; their memberships can still be removed.
  const streams = (await workstreams(t.project_id))
    .filter(w => adding ? !isArchived(w) || t.workstream_ids?.includes(w.id) : t.workstream_ids?.includes(w.id));
  if (submissionPending) return;
  const title = adding ? "Add to workstream" : "Remove from workstream";
  openDialog(title, adding
    ? `Add “${t.title}” to the chosen workstream. Existing memberships, results and reviews are kept.`
    : `Remove “${t.title}” only from the chosen workstream. Other memberships, results and reviews are kept. A task with no memberships is listed under Unassigned.`, title);
  const picker = node("select"); picker.id = "field-workstream_id"; picker.name = "workstream_id"; picker.required = true;
  for (const w of streams) {
    const option = node("option", (w.branch || w.name) + (adding && t.workstream_ids?.includes(w.id) ? " (already included)" : "") + (isArchived(w) ? " (archived)" : ""));
    option.value = w.id; picker.append(option);
  }
  picker.value = streams.some(w => w.id === state.stream) ? state.stream : streams[0]?.id || "";
  const label = node("label", "Workstream"); label.htmlFor = picker.id;
  $("fields").append(el("div", "field", label, picker));
  $("submit").disabled = !streams.length;
  if (!streams.length) $("fields").append(node("p", adding ? "Initialize a branch workstream before adding this task." : "This task has no workstream memberships.", "warn-text"));
  taskAction(t, adding ? "add-to-workstream" : "remove-from-workstream", values => ({ workstream_id: values.get("workstream_id") }));
}
async function addToWorkstreamTask(t) { return membershipDialog(t, true); }
async function removeFromWorkstreamTask(t) { return membershipDialog(t, false); }
function askQuestion(t) {
  openDialog("Ask a question", "Your question holds this task until it's settled. An agent talks it through with you and records the answer.", "Add question");
  field("text", "Question");
  taskAction(t, "question", values => ({ text: values.get("text") }));
}
// The Store keeps a reason with every status change; these stand in when the user
// gives none.
const STATUS_NOTES = {
  deferred: "Deferred in the browser.",
  open: "Resumed in the browser.",
  dropped: "Dropped in the browser.",
};
// Defer and resume (from Later) take effect at once: nothing is lost and either can be
// undone from the same menu.
async function changeStatus(t, value) {
  if (submissionPending) return;
  submissionPending = true;
  try {
    await api("disposition", { task_id: t.id, expected_revision: t.revision, disposition: value, note: STATUS_NOTES[value] });
    toast(value === "deferred" ? "Deferred. It waits under Later until you resume it." : "Resumed.");
  } catch (e) {
    toast(e.conflict ? "This task changed elsewhere, so nothing was saved. Showing its latest version; try again if you still want to." : e.message, true);
  } finally {
    submissionPending = false;
  }
  await reload({ quiet: true });
}
function dropTask(t) {
  openDialog("Drop this task?", `Are you sure you want to drop “${t.title}”? It moves to Done as dropped. Its workstreams, details and results are kept, and Resume can bring it back.`, "Drop task", true);
  field("note", "Reason (optional)", { required: false, maxLength: 200 });
  taskAction(t, "disposition", values => ({ disposition: "dropped", note: values.get("note")?.trim() || STATUS_NOTES.dropped }));
}
// Bringing back a dropped task needs a reason, which the Store keeps as the instruction
// that revived it.
function resumeTask(t) {
  if (t.status !== "dropped") return changeStatus(t, "open");
  openDialog("Bring back this dropped task?", `“${t.title}” returns with its workstreams, details and results. Say why it's coming back; this is kept in its history.`, "Resume");
  field("note", "Why bring it back?", { maxLength: 200 });
  taskAction(t, "disposition", values => {
    const reason = (values.get("note") || "").trim();
    if (!reason) throw Object.assign(new Error("Say why it's coming back."), { local: true });
    return { disposition: "open", note: reason, authorization: reason };
  });
}
// A quick idea is saved to the open project's Unassigned tasks (the Store's inbox), never
// a workstream, held by an "Idea to process" item so nothing is built from it until an
// agent goes through it with the user. Saving shows it in Unassigned, under Design.
// Its permanent ID is prefilled from the title by the server's one short-slug rule
// ("idea-id"), never a rule of the viewer's own. Until the user edits the ID, saving
// sends none, so the server derives it from the final title by that same rule.
const ID_REFUSALS = /^(invalid_public_id|public_id_conflict): /;
function captureIdea() {
  const project = state.project;
  if (!project || state.groups) return;
  const name = projectName(project);
  openDialog("Save an idea",
    `Jot it down before it's lost. It is saved to Unassigned in ${name}${state.stream ? ", not this workstream" : ""}, and waits there under Design until it is assigned or processed. Nothing is built from it until you go through it with an agent.`,
    "Save idea");
  const title = field("text", "Idea title (required)", { maxLength: 200 });
  const id = field("public_id", "ID", { required: false, maxLength: 100,
    hint: "A few short words in lowercase, joined by hyphens. It is filled in from the title; you can change it. It never changes after saving." });
  id.spellcheck = false;
  id.setAttribute("autocapitalize", "none");
  field("note", "Details (optional)", { required: false, maxLength: 500, multiline: true });
  let edited = false, asked = 0, timer = null;
  id.oninput = () => { edited = id.value !== ""; };
  title.oninput = () => {
    if (edited) return;
    clearTimeout(timer);
    const turn = ++asked;
    timer = setTimeout(async () => {
      let suggested = "";
      if (title.value.trim()) {
        try {
          suggested = (await api("idea-id", { title: title.value })).public_id;
        } catch {
          return; // No prefill; saving still derives the ID from the title.
        }
      }
      if (turn === asked && !edited) id.value = suggested;
    }, 200);
  };
  submitAction = async (values) => {
    const text = values.get("text") || "", note = values.get("note") || "";
    const custom = edited ? (values.get("public_id") || "").trim() : "";
    if (!text.trim()) throw Object.assign(new Error("Write a short idea title (up to 200 characters)."), { local: true });
    let saved;
    try {
      saved = await api("idea", { project, text, note, ...(custom ? { public_id: custom } : {}) });
    } catch (e) {
      const refusal = ID_REFUSALS.exec(e.message);
      if (!refusal) throw e;
      // Nothing was saved: ask for another ID in plain words.
      id.focus();
      const plain = refusal[1] === "public_id_conflict" && custom
        ? `The ID “${custom}” is already taken; IDs stay reserved even after a task closes. Choose another ID.`
        : e.message.slice(refusal[0].length).replace(/^./, (c) => c.toUpperCase()) + ".";
      throw Object.assign(new Error(plain), { local: true });
    }
    return { afterSave: () => showIdea(saved) };
  };
}
async function showIdea(saved) {
  toast("Idea saved to Unassigned. It waits under Design until it is assigned or processed.");
  if (state.project !== saved.project_id) return reload({ quiet: true });
  state.groups = false;
  state.linkedGroup = null;
  state.stream = null;
  state.unassigned = true;
  state.selected = saved.id;
  state.task = null;
  state.panel = null;
  $("search").value = "";
  syncRoute("push");
  renderNav();
  $("shell").classList.add("detail-open");
  await reload({ quiet: true });
}
$("form").onsubmit = async (e) => {
  e.preventDefault();
  if (submissionPending) return;
  submissionPending = true;
  $("submit").disabled = $("cancel").disabled = $("close").disabled = true;
  $("form-error").textContent = "";
  try {
    const result = await submitAction(new FormData($("form")));
    $("dialog").close();
    if (result?.afterSave) await result.afterSave();
    else if (!result?.stopped) {
      toast("Saved");
      await reload({ quiet: true });
    }
  } catch (e) {
    $("form-error").textContent =
      e.message +
      (e.local ? "" : e.conflict
        ? " This task changed elsewhere. What you entered is kept; check the current task before trying again."
        : " If you're unsure whether it saved, check the task before retrying.");
    // A long form (a note's) may have scrolled the message out of view.
    $("form-error").scrollIntoView?.({ block: "nearest" });
  } finally {
    submissionPending = false;
    $("submit").disabled = $("cancel").disabled = $("close").disabled = false;
  }
};
$("cancel").onclick = $("close").onclick = () => {
  if (!submissionPending) $("dialog").close();
};
$("dialog").oncancel = (e) => {
  // Escape must not abandon a write whose eventual outcome still owns this dialog.
  if (submissionPending) e.preventDefault();
};

/* ---------- global controls ---------- */

$("search").oninput = renderList;
$("idea").onclick = captureIdea;
$("notes-open").onclick = openScopeNotes;
$("refresh").onclick = () => reload({ quiet: true }).then(() => toast("Up to date"));
$("menu").onclick = () => $("shell").classList.add("drawer-open");
$("scrim").onclick = closeDrawer;
$("stop").onclick = () => {
  openDialog(
    "Stop the viewer?",
    "Closes this local page. simtask keeps working; relaunch with simtask ui.",
    "Stop viewer",
    true,
  );
  confirmation("Stop the local viewer.");
  submitAction = async () => {
    const result = await api("stop");
    $("dialog").close();
    $("shell").classList.add("stopped");
    $("detail").replaceChildren(emptyState("Viewer stopped", "You can close this tab."));
    $("list").replaceChildren();
    submitAction = null;
    return result;
  };
};
function moveSelection(step) {
  const rows = orderedRows();
  if (!rows.length) return;
  const i = rows.findIndex((r) => r.id === state.selected);
  const next = rows[Math.min(rows.length - 1, Math.max(0, i + step))];
  if (next && next.id !== state.selected) selectTask(next.id);
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("popover").hidden) return closeMenu();
  if ($("dialog").open || e.metaKey || e.ctrlKey || e.altKey) return;
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
  if (typing) {
    if (e.key === "Escape" && e.target.id === "search") {
      e.target.value = "";
      renderList();
      e.target.blur();
    } else if (e.key === "ArrowDown" && e.target.id === "search") {
      e.preventDefault();
      moveSelection(1);
    }
    return;
  }
  if (e.key === "/") {
    e.preventDefault();
    $("search").focus();
  } else if (e.key === "j" || e.key === "ArrowDown") {
    e.preventDefault();
    moveSelection(1);
  } else if (e.key === "k" || e.key === "ArrowUp") {
    e.preventDefault();
    moveSelection(-1);
  } else if (e.key === "r") {
    reload({ quiet: true });
  }
});
// Agents change tasks while this tab sits in the background; catch up on return.
window.addEventListener("focus", () => {
  if (state.project && !$("dialog").open && !$("shell").classList.contains("stopped") && Date.now() - state.loadedAt > 15000)
    reload({ quiet: true });
});
boot();
