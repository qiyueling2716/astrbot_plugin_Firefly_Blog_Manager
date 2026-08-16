const bridge = window.AstrBotPluginPage;
let toastTimer = null;
let currentFile = null;
let dirty = false;
const treeLoaded = new Map();

const $ = (id) => document.getElementById(id);

function toast(message, kind = "") {
  const el = $("toast");
  el.textContent = message;
  el.className = "toast show " + kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
}

/* 简易模态对话框（sandbox iframe 中 window.prompt/confirm 可能被禁用）
 * mode="confirm" 时无输入框，确定返回 true；mode="prompt" 返回输入值 */
function dialog(title, hint, mode = "prompt", initial = "") {
  return new Promise((resolve) => {
    const modal = $("modal");
    const input = $("modal-input");
    $("modal-title").textContent = title;
    $("modal-hint").textContent = hint || "";
    input.hidden = mode === "confirm";
    input.value = initial;
    const close = (value) => {
      modal.hidden = true;
      input.onkeydown = null;
      resolve(value);
    };
    input.onkeydown = (ev) => {
      if (ev.key === "Enter") close(mode === "confirm" ? true : input.value);
      if (ev.key === "Escape") close(null);
    };
    $("modal-ok").onclick = () => close(mode === "confirm" ? true : input.value);
    $("modal-cancel").onclick = () => close(mode === "confirm" ? false : null);
    modal.hidden = false;
    if (mode !== "confirm") {
      input.focus();
      input.select();
    }
  });
}

window.addEventListener("beforeunload", (e) => {
  if (dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});

function describeResponse(res) {  if (res && typeof res === "object" && !Array.isArray(res)) {
    const s = JSON.stringify(res);
    return s && s.length > 260 ? s.slice(0, 260) + "…" : (s || String(res));
  }
  return String(res);
}

/* bridge 语义：成功时 resolve 后端 data 字段（无 status 包装），
 * 失败时 reject Error(message)。统一包装成 {status, data/message}。 */
async function apiGet(endpoint, params) {
  try {
    return { status: "ok", data: await bridge.apiGet(endpoint, params || {}) };
  } catch (e) {
    return { status: "error", message: (e && e.message) || String(e) };
  }
}

async function apiPost(endpoint, body) {
  try {
    return { status: "ok", data: await bridge.apiPost(endpoint, body || {}) };
  } catch (e) {
    return { status: "error", message: (e && e.message) || String(e) };
  }
}

function joinPath(dir, name) {
  return dir ? dir + "/" + name : name;
}

/* ================= 标签页 ================= */

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $("tab-" + btn.dataset.tab).classList.add("active");
  });
});

/* ================= 状态 ================= */

function card(key, value, badge = null, mono = false) {
  const div = document.createElement("div");
  div.className = "card";
  div.innerHTML =
    '<div class="k">' + key + '</div><div class="v' + (mono ? " mono" : "") + '">' +
    value + "</div>" + (badge ? '<div class="badge ' + badge.cls + '">' + badge.text + "</div>" : "");
  return div;
}

function statusBadge(ok, text) {
  return { cls: ok ? "ok" : "err", text };
}

async function loadStatus() {
  const res = await apiGet("status");
  if (res.status !== "ok") {
    $("status-cards").innerHTML = '<div class="hint">加载状态失败: ' + (res.message || "未知错误") + "</div>";
    return;
  }
  const d = res.data;
  $("version").textContent = "v" + d.version;

  const cards = $("status-cards");
  cards.innerHTML = "";
  cards.appendChild(card("部署模式", d.deploy_mode));
  cards.appendChild(card("Firefly 博客检测", d.firefly_detected ? "已检测到" : "未检测到", statusBadge(d.firefly_detected, d.firefly_detected ? "Firefly 项目" : "缺少特征文件")));
  cards.appendChild(card("构建状态", d.is_built ? "已构建" : "未构建", statusBadge(d.is_built, d.is_built ? "dist 存在" : "dist 缺失")));
  cards.appendChild(card("文章数量", String(d.posts_count)));
  cards.appendChild(card("博客根目录", d.blog_root, statusBadge(d.blog_root_exists, { configured: "已配置", auto: "自动检测", remote: "远端配置", "not-found": "未找到" }[d.blog_root_source] || "未知"), true));

  $("memory").textContent = d.memory;

  const hint = $("blog-root-hint");
  if (d.remote_mode) {
    hint.textContent = "remote_build 模式：博客目录位于远端服务器，文件与友链管理操作远程仓库。";
  } else if (d.firefly_detected) {
    hint.textContent = "已检测到 Firefly 博客（src 根目录: " + d.src_root + "）。可在下方修改博客目录。";
  } else {
    hint.textContent = "未检测到 Firefly 博客项目。请填写博客根目录（包含 package.json 与 src 的目录），保存后重新检测。";
  }
  $("custom-root").value = d.config.local_blog_root || "";
}

$("btn-set-root").addEventListener("click", async () => {
  const path = $("custom-root").value.trim();
  if (!path) {
    toast("请输入博客目录路径", "error");
    return;
  }
  const res = await apiPost("config", { local_blog_root: path });
  if (res.status !== "ok") {
    toast(res.message || "保存失败", "error");
    return;
  }
  toast("已保存博客目录并重载", "ok");
  await loadStatus();
  await loadWallpaper();
  treeLoaded.clear();
});

$("btn-redetect").addEventListener("click", async () => {
  const res = await apiPost("config", { redetect: true });
  if (res.status !== "ok") {
    toast(res.message || "重新检测失败", "error");
    return;
  }
  toast("已重新自动检测", "ok");
  await loadStatus();
  await loadWallpaper();
  treeLoaded.clear();
});

/* ================= 文件管理 ================= */

function fileType(entry) {
  const n = entry.name || "";
  const base = n.toLowerCase();
  if (base.endsWith(".md") || base.endsWith(".txt") || base === "readme")
    return { cls: "doc", tag: "文档" };
  if (base.endsWith(".ts") || base.endsWith(".tsx")) return { cls: "ts", tag: "TS" };
  if (base.endsWith(".json")) return { cls: "json", tag: "JSON" };
  if (base.endsWith(".png") || base.endsWith(".jpg") || base.endsWith(".jpeg") || base.endsWith(".gif") || base.endsWith(".svg") || base.endsWith(".webp") || base.endsWith(".avif") || base.endsWith(".ico") || base.endsWith(".bmp"))
    return { cls: "img", tag: "图" };
  if (base.endsWith(".css")) return { cls: "css", tag: "CSS" };
  if (base.endsWith(".js") || base.endsWith(".jsx")) return { cls: "js", tag: "JS" };
  if (base.endsWith(".vue")) return { cls: "vue", tag: "VUE" };
  return { cls: "", tag: "" };
}

async function renderDir(path, ulEl) {
  ulEl.innerHTML = "";
  const res = await apiGet("files", { path });
  if (res.status !== "ok") {
    ulEl.innerHTML = '<li class="hint">' + (res.message || "加载失败") + "</li>";
    return;
  }
  treeLoaded.set(path, res.data.entries);
  res.data.entries.forEach((entry) => {
    const li = document.createElement("li");
    li.className = entry.type === "dir" ? "dir" : "file";
    const row = document.createElement("div");
    row.className = "row";
    const arrow = document.createElement("span");
    arrow.className = "arrow";
    arrow.textContent = entry.type === "dir" ? "▸" : "";
    const label = document.createElement("span");
    label.className = "name" + (entry.type === "file" ? " file" : "");
    label.textContent = entry.name;
    row.append(arrow, label);
    if (entry.type === "file") {
      const ft = fileType(entry);
      if (ft.tag) {
        const tag = document.createElement("span");
        tag.className = "file-tag " + ft.cls;
        tag.textContent = ft.tag;
        row.appendChild(tag);
      }
    }
    li.appendChild(row);
    if (entry.type === "dir") {
      const childUl = document.createElement("ul");
      childUl.hidden = true;
      childUl.addEventListener("click", (ev) => ev.stopPropagation());
      li.appendChild(childUl);
      row.addEventListener("click", async () => {
        if (childUl.hidden) {
          arrow.textContent = "▾";
          li.classList.add("open");
          childUl.hidden = false;
          await renderDir(joinPath(path, entry.name), childUl);
        } else {
          arrow.textContent = "▸";
          li.classList.remove("open");
          childUl.hidden = true;
        }
      });
    } else {
      row.addEventListener("click", () => openFile(joinPath(path, entry.name), li));
    }
    ulEl.appendChild(li);
  });
}

