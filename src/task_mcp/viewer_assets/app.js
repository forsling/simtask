"use strict";
// Every piece of task text is inserted with textContent or DOM nodes, never parsed as HTML.
const $ = (id) => document.getElementById(id);
const token =
  location.hash.slice(1) || sessionStorage.getItem("task-token") || "";
if (token) sessionStorage.setItem("task-token", token);
history.replaceState(null, "", "/");

const VIEWS = {
  signoff: { label: "Ready to sign off", short: "Sign off", tone: "go" },
  pending_acceptance: { label: "Needs acceptance", short: "Accept", tone: "ask" },
  unresolved_items: { label: "Open question", short: "Question", tone: "warn" },
  review: { label: "In review", short: "Review", tone: "info" },
  ready: { label: "Ready", short: "Ready", tone: "ready" },
  prerequisites: { label: "Blocked", short: "Blocked", tone: "muted" },
  deferred: { label: "Deferred", short: "Deferred", tone: "muted" },
  done: { label: "Done", short: "Done", tone: "done" },
  dropped: { label: "Dropped", short: "Dropped", tone: "muted" },
  rework: { label: "Rework", short: "Rework", tone: "warn" },
  passed: { label: "Review passed", short: "Passed", tone: "go" },
  human_review: { label: "Human reviewed", short: "Reviewed", tone: "go" },
  rejected: { label: "Rejected", short: "Rejected", tone: "warn" },
  accepted: { label: "Signed off", short: "Signed off", tone: "done" },
  open: { label: "Open", short: "Open", tone: "ready" },
};
const SECTIONS = [
  { key: "you", title: "Needs you", views: ["signoff", "pending_acceptance", "unresolved_items"] },
  { key: "active", title: "In progress", views: ["review", "ready", "prerequisites"] },
  { key: "later", title: "Later", views: ["deferred"], collapsible: true },
  { key: "closed", title: "Closed", views: ["done", "dropped"], collapsible: true },
];
const collapsed = new Set(["closed"]);

