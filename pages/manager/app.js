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

/* ================= 对外展示 ================= */

let externalFiles = [];
let extFile = null;
let extTarget = null;
let extItems = [];
let extDirty = false;

function extDisplayName(name) {
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
    $("ext-hint").textContent = res.message || "加载对外展示配置失败";
    externalFiles = [];
    return;
  }
  externalFiles = res.data.files || [];
  if (!externalFiles.length) {
    $("ext-hint").textContent = "未找到可编辑的对外展示配置（friendsConfig.ts 等），请确认 src/config 目录下存在";
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
      t.kind === "array" ? t.name + "（列表）" : t.field + "（" + t.name + "）";
    fieldSel.appendChild(o);
  });
  fieldSel.value = 0;
  await loadExtItems();
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
  renderExtTable();
  $("ext-hint").textContent =
    extDisplayName(extFile.name) + " / " + t.field + "（" + extItems.length + " 条）";
}

function extKeys() {
  const keys = [];
  extItems.forEach((it) =>
    Object.keys(it).forEach((k) => {
      if (!keys.includes(k)) keys.push(k);
    })
  );
  return keys;
}

function externalTableBody() {
  return document.querySelector("#ext-table tbody");
}

function renderExtTable() {
  const thead = document.querySelector("#ext-table thead tr");
  const keys = extKeys();
  thead.innerHTML = "";
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
    const v = item[k];
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
    el.addEventListener("input", () => {
      extDirty = true;
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
    extDirty = true;
  });
  tdDel.appendChild(del);
  tr.appendChild(tdDel);
  return tr;
}

function collectExtItems() {
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
  toast("已保存 " + extDisplayName(extFile.name) + " / " + extTarget.field + "（" + items.length + " 条）", "ok");
});

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
})();