async function openFile(relPath, liEl) {
  if (dirty) {
    const ok = await dialog(
      "未保存的修改",
      "当前文件有未保存的修改，打开其他文件将丢失这些修改。仍要继续？",
      "confirm"
    );
    if (!ok) return;
  }
  const res = await apiGet("file", { path: relPath });
  if (res.status !== "ok") {
    toast(res.message || "无法读取文件", "error");
    return;
  }
  currentFile = relPath;
  dirty = false;
  $("current-path").textContent = "src/" + relPath;
  if (res.data.binary) {
    $("editor").hidden = true;
    let preview = $("file-preview");
    if (!preview) {
      preview = document.createElement("div");
      preview.id = "file-preview";
      $("editor").parentElement.insertBefore(preview, $("editor"));
    }
    preview.hidden = false;
    if (res.data.image) {
      preview.innerHTML = '<img src="' + res.data.content + '" alt="' + relPath + '">';
    } else {
      preview.textContent = "二进制资源，请通过上传功能更新。";
    }
    $("editor-notice").textContent = "图片预览（二进制文件不可文本编辑，请通过上传更新）";
    $("btn-save").disabled = true;
    $("btn-delete").disabled = false;
    document.querySelectorAll("#tree-root .selected").forEach((el) => el.classList.remove("selected"));
    if (liEl) liEl.classList.add("selected");
    return;
  }
  if ($("file-preview")) $("file-preview").hidden = true;
  $("editor").hidden = false;
  $("editor").value = res.data.content;
  $("editor-notice").textContent = "文本文件，可直接编辑。二进制资源请通过上传更新。";
  $("btn-save").disabled = false;
  $("btn-delete").disabled = false;
  document.querySelectorAll("#tree-root .selected").forEach((el) => el.classList.remove("selected"));
  if (liEl) liEl.classList.add("selected");
}

$("editor").addEventListener("input", () => {
  dirty = true;
});

$("btn-save").addEventListener("click", async () => {
  if (!currentFile) return;
  const res = await apiPost("file", { path: currentFile, content: $("editor").value });
  if (res.status !== "ok") {
    toast(res.message || "保存失败", "error");
    return;
  }
  dirty = false;
  toast("已保存 " + currentFile, "ok");
});

$("btn-delete").addEventListener("click", async () => {
  if (!currentFile) return;
  const confirmed = await dialog("确认删除", "删除 src/" + currentFile + " ？此操作不可恢复。", "confirm");
  if (!confirmed) return;
  const res = await apiPost("file/delete", { path: currentFile });
  if (res.status !== "ok") {
    toast(res.message || "删除失败", "error");
    return;
  }
  toast("已删除 " + currentFile, "ok");
  currentFile = null;
  dirty = false;
  if ($("file-preview")) $("file-preview").hidden = true;
  $("editor").hidden = false;
  $("editor").value = "";
  $("current-path").textContent = "";
  $("btn-save").disabled = true;
  $("btn-delete").disabled = true;
  treeLoaded.clear();
  renderDir("", $("tree-root"));
});

$("btn-new-file").addEventListener("click", async () => {
  const name = await dialog("新建文件", "新文件名（相对 src/，可含子目录）：", "prompt", "");
  if (!name) return;
  const res = await apiPost("file", { path: name, content: "" });
  if (res.status !== "ok") {
    toast(res.message || "创建失败", "error");
    return;
  }
  toast("已创建 " + name, "ok");
  treeLoaded.clear();
  renderDir("", $("tree-root"));
});

$("btn-refresh-tree").addEventListener("click", () => {
  treeLoaded.clear();
  renderDir("", $("tree-root"));
});

$("btn-upload").addEventListener("click", () => $("file-upload-input").click());

$("file-upload-input").addEventListener("change", async (ev) => {
  const file = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  const targetDir = await dialog("上传文件", "上传 " + file.name + " 到 src/ 下的目录（留空为 src/ 根）：", "prompt", "");
  if (targetDir === null) return;
  const endpoint = "file/upload/" + targetDir.replace(/^\/+|\/+$/g, "");
  toast("正在上传 " + file.name + " ...");
  try {
    const res = await bridge.upload(endpoint, file);
    if (!res || typeof res !== "object" || !res.path) {
      toast("上传失败: " + describeResponse(res), "error");
      return;
    }
    toast("已上传 " + file.name + " → src/" + res.path, "ok");
    treeLoaded.clear();
    renderDir("", $("tree-root"));
  } catch (e) {
    toast("上传失败: " + ((e && e.message) || e), "error");
  }
});

/* ================= 友链（站点配置，条目式编辑） ================= */

let friendLinks = [];

async function loadFriends() {
  const res = await apiGet("external-items", {
    file: "friendsConfig.ts",
    kind: "array",
    name: "friendsConfig",
    field: "friendsConfig",
  });
  if (res.status !== "ok") {
    $("friend-links").textContent = "加载友链失败: " + (res.message || "");
    friendLinks = [];
    return;
  }
  friendLinks = res.data.items || [];
  renderFriends();
}

function friendRowTemplate(f) {
  const tags = Array.isArray(f.tags) ? f.tags.join(", ") : (f.tags || "");
  return (
    '<input class="fd-title" placeholder="标题" value="' + esc(f.title || "") + '">' +
    '<input class="fd-url" placeholder="链接 https://" value="' + esc(f.siteurl || "") + '">' +
    '<input class="fd-avatar" placeholder="头像 URL" value="' + esc(f.imgurl || "") + '">' +
    '<input class="fd-desc" placeholder="描述" value="' + esc(f.desc || "") + '">' +
    '<input class="fd-tags" placeholder="标签，逗号分隔" value="' + esc(tags) + '">' +
    '<input class="fd-weight" type="number" placeholder="权重" value="' + esc(f.weight ?? 10) + '">' +
    '<label class="fd-enabled" title="是否启用"><input type="checkbox" ' + (f.enabled ? "checked" : "") + '> 启用</label>' +
    '<button class="pl-del" title="删除">删除</button>'
  );
}

function renderFriends() {
  const box = $("friend-links");
  box.innerHTML = "";
  if (!friendLinks.length) {
    box.textContent = "暂无友链，点击「添加友链」开始";
    return;
  }
  friendLinks.forEach((link, idx) => {
    const row = document.createElement("div");
    row.className = "friend-row";
    row.innerHTML = friendRowTemplate(link);
    row.querySelector(".pl-del").addEventListener("click", () => {
      friendLinks.splice(idx, 1);
      renderFriends();
    });
    box.appendChild(row);
  });
}

function collectFriends() {
  const rows = document.querySelectorAll("#friend-links .friend-row");
  const out = [];
  rows.forEach((row) => {
    const q = (sel) => row.querySelector(sel).value.trim();
    out.push({
      title: q(".fd-title"),
      imgurl: q(".fd-avatar"),
      desc: q(".fd-desc"),
      siteurl: q(".fd-url"),
      tags: q(".fd-tags")
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter(Boolean),
      weight: Number(row.querySelector(".fd-weight").value || 10),
      enabled: row.querySelector(".fd-enabled input").checked,
    });
  });
  return out;
}

async function saveFriends() {
  const items = collectFriends();
  if (items.some((f) => !f.title || !f.siteurl)) {
    toast("友链的标题和链接必填", "error");
    return;
  }
  const res = await apiPost("external", {
    file: "friendsConfig.ts",
    kind: "array",
    name: "friendsConfig",
    field: "friendsConfig",
    items,
  });
  if (res.status !== "ok") {
    toast(res.message || "保存友链失败", "error");
    return;
  }
  friendLinks = items;
  renderFriends();
  toast("已保存 " + items.length + " 条友链", "ok");
}

$("btn-add-friend").addEventListener("click", () => {
  friendLinks.push({ title: "", imgurl: "", desc: "", siteurl: "", tags: [], weight: 10, enabled: true });
  renderFriends();
});

$("btn-save-friends").addEventListener("click", saveFriends);

/* ================= 对外展示 ================= */