const state = {
  projects: [],
  streams: [],
  rows: [],
  project: null,
  stream: null,
  groups: false,
  task: null,
  selected: null,
  generation: 0,
  loadedAt: 0,
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
  plus: "M12 5v14M5 12h14",
  search: "M11 19a8 8 0 1 1 0-16 8 8 0 0 1 0 16zM21 21l-4.3-4.3",
  refresh: "M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  edit: "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z",
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
  if (name === "more") svg.setAttribute("class", "i bold");
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
// Long branch names read better with the prefix dimmed: codex/ + session-resilience.
function branchLabel(name) {
  const cut = name.lastIndexOf("/");
  const wrap = node("span", undefined, "branch");
  if (cut > 0) wrap.append(node("span", name.slice(0, cut + 1), "prefix"));
  wrap.append(node("span", name.slice(cut + 1)));
  wrap.title = name;
  return wrap;
}

const EVENTS = {
  "task.created": "Created",
  "task.updated": "Edited",
  "task.accepted": "Spec accepted",
  "task.disposition_changed": "Status changed",
  "task.signoff": "Sign-off decision",
  "task.decomposed": "Split into a group",
  "tasks.reordered": "Reordered",
  "attempt.recorded": "Result recorded",
  "attempt.reviewed": "Reviewed",
  "attempt.human_reviewed": "Human review",
  "gate.unresolved_added": "Question added",
  "gate.unresolved_resolved": "Question answered",
  "gate.prerequisite_added": "Prerequisite added",
  "gate.prerequisite_proposed": "Gate proposed",
  "gate.proposal_accepted": "Proposal accepted",
  "gate.proposal_dismissed": "Proposal dismissed",
  "group.member_added": "Added to group",
  "scope.changed": "Scope changed",
};
const isRead = (action) => /(\.read|\.listed|_read)$/.test(action);

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
async function pages(action, data = {}) {
  let items = [],
    offset = 0;
  do {
    const p = await api(action, { ...data, limit: 100, offset });
    items.push(...p.items);
    offset = p.next_offset;
  } while (offset !== null);
  return items;
}
function projectName(id) {
  return state.projects.find((p) => p.id === id)?.name || "Other project";
}
function streamName(id) {
  const s = state.streams.find((s) => s.id === id);
  return s?.branch || s?.name || "another workstream";
}
function needsYou(stream) {
  const c = stream.status?.counts || {};
  return (c.signoff || 0) + (c.pending_acceptance || 0) + (c.unresolved_items || 0);
}

/* ---------- navigation ---------- */

async function boot() {
  $("menu").append(icon("menu"));
  $("refresh").append(icon("refresh"));
  $("close").append(icon("close"));
  $("search-icon").append(icon("search", 15));
  $("new").prepend(icon("plus", 15));
  try {
    state.projects = await pages("projects");
    if (!state.projects.length) {
      renderNav();
      $("new").disabled = true;
      $("list").replaceChildren(
        emptyState("No projects yet", "Initialize a project through Task MCP to get started."),
      );
      $("detail").replaceChildren();
      return;
    }
    await chooseProject(state.projects[0].id);
  } catch (e) {
    toast(e.message, true);
  }
}
async function chooseProject(id, { keepSelection = false } = {}) {
  const generation = ++state.generation;
  state.project = id;
  state.groups = false;
  if (!keepSelection) {
    state.selected = null;
    state.task = null;
  }
  const streams = await pages("workstreams", { project: id });
  if (generation !== state.generation) return;
  state.streams = streams;
  state.stream = keepSelection ? null : streams[0]?.id || null;
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
    const all = button("", () => changeScope(null), "nav-item" + (active && !state.stream ? " active" : ""));
    all.append(node("span", "All tasks", "grow"));
    sub.append(all);
    state.streams.forEach((s) => {
      const b = button("", () => changeScope(s.id), "nav-item" + (active && state.stream === s.id ? " active" : ""));
      b.append(icon("branch", 14), branchLabel(s.branch || s.name));
      const n = needsYou(s);
      if (n) b.append(node("span", String(n), "count attention"));
      else b.append(node("span", String(s.status?.scoped_count ?? ""), "count"));
      b.title = `${s.branch || s.name} · ${s.status?.scoped_count || 0} tasks` + (n ? ` · ${n} need you` : "");
      sub.append(b);
    });
    nav.append(sub);
  });
  nav.append(node("div", "Across projects", "nav-label"));
  const g = button("", () => {
    closeDrawer();
    state.groups = true;
    state.selected = null;
    renderNav();
    reload();
  }, "nav-item" + (state.groups ? " active" : ""));
  g.append(icon("layers"), node("span", "Shared groups", "grow"));
  nav.append(g);
}
async function changeScope(id) {
  closeDrawer();
  state.groups = false;
  state.stream = id;
  state.selected = null;
  state.task = null;
  renderNav();
  await reload();
}
async function reload({ quiet = false } = {}) {
  const generation = ++state.generation;
  const project = state.project;
  if (!quiet) $("list").replaceChildren(skeleton());
  $("heading").replaceChildren(
    state.groups ? "Shared groups" : state.stream ? branchLabel(streamName(state.stream)) : "All tasks",
  );
  $("new").hidden = state.groups;
  try {
    const rows = state.groups
      ? await pages("groups")
      : await pages("tasks", { project: state.project, workstream_id: state.stream });
    const streams = state.groups ? state.streams : await pages("workstreams", { project });
    if (generation !== state.generation) return;
    state.rows = rows;
    state.streams = streams;
    state.loadedAt = Date.now();
    $("subheading").textContent = state.groups
      ? `${rows.length} group${rows.length === 1 ? "" : "s"} · progress across all projects`
      : `${projectName(state.project)} · ${rows.length} task${rows.length === 1 ? "" : "s"}`;
    renderNav();
    renderList();
    if (state.selected && rows.some((r) => r.id === state.selected)) await selectTask(state.selected, { quiet });
    else if (rows.length) {
      const first = orderedRows()[0] || rows[0];
      await selectTask(first.id);
    } else {
      state.selected = null;
      $("detail").replaceChildren(
        state.groups
          ? emptyState("No shared groups", "Groups created through Task MCP show their cross-project progress here.")
          : emptyState("Nothing here yet", "Create a task, or pick another workstream.", button("New task", createTask, "btn primary")),
      );
    }
  } catch (e) {
    if (generation !== state.generation) return;
    toast(e.message, true);
    $("list").replaceChildren(emptyState("Couldn't load tasks", "Refresh to try again."));
  }
}

/* ---------- list ---------- */

function filteredRows() {
  const query = $("search").value.trim().toLowerCase();
  return state.rows.filter((r) => r.title.toLowerCase().includes(query));
}
function orderedRows() {
  if (state.groups) return filteredRows();
  const rows = filteredRows();
  return SECTIONS.flatMap((s) =>
    collapsed.has(s.key) && !$("search").value ? [] : rows.filter((r) => s.views.includes(r.view)),
  );
}
function row(r) {
  const b = button("", () => selectTask(r.id, { open: true }), "row" + (r.id === state.selected ? " selected" : ""));
  b.dataset.id = r.id;
  b.setAttribute("role", "listitem");
  b.setAttribute("aria-current", r.id === state.selected ? "true" : "false");
  if (state.groups) {
    const total = r.progress?.total || 0,
      done = r.progress?.done || 0;
    b.append(
      node("span", "", "dot tone-" + (r.complete ? "done" : "info")),
      el("span", "row-main", node("span", r.title, "row-title"), progressBar(done, total)),
      node("span", `${done}/${total}`, "row-meta"),
    );
  } else {
    const v = VIEWS[r.view] || { short: r.view, tone: "muted" };
    b.classList.toggle("closed", r.view === "done" || r.view === "dropped");
    b.append(
      node("span", "", "dot tone-" + v.tone),
      node("span", r.title, "row-title"),
      node("span", v.short, "row-meta tone-" + v.tone),
    );
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
      const items = rows.filter((r) => s.views.includes(r.view));
      if (!items.length) return;
      const isCollapsed = collapsed.has(s.key) && !searching;
      const head = button("", () => {
        if (!s.collapsible) return;
        collapsed.has(s.key) ? collapsed.delete(s.key) : collapsed.add(s.key);
        renderList();
      }, "section-head" + (s.collapsible ? " toggle" : "") + (isCollapsed ? " collapsed" : ""));
      if (s.collapsible) head.append(icon("chevron", 12));
      head.append(node("span", s.title), node("span", String(items.length), "count" + (s.key === "you" ? " attention" : "")));
      head.setAttribute("aria-expanded", String(!isCollapsed));
      list.append(head);
      if (!isCollapsed) list.append(...items.map(row));
    });
    // Views the list does not know about still deserve a place.
    const known = SECTIONS.flatMap((s) => s.views);
    const other = rows.filter((r) => !known.includes(r.view));
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
function emptyState(title, text, action) {
  return el("div", "empty", node("h3", title), node("p", text), action);
}

/* ---------- detail ---------- */