let externalFiles = [];
let extFile = null;
let extTarget = null;
let extItems = [];
let extDirty = false;

function extDisplayName(name) {
  if (name.toLowerCase() === "friends.mdx") return "站点信息";
  const map = { friendsConfig: "友链", socialConfig: "社交链接", footerConfig: "页脚" };
  const base = name.replace(/\.ts$/i, "");
  return map[base] || base.replace(/Config$/i, "").replace(/^[a-z]/, (c) => c.toUpperCase());
}

async function loadExternal() {
  const res = await apiGet("external");
  const tab = $("ext-files");
  tab.innerHTML = "";
  $("ext-hint").textContent = "";
  if (res.status !== "ok") {
    $("ext-hint").textContent = res.message || "加载站点配置失败";
    externalFiles = [];
    return;
  }
  externalFiles = res.data.files || [];
  const dedicated = ["friendsConfig.ts", "profileConfig.ts", "friends.mdx"];
  externalFiles = externalFiles.filter((f) => !dedicated.includes(f.name));
  if (!externalFiles.length) {
    $("ext-hint").textContent = "无其他列表配置（页脚等），可稍后在插件配置中添加外部配置文件";
    return;
  }
  externalFiles.forEach((f) => {
    const b = document.createElement("button");
    b.className = "ext-tab";
    b.textContent = extDisplayName(f.name);
    b.addEventListener("click", async () => {
      if (extDirty) {
        const ok = await dialog("未保存的修改", "当前列表有未保存的修改，切换文件将丢失。仍要继续？", "confirm");
        if (!ok) return;
      }
      await selectExtFile(f, b);
    });
    tab.appendChild(b);
  });
  await selectExtFile(externalFiles[0], tab.querySelector("button"));
}

async function selectExtFile(f, btn) {
  document.querySelectorAll(".ext-tab").forEach((b) => b.classList.remove("active"));
  if (btn) btn.classList.add("active");
  extFile = f;
  extDirty = false;
  const fieldSel = $("ext-field");
  fieldSel.innerHTML = "";
  f.targets.forEach((t, i) => {
    const o = document.createElement("option");
    o.value = i;
    o.textContent =
      t.kind === "object" && t.field === t.name
        ? t.name + "（对象）"
        : t.kind === "array"
          ? t.name + "（列表）"
          : t.field + "（" + t.name + "）";
    fieldSel.appendChild(o);
  });
  fieldSel.value = 0;
  await loadExtItems();
}

function extObjMode() {
  return !!extTarget && extTarget.kind === "object" && extTarget.field === extTarget.name;
}

async function loadExtItems() {
  if (!extFile) return;
  const t = extFile.targets[Number($("ext-field").value)];
  if (!t) return;
  extTarget = t;
  $("ext-hint").textContent =
    "正在加载 " + extDisplayName(extFile.name) + " / " + t.field + " ...";
  const res = await apiGet("external-items", {
    file: extFile.name,
    kind: t.kind,
    name: t.name,
    field: t.field,
  });
  if (res.status !== "ok") {
    $("ext-hint").textContent = res.message || "加载失败";
    return;
  }
  extItems = res.data.items || [];
  extDirty = false;
  $("btn-add-ext").disabled = extObjMode();
  renderExtTable();
  $("ext-hint").textContent =
    extDisplayName(extFile.name) + " / " + t.field +
    (extObjMode() ? "（对象，" + extKeys().length + " 个字段）" : "（" + extItems.length + " 条）");
}

function extKeys() {
  if (Array.isArray(extItems)) {
    const keys = [];
    extItems.forEach((it) =>
      Object.keys(it).forEach((k) => {
        if (!keys.includes(k)) keys.push(k);
      })
    );
    return keys;
  }
  return Object.keys(extItems || {});
}

function externalTableBody() {
  return document.querySelector("#ext-table tbody");
}

function extValueInput(v, onInput) {
  let el;
  if (typeof v === "boolean") {
    el = document.createElement("input");
    el.type = "checkbox";
    el.className = "ext-cell";
    el.checked = v;
  } else if (typeof v === "number") {
    el = document.createElement("input");
    el.type = "number";
    el.className = "ext-cell";
    el.value = v;
  } else if (v && typeof v === "object") {
    el = document.createElement("input");
    el.type = "text";
    el.className = "ext-cell ext-json";
    el.value = JSON.stringify(v);
    el.placeholder = "JSON";
  } else {
    el = document.createElement("input");
    el.type = "text";
    el.className = "ext-cell";
    el.value = v == null ? "" : v;
  }
  el.addEventListener("input", () => onInput());
  return el;
}

function renderExtTable() {
  const thead = document.querySelector("#ext-table thead tr");
  thead.innerHTML = "";
  if (extObjMode()) {
    const thK = document.createElement("th");
    thK.textContent = "字段";
    thead.appendChild(thK);
    const thV = document.createElement("th");
    thV.textContent = "值";
    thead.appendChild(thV);
    const thDel = document.createElement("th");
    thead.appendChild(thDel);
    const tb = externalTableBody();
    tb.innerHTML = "";
    Object.keys(extItems || {}).forEach((k) => {
      const tr = document.createElement("tr");
      const tdK = document.createElement("td");
      tdK.className = "ext-key";
      tdK.textContent = k;
      const tdV = document.createElement("td");
      tdV.appendChild(extValueInput(extItems[k], () => { extDirty = true; }));
      tr.appendChild(tdK);
      tr.appendChild(tdV);
      const tdDel = document.createElement("td");
      const del = document.createElement("button");
      del.className = "row-del";
      del.textContent = "删除";
      del.addEventListener("click", () => {
        tr.remove();
        extDirty = true;
      });
      tdDel.appendChild(del);
      tr.appendChild(tdDel);
      tb.appendChild(tr);
    });
    return;
  }
  const keys = extKeys();
  keys.forEach((k) => {
    const th = document.createElement("th");
    th.textContent = k;
    thead.appendChild(th);
  });
  const thDel = document.createElement("th");
  thead.appendChild(thDel);
  const tb = externalTableBody();
  tb.innerHTML = "";
  extItems.forEach((it) => tb.appendChild(extRow(it, keys)));
}

function extRow(item, keys) {
  const tr = document.createElement("tr");
  keys.forEach((k) => {
    const td = document.createElement("td");
    td.appendChild(extValueInput(item[k], () => { extDirty = true; }));
    tr.appendChild(td);
  });
  const tdDel = document.createElement("td");
  const del = document.createElement("button");
  del.className = "row-del";
  del.textContent = "删除";
  del.addEventListener("click", () => {
    tr.remove();
    extDirty = true;
  });
  tdDel.appendChild(del);
  tr.appendChild(tdDel);
  return tr;
}

function collectExtItems() {
  if (extObjMode()) {
    const items = {};
    document.querySelectorAll("#ext-table tbody tr").forEach((tr) => {
      const k = tr.querySelector(".ext-key").textContent;
      const el = tr.querySelector(".ext-cell");
      if (el.type === "checkbox") {
        items[k] = el.checked;
      } else if (el.type === "number") {
        items[k] = el.value === "" ? 0 : Number(el.value);
      } else if (el.classList.contains("ext-json")) {
        items[k] = el.value.trim() ? JSON.parse(el.value) : "";
      } else {
        items[k] = el.value;
      }
    });
    return items;
  }
  const keys = extKeys();
  const items = [];
  document.querySelectorAll("#ext-table tbody tr").forEach((tr) => {
    const item = {};
    tr.querySelectorAll(".ext-cell").forEach((el, i) => {
      const k = keys[i];
      if (el.type === "checkbox") {
        item[k] = el.checked;
      } else if (el.type === "number") {
        item[k] = el.value === "" ? 0 : Number(el.value);
      } else if (el.classList.contains("ext-json")) {
        item[k] = el.value.trim() ? JSON.parse(el.value) : "";
      } else {
        item[k] = el.value;
      }
    });
    items.push(item);
  });
  return items;
}

$("ext-field").addEventListener("change", async () => {
  if (extDirty) {
    const ok = await dialog("未保存的修改", "切换字段将丢失未保存的修改。仍要继续？", "confirm");
    if (!ok) {
      $("ext-field").value = extFile.targets.indexOf(extTarget);
      return;
    }
  }
  await loadExtItems();
});

$("btn-add-ext").addEventListener("click", () => {
  if (extObjMode()) return;
  const keys = extKeys();
  const empty = {};
  keys.forEach((k) => (empty[k] = ""));
  externalTableBody().appendChild(extRow(empty, keys));
  extDirty = true;
});

$("btn-refresh-ext").addEventListener("click", loadExternal);

$("btn-save-ext").addEventListener("click", async () => {
  if (!extFile || !extTarget) return;
  for (const el of document.querySelectorAll(".ext-cell.ext-json")) {
    if (!el.value.trim()) continue;
    try {
      JSON.parse(el.value);
    } catch (err) {
      toast("字段含无效 JSON 值，请检查后保存", "error");
      return;
    }
  }
  const items = collectExtItems();
  const res = await apiPost("external", {
    file: extFile.name,
    kind: extTarget.kind,
    name: extTarget.name,
    field: extTarget.field,
    items,
  });
  if (res.status !== "ok") {
    toast(res.message || "保存失败", "error");
    return;
  }
  extItems = items;
  extDirty = false;
  toast(
    "已保存 " + extDisplayName(extFile.name) + " / " + extTarget.field +
    (extObjMode() ? "（对象）" : "（" + items.length + " 条）"),
    "ok"
  );
});

/* ================= 站点信息 ================= */

async function loadSiteInfo() {
  const res = await apiGet("site-info");
  if (res.status !== "ok") {
    toast(res.message || "加载站点信息失败", "error");
    return;
  }
  const sc = res.data.site_config || {};
  const site = res.data.site || {};
  $("site-title").value = sc.title ?? "";
  $("site-subtitle").value = sc.subtitle ?? "";
  $("site-url").value = sc.site_url ?? "";
  $("site-description").value = sc.description ?? "";
  $("site-keywords").value = Array.isArray(sc.keywords) ? sc.keywords.join(", ") : "";
  $("site2-name").value = site.name ?? "";
  $("site2-desc").value = site.desc ?? "";
  $("site2-url").value = site.url ?? "";
  $("site2-avatar").value = site.avatar ?? "";
  $("site2-email").value = site.email ?? "";
  renderRssUrl();
}

function collectSiteInfo() {
  const keywords = ($("site-keywords").value || "")
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter(Boolean);
  return {
    site_config: {
      title: $("site-title").value,
      subtitle: $("site-subtitle").value,
      site_url: $("site-url").value,
      description: $("site-description").value,
      keywords,
    },
    site: {
      name: $("site2-name").value,
      desc: $("site2-desc").value,
      url: $("site2-url").value,
      avatar: $("site2-avatar").value,
      email: $("site2-email").value,
    },
  };
}

async function saveSiteInfo(partial) {
  const res = await apiPost("site-info", partial);
  if (res.status !== "ok") {
    toast(res.message || "保存失败", "error");
    return false;
  }
  toast("已保存 " + (res.data.saved || []).join("、"), "ok");
  return true;
}

$("btn-save-sitecfg").addEventListener("click", async () => {
  const all = collectSiteInfo();
  await saveSiteInfo({ site_config: all.site_config });
});

$("btn-save-site2").addEventListener("click", async () => {
  const all = collectSiteInfo();
  await saveSiteInfo({ site: all.site });
});

/* ================= 关于我（对外展示） ================= */

let profileLinks = [];

function profileEmailFromLinks(links) {
  const hit = (links || []).find(
    (l) => String(l.url || "").toLowerCase().startsWith("mailto:")
  );
  return hit ? String(hit.url || "") : "";
}

function profileLinksWithoutEmail(links) {
  return (links || []).filter(
    (l) => !String(l.url || "").toLowerCase().startsWith("mailto:")
  );
}