async function selectTask(id, { open = false, quiet = false } = {}) {
  state.selected = id;
  document.querySelectorAll(".row").forEach((r) => {
    const on = r.dataset.id === id;
    r.classList.toggle("selected", on);
    r.setAttribute("aria-current", String(on));
    if (on) r.scrollIntoView({ block: "nearest" });
  });
  if (open) $("shell").classList.add("detail-open");
  if (!quiet) $("detail").classList.add("loading");
  try {
    const t = (await api("details", { ids: [id] })).items[0];
    if (state.selected !== id) return;
    state.task = t;
    const scroll = $("detail").scrollTop;
    renderDetail(t);
    $("detail").scrollTop = quiet ? scroll : 0;
  } catch (e) {
    toast(e.message, true);
  } finally {
    $("detail").classList.remove("loading");
  }
}
function section(title, content, extra) {
  return el("section", "block", el("h3", "block-title", node("span", title), extra), content);
}
function topBar(crumbs, actions) {
  const back = iconButton("back", "Back to list", () => $("shell").classList.remove("detail-open"), "icon-btn only-mobile");
  return el("div", "topbar", back, el("div", "crumbs", ...crumbs), el("div", "top-actions", ...actions));
}
function renderDetail(t) {
  const d = $("detail");
  if (t.object_type === "group") return renderGroup(t);
  const row = state.rows.find((r) => r.id === t.id);
  const view = row?.view || t.status;
  const locked = t.status === "done";

  const actions = [];
  if (!locked) {
    actions.push(iconButton("edit", "Edit (e)", () => editTask(t)));
    actions.push(iconButton("more", "More actions", (e) => moreMenu(e.currentTarget, t)));
  }
  const crumbs = [node("span", projectName(t.project_id))];
  if (state.stream) crumbs.push(node("span", "/", "sep"), branchLabel(streamName(state.stream)));

  const head = el(
    "header",
    "detail-head",
    node("h2", t.title, "title"),
    el(
      "div",
      "meta",
      pill(view),
      t.accepted
        ? el("span", "meta-item", icon("check", 13), "Spec accepted")
        : node("span", "Spec not accepted", "meta-item warn"),
      node("span", "Updated " + ago(t.updated_at), "meta-item"),
      t.parent_group
        ? button(t.parent_group.title, () => openGroup(t.parent_group.id), "chip")
        : null,
    ),
  );
  d.replaceChildren(topBar(crumbs, actions), el("div", "content", head, nextStep(t, view), ...body(t)));
}
function currentAttempt(t, requiredStates = null) {
  if (!requiredStates && t.status === "done" && t.selected_attempt_id)
    return t.attempts.find((a) => a.id === t.selected_attempt_id) || null;
  return [...t.attempts].reverse().find((a) =>
    a.spec_revision === t.spec_revision &&
    (!state.stream || a.workstream_id === state.stream) &&
    (!requiredStates || requiredStates.includes(a.state))
  ) || null;
}
// The one thing the human can do next, stated plainly with its buttons.
function nextStep(t, view) {
  const a = currentAttempt(t, view === "signoff" ? ["passed", "human_review"] : ["review"]);
  let title, text, buttons = [];
  if (t.status === "done") {
    title = "Signed off";
    text = "This task is complete. A new requirement becomes a new task.";
  } else if (view === "deferred" || view === "dropped") {
    title = view === "deferred" ? "Deferred" : "Dropped";
    text = "The specification and history are kept. Resume to put it back in the queue.";
    buttons.push(button("Resume task", () => disposition(t, "open", "Resume"), "btn primary"));
  } else if (view === "signoff" && a) {
    title = "Ready for your sign-off";
    text = `${a.implementer}'s result has been reviewed. Check it against the acceptance criteria below.`;
    buttons.push(button("Approve & sign off", () => signoff(t, a), "btn primary"));
    buttons.push(button("Request changes", () => requestChanges(t, a)));
  } else if (view === "review" && a) {
    title = "Result waiting for review";
    text = `${a.implementer} recorded a result. An independent reviewer usually handles this, or you can review it yourself.`;
    buttons.push(button("Record my review", () => humanReview(a)));
  } else if (view === "pending_acceptance") {
    title = "Accept this spec to unblock work";
    text = "Agents won't implement it until you accept this exact specification.";
    buttons.push(button("Accept spec", () => accept(t), "btn primary"));
    buttons.push(button("Edit first", () => editTask(t)));
  } else if (view === "unresolved_items") {
    const n = t.unresolved_items.length;
    title = n === 1 ? "One question needs an answer" : `${n} questions need answers`;
    text = "Work is blocked until each is resolved.";
  } else if (view === "prerequisites") {
    title = "Waiting on prerequisites";
    text = "It becomes ready once the tasks below are done.";
  } else if (view === "ready") {
    title = "Ready for an agent";
    text = "Accepted and unblocked. An agent can pick this up.";
  } else return null;
  const tone = (VIEWS[view] || VIEWS.open).tone;
  return el(
    "div",
    "next tone-" + (t.status === "done" ? "done" : tone),
    el("div", "next-text", node("strong", title), node("p", text)),
    buttons.length ? el("div", "next-actions", ...buttons) : null,
  );
}
function body(t) {
  const out = [];
  const locked = t.status === "done";
  if (t.unresolved_items.length) {
    const list = el("div", "questions");
    t.unresolved_items.forEach((q) => {
      const item = el("div", "question", markdown(q.text));
      if (!locked) item.append(el("div", "q-actions", button("Answer", () => resolve(t, q), "btn small")));
      list.append(item);
    });
    out.push(section("Open questions", list));
  }
  if (t.blocked_by.length) {
    const list = el("div", "links");
    t.blocked_by.forEach((id) => {
      const b = button("Loading…", async () => {
        const p = (await api("details", { ids: [id] })).items[0];
        if (p.object_type === "group") await openGroup(id);
        else await navigateMember(p);
      }, "link-row");
      list.append(b);
      api("details", { ids: [id] })
        .then((r) => {
          const p = r.items[0];
          b.replaceChildren(node("span", p.title, "grow"), pill(p.status === "done" ? "done" : "open", p.status === "done" ? "Done" : p.object_type === "group" ? "Group" : "Not done"), icon("link", 14));
        })
        .catch(() => (b.textContent = "Prerequisite unavailable"));
    });
    out.push(section("Prerequisites", list));
  }
  out.push(section("Specification", markdown(t.body)));
  const criteria = markdown(t.acceptance_criteria, { checklist: true });
  criteria.classList.add("criteria");
  out.push(section("Acceptance criteria", criteria));
  if (t.acceptance_note) {
    const note = el("details", "fold", node("summary", t.accepted ? "Acceptance note" : "Previous acceptance note (spec changed since)"), markdown(t.acceptance_note));
    out.push(note);
  }
  const view = state.rows.find((r) => r.id === t.id)?.view;
  const requiredStates = view === "signoff" ? ["passed", "human_review"] : view === "review" ? ["review"] : null;
  const a = currentAttempt(t, t.status === "done" ? null : requiredStates);
  const earlier = t.attempts.filter((x) => x !== a).reverse();
  if (a) out.push(section("Result", attemptCard(a, t)));
  if (earlier.length) {
    const fold = el("details", "fold", node("summary", `Other results (${earlier.length})`));
    earlier.forEach((x) => fold.append(attemptCard(x, t)));
    out.push(fold);
  }
  if (t.gate_proposals.length) {
    const list = el("div", "proposals");
    t.gate_proposals.forEach((p) =>
      list.append(el("div", "proposal", pill("open", p.state || p.status || "proposed"), markdown(p.text || p.kind || "Gate proposal"))),
    );
    out.push(el("details", "fold", node("summary", `Gate proposals (${t.gate_proposals.length})`), list));
  }
  out.push(activity(t));
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
  if (a.review_note) card.append(el("div", "sub", node("h4", "Review by " + (a.reviewer || "reviewer")), markdown(a.review_note)));
  if (a.human_review_note) card.append(el("div", "sub", node("h4", "Your review"), markdown(a.human_review_note)));
  return card;
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
  d.replaceChildren(
    topBar([node("span", "Shared group")], []),
    el(
      "div",
      "content",
      el(
        "header",
        "detail-head",
        node("h2", t.title, "title"),
        el("div", "meta", pill(t.complete ? "done" : "review", t.complete ? "Complete" : "In progress"), node("span", `${p.done} of ${p.total} signed off · ${projects} project${projects === 1 ? "" : "s"}`, "meta-item")),
        progressBar(p.done, p.total),
      ),
      section("Context", markdown(t.body)),
      section("Done when", markdown(t.acceptance_criteria, { checklist: true })),
      section("Members", members),
      node("p", "Group progress counts members in every project. It doesn't mean one workstream delivered them all.", "footnote"),
      activity(t),
    ),
  );
}
async function navigateMember(m) {
  state.selected = m.id;
  await chooseProject(m.project_id, { keepSelection: true });
}
async function openGroup(id) {
  state.groups = true;
  state.selected = id;
  renderNav();
  await reload();
}
function activity(t) {
  const wrap = el("details", "fold", node("summary", "Activity"));
  const content = el("ol", "timeline");
  wrap.append(content);
  let loaded = false,
    after = 0,
    through = null;
  async function more() {
    try {
      const r = await api("events", {
        task_id: t.id,
        after_sequence: after,
        through_sequence: through,
        limit: 50,
        include_details: true,
      });
      through = r.through_sequence;
      let reads = 0;
      r.items.forEach((e) => {
        if (isRead(e.action) && e.outcome === "ok") return void reads++;
        const failed = e.outcome !== "ok";
        const li = el(
          "li",
          failed ? "failed" : "",
          el(
            "div",
            "event",
            node("span", EVENTS[e.action] || e.action.replace(/[._]/g, " "), "what"),
            node("span", e.actor, "muted"),
            node("time", ago(e.timestamp), "muted push"),
          ),
        );
        li.lastChild.lastChild.title = new Date(e.timestamp).toLocaleString();
        if (failed) li.append(node("div", "Failed", "warn-text"));
        const note = e.request?.user_note || e.request?.note || e.request?.text || e.error;
        if (note) li.append(node("p", note, "event-note"));
        content.append(li);
      });
      if (reads) content.append(node("li", `${reads} read${reads === 1 ? "" : "s"} not shown`, "reads"));
      after = r.next_after_sequence;
      if (after !== null) {
        const b = button("Load more", () => {
          b.remove();
          more();
        }, "btn small");
        content.append(b);
      }
    } catch (e) {
      content.append(node("p", e.message, "warn-text"));
    }
  }
  wrap.ontoggle = () => {
    if (wrap.open && !loaded) {
      loaded = true;
      more();
    }
  };
  return wrap;
}