function renderProfileLinks() {
  const box = $("profile-links");
  box.innerHTML = "";
  if (!profileLinks.length) {
    box.textContent = "暂无其他社交链接";
    return;
  }
  profileLinks.forEach((link, idx) => {
    const row = document.createElement("div");
    row.className = "profile-link-row";
    row.innerHTML =
      '<input class="pl-name" placeholder="名称（如 GitHub）" value="' + esc(link.name || "") + '">' +
      '<input class="pl-icon" placeholder="图标（如 fa7-brands:github）" value="' + esc(link.icon || "") + '">' +
      '<input class="pl-url" placeholder="URL" value="' + esc(link.url || "") + '">' +
      '<button class="pl-del" title="删除">删除</button>';
    row.querySelector(".pl-del").addEventListener("click", () => {
      profileLinks.splice(idx, 1);
      renderProfileLinks();
    });
    box.appendChild(row);
  });
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

async function loadProfile() {
  const res = await apiGet("theme-file", { file: "profileConfig.ts", export: "profileConfig" });
  if (res.status !== "ok") {
    $("profile-links").textContent = "加载失败";
    return;
  }
  const data = res.data.data || {};
  $("profile-avatar").value = data.avatar ?? "";
  $("profile-name").value = data.name ?? "";
  $("profile-bio").value = data.bio ?? "";
  $("profile-email").value = profileEmailFromLinks(data.links);
  profileLinks = profileLinksWithoutEmail(data.links);
  renderProfileLinks();
}

async function saveProfile() {
  const res = await apiGet("theme-file", { file: "profileConfig.ts", export: "profileConfig" });
  if (res.status !== "ok") {
    toast(res.message || "读取关于我配置失败", "error");
    return;
  }
  const data = res.data.data || {};
  data.avatar = $("profile-avatar").value;
  data.name = $("profile-name").value;
  data.bio = $("profile-bio").value;
  const email = String($("profile-email").value || "").trim();
  const other = profileLinks.map((l) => ({
    name: l.name ?? "",
    icon: l.icon ?? "",
    url: l.url ?? "",
    showName: l.showName ?? false,
  }));
  const links = other.slice();
  if (email) {
    const mailUrl = /^mailto:/i.test(email) ? email : "mailto:" + email;
    const existing = links.findIndex((l) => String(l.url || "").toLowerCase().startsWith("mailto:"));
    if (existing >= 0) links[existing].url = mailUrl;
    else links.unshift({ name: "Email", icon: "fa7-solid:envelope", url: mailUrl, showName: false });
  }
  data.links = links;
  const save = await apiPost("theme-file", { file: "profileConfig.ts", export: "profileConfig", data });
  if (save.status !== "ok") {
    toast(save.message || "保存关于我配置失败", "error");
    return;
  }
  toast("已保存关于我", "ok");
}

$("btn-add-profile-link").addEventListener("click", () => {
  profileLinks.push({ name: "", icon: "", url: "", showName: false });
  renderProfileLinks();
});

$("btn-save-profile").addEventListener("click", saveProfile);

/* ================= 留言板（对外展示） ================= */

async function loadComment() {
  const res = await apiGet("theme-file", { file: "commentConfig.ts", export: "commentConfig" });
  if (res.status !== "ok") {
    toast(res.message || "加载留言板配置失败", "error");
    return;
  }
  const data = res.data.data || {};
  const type = String(data.type || "none");
  $("comment-type").value = ["none", "twikoo", "waline", "giscus", "disqus", "artalk"].includes(type)
    ? type
    : "none";
  const env =
    (data.twikoo && data.twikoo.envId) ||
    (data.waline && data.waline.serverURL) ||
    "";
  $("comment-env").value = env || "";
  $("comment-env-wrap").hidden = !(type === "twikoo" || type === "waline");
  $("comment-env").placeholder = type === "twikoo" ? "Twikoo envId" : "Waline serverURL";
}

$("comment-type").addEventListener("change", () => {
  const type = $("comment-type").value;
  $("comment-env-wrap").hidden = !(type === "twikoo" || type === "waline");
  $("comment-env").placeholder = type === "twikoo" ? "Twikoo envId" : "Waline serverURL";
});

async function saveComment() {
  const res = await apiGet("theme-file", { file: "commentConfig.ts", export: "commentConfig" });
  if (res.status !== "ok") {
    toast(res.message || "读取留言板配置失败", "error");
    return;
  }
  const data = res.data.data || {};
  data.type = $("comment-type").value;
  const env = String($("comment-env").value || "").trim();
  if (data.type === "twikoo") {
    if (!data.twikoo || typeof data.twikoo !== "object") data.twikoo = {};
    data.twikoo.envId = env;
  } else if (data.type === "waline") {
    if (!data.waline || typeof data.waline !== "object") data.waline = {};
    data.waline.serverURL = env;
  }
  const save = await apiPost("theme-file", { file: "commentConfig.ts", export: "commentConfig", data });
  if (save.status !== "ok") {
    toast(save.message || "保存留言板配置失败", "error");
    return;
  }
  toast("已保存留言板配置", "ok");
}

$("btn-save-comment").addEventListener("click", saveComment);

/* ================= 打赏（站点配置） ================= */

let sponsorMethods = [];
let sponsorList = [];

function renderSponsorMethods() {
  const box = $("sponsor-methods");
  box.innerHTML = "";
  if (!sponsorMethods.length) {
    box.textContent = "暂无打赏方式";
    return;
  }
  sponsorMethods.forEach((m, idx) => {
    const row = document.createElement("div");
    row.className = "friend-row";
    row.innerHTML =
      '<input class="fm-name" placeholder="名称（如 支付宝）" value="' + esc(m.name || "") + '">' +
      '<input class="fm-icon" placeholder="图标（如 fa7-brands:alipay）" value="' + esc(m.icon || "") + '">' +
      '<input class="fm-qr" placeholder="收款码图（public 下路径）" value="' + esc(m.qrCode || "") + '">' +
      '<input class="fm-link" placeholder="外链（如 ko-fi）" value="' + esc(m.link || "") + '">' +
      '<input class="fm-desc" placeholder="描述" value="' + esc(m.description || "") + '">' +
      '<label class="fd-enabled" title="是否启用"><input type="checkbox" ' + (m.enabled ? "checked" : "") + '> 启用</label>' +
      '<button class="pl-del" title="删除">删除</button>';
    row.querySelector(".pl-del").addEventListener("click", () => {
      sponsorMethods.splice(idx, 1);
      renderSponsorMethods();
    });
    box.appendChild(row);
  });
}

function renderSponsorList() {
  const box = $("sponsor-list");
  box.innerHTML = "";
  if (!sponsorList.length) {
    box.textContent = "暂无打赏者";
    return;
  }
  sponsorList.forEach((s, idx) => {
    const row = document.createElement("div");
    row.className = "friend-row";
    row.innerHTML =
      '<input class="fs-name" placeholder="名称" value="' + esc(s.name || "") + '">' +
      '<input class="fs-amount" placeholder="金额（如 ¥20）" value="' + esc(s.amount || "") + '">' +
      '<input class="fs-date" placeholder="日期（YYYY-MM-DD）" value="' + esc(s.date || "") + '">' +
      '<button class="pl-del" title="删除">删除</button>';
    row.querySelector(".pl-del").addEventListener("click", () => {
      sponsorList.splice(idx, 1);
      renderSponsorList();
    });
    box.appendChild(row);
  });
}

async function loadSponsor() {
  const res = await apiGet("theme-file", { file: "sponsorConfig.ts", export: "sponsorConfig" });
  if (res.status !== "ok") {
    toast(res.message || "加载打赏配置失败", "error");
    return;
  }
  const data = res.data.data || {};
  $("sponsor-title").value = data.title ?? "";
  $("sponsor-desc").value = data.description ?? "";
  $("sponsor-usage").value = data.usage ?? "";
  $("sponsor-show-list").checked = !!data.showSponsorsList;
  $("sponsor-show-comment").checked = !!data.showComment;
  $("sponsor-post-btn").checked = !!data.showButtonInPost;
  sponsorMethods = Array.isArray(data.methods) ? data.methods : [];
  sponsorList = Array.isArray(data.sponsors) ? data.sponsors : [];
  renderSponsorMethods();
  renderSponsorList();
}

async function saveSponsor() {
  const res = await apiGet("theme-file", { file: "sponsorConfig.ts", export: "sponsorConfig" });
  if (res.status !== "ok") {
    toast(res.message || "读取打赏配置失败", "error");
    return;
  }
  const data = res.data.data || {};
  data.title = $("sponsor-title").value;
  data.description = $("sponsor-desc").value;
  data.usage = $("sponsor-usage").value;
  data.showSponsorsList = $("sponsor-show-list").checked;
  data.showComment = $("sponsor-show-comment").checked;
  data.showButtonInPost = $("sponsor-post-btn").checked;
  data.methods = [];
  document.querySelectorAll("#sponsor-methods .friend-row").forEach((row) => {
    data.methods.push({
      name: row.querySelector(".fm-name").value.trim(),
      icon: row.querySelector(".fm-icon").value.trim(),
      qrCode: row.querySelector(".fm-qr").value.trim(),
      link: row.querySelector(".fm-link").value.trim(),
      description: row.querySelector(".fm-desc").value.trim(),
      enabled: row.querySelector(".fd-enabled input").checked,
    });
  });
  data.sponsors = [];
  document.querySelectorAll("#sponsor-list .friend-row").forEach((row) => {
    data.sponsors.push({
      name: row.querySelector(".fs-name").value.trim(),
      amount: row.querySelector(".fs-amount").value.trim(),
      date: row.querySelector(".fs-date").value.trim(),
    });
  });
  const save = await apiPost("theme-file", { file: "sponsorConfig.ts", export: "sponsorConfig", data });
  if (save.status !== "ok") {
    toast(save.message || "保存打赏配置失败", "error");
    return;
  }
  toast("已保存打赏配置", "ok");
}

$("btn-add-method").addEventListener("click", () => {
  sponsorMethods.push({ name: "", icon: "", qrCode: "", link: "", description: "", enabled: true });
  renderSponsorMethods();
});
$("btn-add-sponsor").addEventListener("click", () => {
  sponsorList.push({ name: "", amount: "", date: "" });
  renderSponsorList();
});
$("btn-save-sponsor").addEventListener("click", saveSponsor);

/* ================= 相册（站点配置） ================= */

let galleryAlbums = [];

function renderGalleryAlbums() {
  const box = $("gallery-albums");
  box.innerHTML = "";
  if (!galleryAlbums.length) {
    box.textContent = "暂无相册";
    return;
  }
  galleryAlbums.forEach((a, idx) => {
    const row = document.createElement("div");
    row.className = "friend-row";
    const tags = Array.isArray(a.tags) ? a.tags.join(", ") : (a.tags || "");
    row.innerHTML =
      '<input class="ga-id" placeholder="id（目录名）" value="' + esc(a.id || "") + '">' +
      '<input class="ga-name" placeholder="名称" value="' + esc(a.name || "") + '">' +
      '<input class="ga-desc" placeholder="描述" value="' + esc(a.description || "") + '">' +
      '<input class="ga-location" placeholder="地点" value="' + esc(a.location || "") + '">' +
      '<input class="ga-date" placeholder="日期 YYYY-MM-DD" value="' + esc(a.date || "") + '">' +
      '<input class="ga-tags" placeholder="标签，逗号分隔" value="' + esc(tags) + '">' +
      '<input class="ga-password" placeholder="访问密码（留空不加密）" value="' + esc(a.password || "") + '">' +
      '<button class="pl-del" title="删除">删除</button>';
    row.querySelector(".pl-del").addEventListener("click", () => {
      galleryAlbums.splice(idx, 1);
      renderGalleryAlbums();
    });
    box.appendChild(row);
  });
}

async function loadGallery() {
  const res = await apiGet("theme-file", { file: "galleryConfig.ts", export: "galleryConfig" });
  if (res.status !== "ok") {
    toast(res.message || "加载相册配置失败", "error");
    return;
  }
  const data = res.data.data || {};
  $("gallery-column").value = data.columnWidth ?? 240;
  galleryAlbums = Array.isArray(data.albums) ? data.albums : [];
  renderGalleryAlbums();
}

async function saveGallery() {
  const res = await apiGet("theme-file", { file: "galleryConfig.ts", export: "galleryConfig" });
  if (res.status !== "ok") {
    toast(res.message || "读取相册配置失败", "error");
    return;
  }
  const data = res.data.data || {};
  const col = parseInt($("gallery-column").value, 10);
  data.columnWidth = Number.isNaN(col) ? 240 : col;
  data.albums = [];
  document.querySelectorAll("#gallery-albums .friend-row").forEach((row) => {
    const tags = row
      .querySelector(".ga-tags").value
      .split(/[,，]/)
      .map((s) => s.trim())
      .filter(Boolean);
    const album = {
      id: row.querySelector(".ga-id").value.trim(),
      name: row.querySelector(".ga-name").value.trim(),
      description: row.querySelector(".ga-desc").value.trim(),
      location: row.querySelector(".ga-location").value.trim(),
      date: row.querySelector(".ga-date").value.trim(),
      tags,
    };
    const password = row.querySelector(".ga-password").value.trim();
    if (password) {
      album.password = password;
      album.passwordHint = password;
    }
    data.albums.push(album);
  });
  const save = await apiPost("theme-file", { file: "galleryConfig.ts", export: "galleryConfig", data });
  if (save.status !== "ok") {
    toast(save.message || "保存相册配置失败", "error");
    return;
  }
  toast("已保存相册配置", "ok");
}

$("btn-add-album").addEventListener("click", () => {
  galleryAlbums.push({ id: "", name: "", description: "", location: "", date: "", tags: [], password: "", passwordHint: "" });
  renderGalleryAlbums();
});
$("btn-save-gallery").addEventListener("click", saveGallery);

/* ================= 公告（站点配置） ================= */

async function loadAnnouncement() {
  const res = await apiGet("theme-file", { file: "announcementConfig.ts", export: "announcementConfig" });
  if (res.status !== "ok") {
    toast(res.message || "加载公告配置失败", "error");
    return;
  }
  const data = res.data.data || {};
  $("announce-title").value = data.title ?? "";
  $("announce-content").value = data.content ?? "";
  $("announce-closable").checked = !!data.closable;
  const link = data.link || {};
  $("announce-link-enable").checked = !!link.enable;
  $("announce-link-text").value = link.text ?? "";
  $("announce-link-url").value = link.url ?? "";
}

async function saveAnnouncement() {
  const res = await apiGet("theme-file", { file: "announcementConfig.ts", export: "announcementConfig" });
  if (res.status !== "ok") {
    toast(res.message || "读取公告配置失败", "error");
    return;
  }
  const data = res.data.data || {};
  data.title = $("announce-title").value;
  data.content = $("announce-content").value;
  data.closable = $("announce-closable").checked;
  if (!data.link || typeof data.link !== "object") data.link = {};
  data.link.enable = $("announce-link-enable").checked;
  data.link.text = $("announce-link-text").value;
  data.link.url = $("announce-link-url").value;
  const save = await apiPost("theme-file", { file: "announcementConfig.ts", export: "announcementConfig", data });
  if (save.status !== "ok") {
    toast(save.message || "保存公告配置失败", "error");
    return;
  }
  toast("已保存公告", "ok");
}

$("btn-save-announce").addEventListener("click", saveAnnouncement);

/* ================= 音乐（站点配置） ================= */

async function loadMusic() {
  const res = await apiGet("theme-file", { file: "musicConfig.ts", export: "musicPlayerConfig" });
  if (res.status !== "ok") {
    toast(res.message || "加载音乐配置失败", "error");
    return;
  }
  const data = res.data.data || {};
  $("music-mode").value = data.mode === "local" ? "local" : "meting";
  $("music-navbar").checked = !!data.showInNavbar;
  $("music-lyrics").checked = !!data.showLyrics;
  const meting = data.meting || {};
  $("music-server").value = ["netease", "tencent", "kugou", "xiami", "baidu"].includes(meting.server)
    ? meting.server
    : "netease";
  $("music-id").value = meting.id ?? "";
}

async function saveMusic() {
  const res = await apiGet("theme-file", { file: "musicConfig.ts", export: "musicPlayerConfig" });
  if (res.status !== "ok") {
    toast(res.message || "读取音乐配置失败", "error");
    return;
  }
  const data = res.data.data || {};
  data.mode = $("music-mode").value;
  data.showInNavbar = $("music-navbar").checked;
  data.showLyrics = $("music-lyrics").checked;
  if (!data.meting || typeof data.meting !== "object") data.meting = {};
  data.meting.server = $("music-server").value;
  data.meting.id = $("music-id").value;
  const save = await apiPost("theme-file", { file: "musicConfig.ts", export: "musicPlayerConfig", data });
  if (save.status !== "ok") {
    toast(save.message || "保存音乐配置失败", "error");
    return;
  }
  toast("已保存音乐配置", "ok");
}

$("btn-save-music").addEventListener("click", saveMusic);

/* ================= RSS（站点配置，联动站点信息） ================= */

function renderRssUrl() {
  const base = String($("site-url").value || "").replace(/\/+$/, "");
  $("rss-url").value = base ? base + "/rss.xml" : "（请先填写站点 URL）";
}

/* ================= 插件配置 ================= */

const PLUGIN_SECRET_FIELDS = ["password"];
const PLUGIN_GROUPS = [
  {
    key: "deploy",
    name: "部署与构建",
    fields: [
      "deploy_mode", "local_blog_root", "web_root",
      "server_ip", "server_port", "username", "auth_type",
      "private_key_path", "password", "ssh_known_hosts_path",
      "ssh_strict_host_key_checking",
      "remote_blog_root", "remote_web_root",
      "build_memory_threshold", "build_memory_limit", "allow_build_concurrent",
    ],
  },
  { key: "admin", name: "权限", fields: ["admin_umo"] },
  {
    key: "features",
    name: "功能开关",
    fields: [
      "enable_advanced_syntax",
      "advanced_syntax_github_card", "advanced_syntax_admonitions",
      "advanced_syntax_spoiler", "advanced_syntax_image_grid",
      "advanced_syntax_code_blocks", "advanced_syntax_mermaid",
      "advanced_syntax_plantuml", "advanced_syntax_katex",
      "enable_ai_review",
    ],
  },
  { key: "other", name: "其他", fields: ["external_config_files"] },
];

let pluginSchema = {};
let pluginValues = {};

function pluginFieldEl(name, meta, value) {
  const wrap = document.createElement("div");
  wrap.className = "plugin-field";
  const label = document.createElement("label");
  label.className = "plugin-label";
  label.textContent = name;
  wrap.appendChild(label);
  if (meta.description) {
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent = meta.description;
    wrap.appendChild(hint);
  }
  let input;
  if (meta.type === "bool") {
    input = document.createElement("input");
    input.type = "checkbox";
    input.checked = !!value;
    wrap.appendChild(input);
  } else if (Array.isArray(meta.options)) {
    input = document.createElement("select");
    meta.options.forEach((opt) => {
      const o = document.createElement("option");
      o.value = opt;
      o.textContent = opt;
      input.appendChild(o);
    });
    input.value = String(value ?? "");
    wrap.appendChild(input);
  } else if (meta.type === "int") {
    input = document.createElement("input");
    input.type = "number";
    input.value = value ?? "";
    wrap.appendChild(input);
  } else {
    input = document.createElement("input");
    input.type = "text";
    input.value = value ?? "";
    if (PLUGIN_SECRET_FIELDS.includes(name)) {
      input.placeholder = "留空则不修改";
      input.type = "password";
    }
    wrap.appendChild(input);
  }
  input.dataset.name = name;
  wrap.dataset.name = name;
  return wrap;
}

async function loadPluginConfig() {
  const res = await apiGet("plugin-config");
  if (res.status !== "ok") {
    $("plugin-hint").textContent = res.message || "加载插件配置失败";
    pluginSchema = {};
    pluginValues = {};
    return;
  }
  pluginSchema = res.data.schema || {};
  pluginValues = res.data.values || {};
  $("plugin-hint").textContent = "";
  const box = $("plugin-form");
  box.innerHTML = "";
  PLUGIN_GROUPS.forEach((g) => {
    const section = document.createElement("div");
    section.className = "plugin-group";
    const title = document.createElement("h4");
    title.textContent = g.name;
    section.appendChild(title);
    g.fields.forEach((name) => {
      const meta = pluginSchema[name];
      if (!meta) return;
      section.appendChild(pluginFieldEl(name, meta, pluginValues[name]));
    });
    box.appendChild(section);
  });
}

function collectPluginConfig() {
  const out = {};
  document.querySelectorAll("#plugin-form [data-name]").forEach((el) => {
    const name = el.dataset.name;
    const meta = pluginSchema[name] || {};
    if (meta.type === "bool") {
      out[name] = el.checked;
    } else if (meta.type === "int") {
      const n = parseInt(el.value, 10);
      out[name] = Number.isNaN(n) ? null : n;
    } else if (meta.type === "list") {
      out[name] = (el.value || "")
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter(Boolean);
    } else {
      out[name] = el.value;
    }
  });
  return out;
}

$("btn-save-plugin").addEventListener("click", async () => {
  const payload = collectPluginConfig();
  const res = await apiPost("plugin-config", { config: payload });
  if (res.status !== "ok") {
    toast(res.message || "保存插件配置失败", "error");
    return;
  }
  toast("已保存插件配置（" + (res.data.saved || []).length + " 项）", "ok");
  await loadPluginConfig();
});

$("btn-refresh-plugin").addEventListener("click", loadPluginConfig);

/* ================= 主题配置 ================= */

let themeFiles = [];
let themeCurrent = null;
let themeDirty = false;

async function loadThemeFiles() {
  const res = await apiGet("theme-files");
  const box = $("theme-groups");
  box.innerHTML = "";
  $("theme-hint").textContent = "";
  if (res.status !== "ok") {
    $("theme-hint").textContent = res.message || "加载主题配置失败";
    themeFiles = [];
    return;
  }
  themeFiles = res.data.files || [];
  const groups = res.data.groups || [];
  groups.forEach((g) => {
    const mods = themeFiles.filter((f) => f.group === g.key);
    if (!mods.length) return;
    const sec = document.createElement("div");
    sec.className = "theme-group";
    const title = document.createElement("div");
    title.className = "theme-group-name";
    title.textContent = g.name;
    sec.appendChild(title);
    mods.forEach((m) => {
      m.exports.forEach((e) => sec.appendChild(themeModBtn(m, e)));
      m.source_only.forEach((s) => {
        const d = document.createElement("div");
        d.className = "theme-mod theme-src-only";
        d.textContent = m.name + " / " + s + "（源码生成）";
        d.title = "该导出由代码动态生成，请在文件管理中编辑源码";
        sec.appendChild(d);
      });
    });
    box.appendChild(sec);
  });
  const first = themeFiles.find((f) => f.exports.length) || null;
  if (first) await selectThemeFile(first, first.exports[0]);
}

function themeModBtn(m, e) {
  const b = document.createElement("button");
  b.className = "theme-mod" + (themeDirty ? "" : "");
  b.textContent = m.name + (m.exports.length > 1 ? " / " + e.export : "");
  b.title = m.desc || "";
  b.addEventListener("click", async () => {
    if (themeDirty) {
      const ok = await dialog("未保存的修改", "当前配置有未保存的修改，切换将丢失。仍要继续？", "confirm");
      if (!ok) return;
    }
    await selectThemeFile(m, e);
  });
  return b;
}

async function selectThemeFile(m, e) {
  document.querySelectorAll(".theme-mod").forEach((x) => x.classList.remove("active"));
  themeCurrent = { file: m.file, export: e.export, meta: m };
  themeDirty = false;
  $("theme-current").textContent = m.file + " → " + e.export;
  $("theme-hint").textContent = "正在加载 " + m.name + " ...";
  const res = await apiGet("theme-file", { file: m.file, export: e.export });
  if (res.status !== "ok") {
    $("theme-hint").textContent = res.message || "加载失败";
    $("theme-form").innerHTML = "";
    return;
  }
  $("theme-hint").textContent = m.desc || "";
  renderThemeForm(res.data);
  $("btn-save-theme").disabled = true;
  $("btn-revert-theme").disabled = false;
}

/* ---- 动态表单渲染 ---- */

function renderThemeForm(info) {
  const root = $("theme-form");
  root.innerHTML = "";
  const readonly = info.readonly || {};
  const enums = info.enums || {};
  if (info.kind === "array") {
    const box = document.createElement("div");
    box.className = "tf-group tf-array tf-arr-objects";
    box.dataset.tarr = "";
    renderTfTable(box, info.data || [], "", readonly, enums);
    root.appendChild(box);
  } else {
    renderTfObject(root, info.data || {}, "", readonly, enums);
  }
}

function tfKeyLabel(key) {
  return key.replace(/[A-Z]/g, (c) => " " + c.toLowerCase()).replace(/^[a-z]/, (c) => c.toUpperCase());
}

function renderTfObject(container, obj, path, readonly, enums) {
  const g = document.createElement("div");
  g.className = "tf-group" + (path ? " tf-nested" : "");
  Object.keys(obj).forEach((k) => {
    const p = path ? path + "." + k : k;
    const v = obj[k];
    if (v && typeof v === "object" && !Array.isArray(v)) {
      renderTfObject(g, v, p, readonly, enums);
      return;
    }
    if (Array.isArray(v)) {
      if (p in readonly) return;
      if (p + "[]" in readonly) return;
      if (v.length && v[0] && typeof v[0] === "object") {
        renderTfObjectArray(g, p, v, readonly, enums);
      } else {
        renderTfScalarArray(g, p, v);
      }
      return;
    }
    renderTfLeaf(g, p, k, v, readonly, enums);
  });
  container.appendChild(g);
}

function renderTfLeaf(container, path, key, value, readonly, enums) {
  const row = document.createElement("div");
  row.className = "tf-row";
  const label = document.createElement("span");
  label.className = "tf-key";
  label.textContent = tfKeyLabel(key);
  row.appendChild(label);
  let el;
  if (path in readonly) {
    el = document.createElement("input");
    el.type = "text";
    el.className = "tf-ctrl tf-readonly";
    el.value = String(value);
    el.disabled = true;
    el.title = "由源码定义（" + readonly[path] + "），请通过文件管理编辑";
    const tag = document.createElement("span");
    tag.className = "tf-ro-tag";
    tag.textContent = "源码定义";
    row.appendChild(el);
    row.appendChild(tag);
  } else if (typeof value === "boolean") {
    el = document.createElement("input");
    el.type = "checkbox";
    el.className = "tf-ctrl";
    el.checked = value;
    row.appendChild(el);
  } else if (enums[path] && enums[path].length) {
    el = document.createElement("select");
    el.className = "tf-ctrl";
    enums[path].forEach((o) => {
      const op = document.createElement("option");
      op.value = o;
      op.textContent = o;
      el.appendChild(op);
    });
    el.value = String(value);
    row.appendChild(el);
  } else if (typeof value === "number") {
    el = document.createElement("input");
    el.type = "number";
    el.className = "tf-ctrl";
    el.value = value;
    row.appendChild(el);
  } else {
    el = document.createElement("input");
    el.type = "text";
    el.className = "tf-ctrl";
    el.value = value == null ? "" : String(value);
    row.appendChild(el);
  }
  el.dataset.tpath = path;
  el.addEventListener("input", () => {
    themeDirty = true;
    $("btn-save-theme").disabled = false;
  });
  container.appendChild(row);
}

function renderTfScalarArray(container, path, values) {
  const box = document.createElement("div");
  box.className = "tf-group tf-array";
  box.dataset.tarr = path;
  const title = document.createElement("div");
  title.className = "tf-arr-title";
  title.textContent = tfKeyLabel(path.split(".").pop());
  box.appendChild(title);
  const items = document.createElement("div");
  items.className = "tf-arr-items";
  values.forEach((v) => items.appendChild(tfScalarRow(v)));
  box.appendChild(items);
  const add = document.createElement("button");
  add.className = "row-add";
  add.textContent = "添加";
  add.addEventListener("click", () => {
    items.appendChild(tfScalarRow(""));
    themeDirty = true;
    $("btn-save-theme").disabled = false;
  });
  box.appendChild(add);
  container.appendChild(box);
}

function tfScalarRow(v) {
  const row = document.createElement("div");
  row.className = "tf-arr-item";
  const input = document.createElement("input");
  input.type = "text";
  input.className = "tf-ctrl tf-arr-input";
  input.value = v == null ? "" : String(v);
  row.appendChild(input);
  const del = document.createElement("button");
  del.className = "row-del";
  del.textContent = "删除";
  del.addEventListener("click", () => {
    row.remove();
    themeDirty = true;
    $("btn-save-theme").disabled = false;
  });
  row.appendChild(del);
  return row;
}

function renderTfObjectArray(container, path, items, readonly, enums) {
  const box = document.createElement("div");
  box.className = "tf-group tf-array tf-arr-objects";
  box.dataset.tarr = path;
  const title = document.createElement("div");
  title.className = "tf-arr-title";
  title.textContent = tfKeyLabel(path.split(".").pop());
  box.appendChild(title);
  const tb = document.createElement("tbody");
  tb.className = "tf-table-body";
  renderTfTableRows(tb, items, path, readonly, enums);
  box.appendChild(tb);
  const add = document.createElement("button");
  add.className = "row-add";
  add.textContent = "添加条目";
  add.addEventListener("click", () => {
    const empty = {};
    (Object.keys(items[0] || {})).forEach((k) => (empty[k] = ""));
    renderTfTableRow(tb, empty, path, readonly, enums, false);
    themeDirty = true;
    $("btn-save-theme").disabled = false;
  });
  box.appendChild(add);
  container.appendChild(box);
}

function renderTfTableRows(tb, items, path, readonly, enums) {
  items.forEach((it) => renderTfTableRow(tb, it, path, readonly, enums, true));
}

function renderTfTableRow(tb, item, path, readonly, enums, isExisting) {
  const tr = document.createElement("tr");
  Object.keys(item).forEach((k) => {
    const td = document.createElement("td");
    td.dataset.k = k;
    const v = item[k];
    const pReal = path + "[]." + k;
    let el;
    if (isExisting && pReal in readonly) {
      el = document.createElement("input");
      el.type = "text";
      el.disabled = true;
      el.value = String(v);
      el.title = "由源码定义";
    } else if (typeof v === "boolean") {
      el = document.createElement("input");
      el.type = "checkbox";
      el.checked = v;
    } else if (typeof v === "number") {
      el = document.createElement("input");
      el.type = "number";
      el.value = v;
    } else if (v && typeof v === "object") {
      el = document.createElement("input");
      el.type = "text";
      el.className = "tf-json";
      el.value = JSON.stringify(v);
      el.placeholder = "JSON";
    } else {
      el = document.createElement("input");
      el.type = "text";
      el.value = v == null ? "" : String(v);
    }
    el.classList.add("tf-ctrl");
    el.addEventListener("input", () => {
      themeDirty = true;
      $("btn-save-theme").disabled = false;
    });
    td.appendChild(el);
    tr.appendChild(td);
  });
  const tdDel = document.createElement("td");
  const del = document.createElement("button");
  del.className = "row-del";
  del.textContent = "删除";
  del.addEventListener("click", () => {
    tr.remove();
    themeDirty = true;
    $("btn-save-theme").disabled = false;
  });
  tdDel.appendChild(del);
  tr.appendChild(tdDel);
  tb.appendChild(tr);
}

/* ---- 数据收集 ---- */

function collectThemeData() {
  const data = {};
  document.querySelectorAll("#theme-form [data-tpath]").forEach((el) => {
    let val;
    if (el.type === "checkbox") val = el.checked;
    else if (el.type === "number") val = el.value === "" ? 0 : Number(el.value);
    else if (el.classList.contains("tf-json")) val = el.value.trim() ? JSON.parse(el.value) : "";
    else val = el.value;
    setPath(data, el.dataset.tpath, val);
  });
  document.querySelectorAll("#theme-form [data-tarr]").forEach((box) => {
    const path = box.dataset.tarr;
    if (box.classList.contains("tf-arr-objects")) {
      const arr = [];
      box.querySelectorAll("tbody tr").forEach((tr) => {
        const item = {};
        tr.querySelectorAll("td[data-k]").forEach((td) => {
          const el = td.querySelector("input");
          if (!el || el.disabled) return;
          const k = td.dataset.k;
          if (el.type === "checkbox") item[k] = el.checked;
          else if (el.type === "number") item[k] = el.value === "" ? 0 : Number(el.value);
          else if (el.classList.contains("tf-json")) item[k] = el.value.trim() ? JSON.parse(el.value) : "";
          else item[k] = el.value;
        });
        arr.push(item);
      });
      setPath(data, path, arr);
    } else {
      const arr = [];
      box.querySelectorAll(".tf-arr-input").forEach((el) => arr.push(el.value));
      setPath(data, path, arr);
    }
  });
  return data;
}

function setPath(obj, path, val) {
  const parts = path.split(".");
  let cur = obj;
  parts.forEach((p, i) => {
    if (i === parts.length - 1) {
      cur[p] = val;
    } else {
      if (typeof cur[p] !== "object" || cur[p] === null) cur[p] = {};
      cur = cur[p];
    }
  });
}

$("btn-save-theme").addEventListener("click", async () => {
  if (!themeCurrent) return;
  for (const el of document.querySelectorAll("#theme-form .tf-json")) {
    if (!el.value.trim()) continue;
    try {
      JSON.parse(el.value);
    } catch (err) {
      toast("存在无效 JSON 值，请检查后保存", "error");
      return;
    }
  }
  const res = await apiPost("theme-file", {
    file: themeCurrent.file,
    export: themeCurrent.export,
    data: collectThemeData(),
  });
  if (res.status !== "ok") {
    toast(res.message || "保存失败", "error");
    return;
  }
  themeDirty = false;
  $("btn-save-theme").disabled = true;
  toast("已保存 " + themeCurrent.file + "（已备份 .bak，构建部署后生效）", "ok");
});

$("btn-revert-theme").addEventListener("click", async () => {
  if (!themeCurrent) return;
  if (themeDirty) {
    const ok = await dialog("还原", "将放弃未保存的修改并重新加载。继续？", "confirm");
    if (!ok) return;
  }
  await selectThemeFile(themeCurrent.meta, { export: themeCurrent.export });
});

$("btn-refresh-theme").addEventListener("click", loadThemeFiles);

/* ================= 构建与部署（状态页） ================= */

let buildPollTimer = null;

async function startBuild(mode) {
  const res = await apiPost("build-deploy", { mode });
  if (res.status !== "ok") {
    toast(res.message || "任务启动失败", "error");
    return;
  }
  $("build-log").textContent = "任务已启动，正在执行...";
  pollBuild(res.data.task_id);
}

function pollBuild(tid) {
  clearInterval(buildPollTimer);
  buildPollTimer = setInterval(async () => {
    const res = await apiGet("build-deploy", { task_id: tid });
    if (res.status !== "ok") {
      clearInterval(buildPollTimer);
      $("build-log").textContent = "查询任务状态失败: " + (res.message || "");
      return;
    }
    const t = res.data.task;
    $("build-log").textContent = (t.log || []).join("\n") || t.message || "执行中...";
    if (t.status === "done") {
      clearInterval(buildPollTimer);
      if (t.message) $("build-log").textContent = t.message;
      toast(t.ok ? "构建部署完成" : "构建部署失败", t.ok ? "ok" : "error");
    }
  }, 1500);
}

$("btn-build").addEventListener("click", () => startBuild("build"));
$("btn-deploy").addEventListener("click", () => startBuild("deploy"));
$("btn-build-deploy").addEventListener("click", () => startBuild("both"));

/* ================= 壁纸背景 ================= */

function deviceKind() {
  return window.innerWidth <= 768 ? "mobile" : "desktop";
}

async function loadWallpaper() {
  const banner = $("deploy-hint");
  const device = deviceKind();
  const res = await apiGet("wallpaper", { device });
  if (res.status !== "ok") {
    const msg = res.message || "";
    banner.hidden = !(msg.includes("未检测到") || msg.includes("deploy") || msg.includes("部署"));
    return;
  }
  banner.hidden = true;
  document.body.classList.add("has-bg");
  document.body.classList.toggle("mobile", device === "mobile");
  document.body.style.backgroundImage = "url(" + res.data.content + ")";
}

/* ================= 初始化 ================= */

(async function init() {
  if (!bridge) {
    toast("bridge SDK 未加载，请刷新页面重试", "error");
    return;
  }
  try {
    await bridge.ready();
  } catch (e) {
    toast("bridge 初始化失败", "error");
    return;
  }
  await loadWallpaper();
  await loadStatus();
  await renderDir("", $("tree-root"));
  await loadExternal();
  await loadFriends();
  await loadSiteInfo();
  await loadAnnouncement();
  await loadMusic();
  await loadComment();
  await loadSponsor();
  await loadGallery();
  await loadProfile();
  await loadThemeFiles();
  await loadPluginConfig();
})();