/* ---------- menus ---------- */

function moreMenu(anchor, t) {
  const pop = $("popover");
  if (!pop.hidden && pop.dataset.for === t.id) return closeMenu();
  const item = (label, fn, cls = "") =>
    button(label, () => {
      closeMenu();
      fn();
    }, "menu-item " + cls);
  const items = [item("Ask a question", () => askQuestion(t))];
  if (!t.accepted) items.push(item("Accept spec", () => accept(t)));
  if (t.status === "deferred" || t.status === "dropped") items.push(item("Resume", () => disposition(t, "open", "Resume")));
  else items.push(item("Defer", () => disposition(t, "deferred", "Defer")), item("Drop", () => disposition(t, "dropped", "Drop"), "danger"));
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

function field(name, label, value = "", kind = "textarea", required = true, hint) {
  const wrap = el("div", "field");
  const l = node("label", label);
  l.htmlFor = "field-" + name;
  const i = node(kind === "input" ? "input" : "textarea");
  i.id = "field-" + name;
  i.name = name;
  i.value = value;
  i.required = required;
  if (kind === "tall") i.rows = 9;
  else if (kind !== "input") i.rows = 4;
  wrap.append(l, i);
  if (hint) wrap.append(node("small", hint, "field-hint"));
  $("fields").append(wrap);
  return i;
}
function openDialog(title, description, saveLabel, danger = false) {
  if (submissionPending) return;
  closeMenu();
  $("dialog-title").textContent = title;
  $("dialog-description").textContent = description;
  $("dialog-description").hidden = !description;
  $("fields").replaceChildren();
  $("form-error").textContent = "";
  $("conflict").replaceChildren();
  $("submit").textContent = saveLabel;
  $("submit").className = "btn " + (danger ? "danger" : "primary");
  $("dialog").showModal();
  setTimeout(() => $("fields").querySelector("input,textarea")?.focus(), 0);
}
function confirmation(text = "This is my decision. Record it in the task history.") {
  const l = node("label", undefined, "check");
  const i = node("input");
  i.type = "checkbox";
  i.required = true;
  l.append(i, node("span", text));
  $("fields").append(l);
}
function decision({ title, description, action, data, key, label, submit, danger, before }) {
  openDialog(title, description, submit || "Confirm", danger);
  before?.();
  field(key, label || (key === "text" ? "Question" : "Note"));
  confirmation();
  const taskId = state.task.id;
  submitAction = async (values) => {
    const payload = { ...data, [key]: values.get(key) };
    if (values.has("rejection")) payload.rejection = values.get("rejection");
    try {
      return await api(action, payload);
    } catch (e) {
      if (e.conflict) {
        $("conflict").replaceChildren(
          button("Show me what changed", async () => {
            const latest = (await api("details", { ids: [taskId] })).items[0];
            const n = el(
              "div",
              "conflict-box",
              node("strong", "Current version"),
              node("p", latest.title, "c-title"),
              markdown(latest.body),
              markdown(latest.acceptance_criteria),
            );
            latest.unresolved_items.forEach((q) => n.append(node("p", "Question: " + q.text)));
            const a = data.attempt_id ? latest.attempts.find((a) => a.id === data.attempt_id) : null;
            if (a) n.append(node("p", `${VIEWS[a.state]?.label || a.state}: ${a.summary}`), node("p", a.evidence, "muted"));
            n.append(
              button("I've reviewed it — use this version", () => {
                data.expected_revision = action === "human-review" ? a.revision : latest.revision;
                $("fields").querySelector("[type=checkbox]").checked = false;
                $("form-error").textContent = "Check your note, confirm again, then submit.";
                n.remove();
              }, "btn small"),
            );
            $("conflict").replaceChildren(n);
          }, "btn small"),
        );
      }
      throw e;
    }
  };
}
function accept(t) {
  decision({
    title: "Accept this spec?",
    description: "Your acceptance covers the exact specification shown. Editing it later needs a new acceptance.",
    action: "accept",
    data: { task_id: t.id, expected_revision: t.revision },
    key: "user_note",
    label: "Why it's ready (note)",
    submit: "Accept spec",
  });
}
function askQuestion(t) {
  decision({
    title: "Ask a question",
    description: "Open questions block implementation until they're answered.",
    action: "question",
    data: { task_id: t.id, expected_revision: t.revision },
    key: "text",
    label: "Question",
    submit: "Add question",
  });
}
function resolve(t, q) {
  decision({
    title: "Answer question",
    description: q.text,
    action: "resolve",
    data: { task_id: t.id, expected_revision: t.revision, item_id: q.id },
    key: "user_note",
    label: "Your answer",
    submit: "Resolve",
  });
}
function disposition(t, value, label) {
  decision({
    title: `${label} this task?`,
    description: "The spec and history are kept either way.",
    action: "disposition",
    data: { task_id: t.id, expected_revision: t.revision, disposition: value },
    key: "note",
    label: "Reason",
    submit: label,
    danger: value === "dropped",
  });
}
function humanReview(a) {
  decision({
    title: "Record your review",
    description: "Confirm you reviewed this result and its evidence yourself, or that more independent review isn't needed. This doesn't sign off the task.",
    action: "human-review",
    data: { attempt_id: a.id, expected_revision: a.revision },
    key: "user_note",
    label: "Review note",
    submit: "Record review",
  });
}
function signoff(t, a) {
  decision({
    title: "Sign off this task?",
    description: `Approves ${a.implementer}'s result and completes “${t.title}”. This can't be undone.`,
    action: "signoff",
    data: { task_id: t.id, expected_revision: t.revision, attempt_id: a.id, verdict: "approve" },
    key: "user_note",
    label: "Sign-off note",
    submit: "Approve & sign off",
  });
}
function requestChanges(t, a) {
  decision({
    title: "Request changes",
    description: "Send the result back. Choose what needs to change.",
    action: "signoff",
    data: { task_id: t.id, expected_revision: t.revision, attempt_id: a.id, verdict: "reject" },
    key: "user_note",
    label: "What needs to change",
    submit: "Request changes",
    before: () => {
      const group = el("fieldset", "choices");
      group.append(node("legend", "What should happen"));
      [
        ["rework", "Rework the implementation", "The accepted spec stays; the result goes back for rework."],
        ["revise", "Revise the spec", "Acceptance is withdrawn and your note becomes an open question."],
      ].forEach(([value, title, text], i) => {
        const l = node("label", undefined, "choice");
        const r = node("input");
        r.type = "radio";
        r.name = "rejection";
        r.value = value;
        r.required = true;
        r.checked = i === 0;
        l.append(r, el("span", "", node("strong", title), node("small", text)));
        group.append(l);
      });
      $("fields").append(group);
    },
  });
}
function editTask(t) {
  if (!t || t.object_type !== "task" || t.status === "done") return;
  let revision = t.revision;
  openDialog("Edit task", t.accepted ? "Saving changes to the spec withdraws its acceptance." : "", "Save");
  $("dialog").classList.add("wide");
  field("title", "Title", t.title, "input");
  field("body", "Specification", t.body, "tall", false, "Markdown is supported.");
  field("acceptance_criteria", "Acceptance criteria", t.acceptance_criteria, "textarea", false);
  submitAction = async (values) => {
    try {
      return await api("edit", {
        task_id: t.id,
        expected_revision: revision,
        changes: Object.fromEntries(values),
      });
    } catch (e) {
      if (e.conflict) {
        $("conflict").replaceChildren(
          button("Show the current version next to my draft", async () => {
            const latest = (await api("details", { ids: [t.id] })).items[0];
            const n = el(
              "div",
              "conflict-box",
              node("strong", "Current version"),
              node("p", latest.title, "c-title"),
              markdown(latest.body),
              markdown(latest.acceptance_criteria),
              button("I've merged my draft — save over this version", () => {
                revision = latest.revision;
                $("form-error").textContent = "Ready. Check your draft and save.";
                n.remove();
              }, "btn small"),
            );
            $("conflict").replaceChildren(n);
          }, "btn small"),
        );
      }
      throw e;
    }
  };
}
function createTask() {
  if (state.groups || !state.project || $("new").disabled) return;
  openDialog(
    "New task",
    state.stream
      ? `Adds an accepted task to ${streamName(state.stream)}.`
      : `Adds an accepted task to the ${projectName(state.project)} inbox.`,
    "Create task",
  );
  $("dialog").classList.add("wide");
  field("title", "Title", "", "input");
  field("body", "What needs to happen?", "", "tall", false, "Markdown is supported.");
  field("acceptance_criteria", "How will you know it's done?", "", "textarea", false);
  field("user_request", "Your request", "", "textarea", true, "Recorded as your authorization for this task.");
  submitAction = async (values) => {
    const r = await api("create", {
      ...Object.fromEntries(values),
      project: state.project,
      workstream_id: state.stream,
      scope: state.stream ? "workstream" : "inbox",
    });
    state.selected = r.id;
    return r;
  };
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
    if (!result?.stopped) {
      toast("Saved");
      await reload({ quiet: true });
    }
  } catch (e) {
    $("form-error").textContent =
      e.message +
      (e.conflict
        ? " Someone else changed this task. Your draft is kept; review the current version before retrying."
        : " If you're unsure whether it saved, check the task before retrying.");
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
$("dialog").onclose = () => $("dialog").classList.remove("wide");

/* ---------- global controls ---------- */

$("search").oninput = renderList;
$("new").onclick = createTask;
$("refresh").onclick = () => reload({ quiet: true }).then(() => toast("Up to date"));
$("menu").onclick = () => $("shell").classList.add("drawer-open");
$("scrim").onclick = closeDrawer;
$("stop").onclick = () => {
  openDialog(
    "Stop the viewer?",
    "Closes this local page. Task MCP keeps working; relaunch with task-mcp ui.",
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
    $("new").disabled = true;
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
  } else if (e.key === "n") {
    e.preventDefault();
    createTask();
  } else if (e.key === "e" && state.task) {
    e.preventDefault();
    editTask(state.task);
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
