"use strict";

const $ = (id) => document.getElementById(id);
const session = document.querySelector('meta[name="savesync-token"]').content;
let state;
let selected;
let reviewRequest = 0;
let mapSession = 0;
let busy = false;
let transferStatus = null;

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function button(label, action, className) {
  const node = element("button", label, className);
  node.type = "button";
  node.addEventListener("click", action);
  return node;
}

function iconButton(icon, label, action) {
  const node = button(icon, action, "icon-button");
  node.setAttribute("aria-label", label);
  node.title = label;
  return node;
}

async function api(endpoint, payload = {}) {
  const response = await fetch("/api/" + endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-SaveSync-Token": session },
    body: JSON.stringify(payload),
  });
  if (!response.headers.get("content-type")?.includes("application/json")) {
    throw new Error("The local server did not respond. Reopen SaveSync from its launcher.");
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error);
  return data;
}

function notice(message, error = false) {
  const node = $("notice");
  node.hidden = !message;
  node.textContent = message;
  node.classList.toggle("error", error);
}

function formError(form, message) {
  const node = form.querySelector(".form-error");
  node.textContent = message;
  node.hidden = !message;
}

function openDialog(id) {
  const dialog = $(id);
  formError(dialog.querySelector("form"), "");
  dialog.showModal();
}

function bindForm(id, action) {
  const form = $(id);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = form.querySelector('[type="submit"]');
    if (submit.disabled) return;
    submit.disabled = true;
    formError(form, "");
    try {
      await action(Object.fromEntries(new FormData(form)));
    } catch (error) {
      formError(form, error.message);
    } finally {
      submit.disabled = false;
    }
  });
}

async function refresh(gameId = selected) {
  state = await api("state");
  selected = state.games.some((game) => game.id === gameId) ? gameId : state.games[0]?.id;
  $("device-label").textContent = state.device + " · " + state.platform;
  $("shared-label").textContent = state.cloud?.paired
    ? (state.cloud.online ? "Cloud saves connected · " : "Cloud saves unavailable · ") + state.cloud.url
    : state.shared ? "Shared folder · " + state.shared : "Pair with a cloud server in Settings.";
  if (state.warning) notice(state.warning, true);
  if (state.cloud?.warning) notice(state.cloud.warning, true);
  renderLibrary();
  await showGame();
}

function renderLibrary() {
  const list = $("games");
  const focused = document.activeElement?.dataset.game;
  list.replaceChildren();
  if (!state.games.length) list.append(element("p", "No games added yet."));
  for (const game of state.games) {
    const item = button(game.title, () => {
      if (busy) return;
      selected = game.id;
      notice("");
      renderLibrary();
      showGame();
    }, "game");
    item.dataset.game = game.id;
    item.setAttribute("aria-current", String(game.id === selected));
    item.append(element("span", game.path ? "Mapped on this device" : "Set up on this device"));
    list.append(item);
    if (game.id === focused) item.focus({ preventScroll: true });
  }
}

function openSettings() {
  const fields = $("settings-form").elements;
  for (const key of ["device", "shared", "ludusavi"]) fields[key].value = state[key];
  const tools = $("settings-ludusavi");
  tools.replaceChildren();
  if (!state.ludusavi_ready) {
    tools.append(ludusaviInstallButton(() => {
      fields.ludusavi.value = state.ludusavi;
      tools.replaceChildren(element("span", "Ludusavi installed. Detection is ready.", "hint"));
    }));
  }
  openDialog("settings-dialog");
}

function openCloudSettings() {
  const form = $("cloud-form");
  const paired = Boolean(state.cloud?.paired);
  form.elements.url.value = state.cloud?.url || "";
  form.elements.code.value = "";
  form.elements.code.type = "password";
  $("cloud-show-code").setAttribute("aria-pressed", "false");
  $("cloud-pair-fields").hidden = paired;
  $("cloud-paired").hidden = !paired;
  $("cloud-pair").hidden = paired;
  $("cloud-url").textContent = state.cloud?.url || "";
  $("cloud-invite-field").hidden = true;
  $("cloud-invite-code").value = "";
  $("settings-dialog").close();
  openDialog("cloud-dialog");
}

function ludusaviInstallButton(onReady) {
  const node = button("Download Ludusavi", async () => {
    node.disabled = true;
    node.textContent = "Downloading Ludusavi…";
    try {
      const result = await api("ludusavi", { action: "install" });
      state.ludusavi = result.path;
      state.ludusavi_ready = true;
      onReady(result);
    } catch (error) {
      node.disabled = false;
      node.textContent = "Download Ludusavi";
      notice(error.message, true);
    }
  }, "primary");
  return node;
}

function openMap(game) {
  const form = $("map-form");
  const hasLocation = Boolean(game?.path);
  mapSession++;
  form.dataset.game = game?.id || "";
  const fields = form.elements;
  fields.title.value = game?.title || "";
  fields.install.value = game?.install || "";
  fields.install.closest("label").hidden = false;
  fields.path.value = game?.path || "";
  fields.path.required = hasLocation;
  fields.path.placeholder = hasLocation
    ? "Enter an absolute path"
    : "Leave blank to detect automatically, or enter an absolute path";
  fields.prefix.value = "";
  $("map-proton").open = false;
  $("map-proton").hidden = hasLocation || state?.platform !== "Linux";
  fields.mode.value = game?.mode || "auto";
  fields.title.readOnly = hasLocation;
  $("map-title-label").textContent = game && !hasLocation ? "Game title for save detection" : "Game title";
  $("map-rename").hidden = !game || hasLocation;
  $("map-rename").disabled = false;
  $("map-candidates").replaceChildren();
  $("map-heading").textContent = game ? "Set local save location" : "Add game";
  updateMapSubmit(form);
  openDialog("map-dialog");
}

const statuses = {
  empty: ["No saves found", "Make a save in the game, then check again."],
  upload: ["Ready to upload", "Publish this device’s save files to the shared folder."],
  download: ["Ready to download", "Apply the shared version to this device."],
  current: ["Up to date", "This device has the same saves as the shared version."],
  conflict: ["Different save versions found", "Open Save history to choose a shared version, or keep this device’s saves."],
  interrupted: ["Restore interrupted", "Recover the original local files before running another sync."],
};

async function runSaveDetection(game, action) {
  if (busy) return;
  busy = true;
  try {
    const result = await api("detect_save", { id: game.id, action });
    notice(result.message);
    await refresh(game.id);
  } catch (error) {
    notice(error.message, true);
  } finally {
    busy = false;
  }
}

async function showGame() {
  const request = ++reviewRequest;
  const game = state.games.find((entry) => entry.id === selected);
  const detail = $("detail");
  const refreshFocused = document.activeElement?.id === "refresh-saves";
  detail.replaceChildren();
  if (!game) {
    detail.append(element("h2", "Add your first game"),
      element("p", "Add a game with its title and, optionally, its install folder. SaveSync detects the save location automatically and can transfer the game to a Steam Deck."));
    if (!state.shared) detail.append(button("Connect a shared folder", openSettings, "primary"));
    return;
  }

  const heading = element("div", "", "panel-header");
  const tools = element("div", "", "actions");
  if (!game.detecting) {
    if (game.path) {
      const refreshButton = iconButton("↻", "Refresh saves", showGame);
      refreshButton.id = "refresh-saves";
      tools.append(refreshButton, iconButton("⚙︎", "Change save location", () => openMap(game)));
    }
    if (game.can_detect) tools.append(button("Detect active save location", () => runSaveDetection(game, "start")));
    if (game.path && state.cloud?.paired && state.platform === "Windows") {
      tools.append(button("Create sync shortcut", () => {
        const form = $("shortcut-form");
        form.dataset.game = game.id;
        form.reset();
        if (game.install) form.elements.exe.placeholder = game.install + "\\Game.exe";
        openDialog("shortcut-dialog");
      }));
    }
  }
  heading.append(element("h2", game.title), tools);
  detail.append(heading);
  if (refreshFocused) $("refresh-saves")?.focus({ preventScroll: true });
  if (game.detecting) {
    const status = element("div", "", "status-box");
    status.append(element("h3", "Waiting for a new save"),
      element("p", "Launch the game from the Steam shortcut you use, make a save, then close the game. Return here and check for the save. Sync for this game is paused during detection."));
    const actions = element("div", "", "actions");
    actions.append(button("Check for new save", () => runSaveDetection(game, "check"), "primary"),
      button("Cancel detection", () => runSaveDetection(game, "cancel")));
    detail.append(status, actions);
    return;
  }
  if (!game.path) {
    detail.append(element("p", "Choose the " + (game.mode === "file" ? "save file" : "save folder") + " for this game on this device."),
      button("Set save location", () => openMap(game), "primary"));
    return;
  }

  const checking = element("p", "Checking save files…");
  detail.append(checking);
  try {
    const review = await api("review", { id: game.id });
    if (request !== reviewRequest) return;
    checking.remove();
    renderReview(game, review, detail);
  } catch (error) {
    if (request !== reviewRequest) return;
    checking.textContent = error.message;
  }
}

function renderReview(game, review, detail) {
  if (review.cloud && review.status !== "interrupted") {
    renderCloudHistory(game, review, detail);
    return;
  }
  const [title, description] = statuses[review.status];
  const problem = ["conflict", "interrupted"].includes(review.status);
  const status = element("div", "", "status-box" + (problem ? " attention" : ""));
  status.append(element("h3", title), element("p", description));
  detail.append(status);

  const actions = element("div", "", "actions");
  if (review.status === "interrupted") {
    actions.append(button("Recover original files", () => runAction(game, "recover", {},
      "Close the game before recovering the original save files. Continue?"), "primary"));
  }
  if (["upload", "download"].includes(review.status)) {
    const label = review.status === "upload" ? "Upload saves" : "Download saves";
    actions.append(button(label, () => runAction(game, "sync", { token: review.token, choice: "auto" },
      review.status === "upload" ? "Close the game before uploading. Continue?"
        : "Close the game. Apply the shared version to the local save location?"), "primary"));
  }
  if (review.status === "current") {
    api("sync", { id: game.id, token: review.token, choice: "auto" }).catch((error) => notice(error.message, true));
  }
  if (review.status === "conflict" && review.count) {
    actions.append(button("Keep this device’s saves", () => runAction(game, "sync",
      { token: review.token, choice: "local" }, "Publish this device’s saves as the shared version?"), "primary"));
  }
  if (actions.childElementCount) detail.append(actions);

  if (review.versions.length) {
    const history = element("details", "", "history");
    const versions = element("div", "", "versions");
    appendDeviceHistory(versions, review.versions, review.status === "conflict", (version) => {
      const row = element("div", "", "version");
      const label = element("div");
      label.append(element("strong", version.device),
        element("small", new Date(version.created).toLocaleString() + " · " + version.count
          + (version.count === 1 ? " file" : " files") + (version.head ? " · Latest" : "")));
      row.append(label);
      if (!version.head || review.status === "conflict") {
        row.append(button("Use this version", () => runAction(game, "sync",
          { token: review.token, choice: version.id },
          "Close the game. Replace the local saves with this snapshot and make it the shared version?")));
      }
      return row;
    });
    history.append(element("summary", "Save history"), versions);
    detail.append(history);
  }
}

function appendDeviceHistory(container, versions, showHeads, makeRow) {
  const idsByName = new Map();
  for (const version of versions) {
    if (!version.device_id) continue;
    if (!idsByName.has(version.device)) idsByName.set(version.device, new Set());
    idsByName.get(version.device).add(version.device_id);
  }
  const groups = new Map();
  for (const version of versions) {
    const knownIds = idsByName.get(version.device);
    const id = version.device_id || (knownIds?.size === 1 ? [...knownIds][0] : null);
    const key = id ? "id:" + id : "name:" + version.device;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(version);
  }
  for (const entries of groups.values()) {
    const group = element("div", "", "device-history");
    const older = [];
    for (const [index, version] of entries.entries()) {
      if (index === 0 || (showHeads && version.head)) group.append(makeRow(version));
      else older.push(version);
    }
    if (older.length) {
      const more = element("details", "", "older-saves");
      const label = older.length === 1 ? "older save" : "older saves";
      more.append(element("summary", `${older.length} ${label} from ${entries[0].device}`));
      older.forEach((version) => more.append(makeRow(version)));
      group.append(more);
    }
    container.append(group);
  }
}

function renderCloudHistory(game, review, detail) {
  const cloud = review.cloud;
  const status = element("div", "", "status-box" + (cloud.error || cloud.conflict ? " attention" : ""));
  const label = cloud.error ? "Cloud unavailable" : cloud.conflict ? "Different save versions found"
    : cloud.current ? "Up to date with the cloud" : cloud.latest ? "Cloud save available" : "Ready for cloud saves";
  status.append(element("h3", label));
  if (cloud.error) status.append(element("p", cloud.error));
  if (cloud.conflict) status.append(element("p", "Open Save history to choose which progress to keep."));
  const actions = element("div", "", "actions");
  const choose = (choice, message) => runAction(game, "sync", {
    token: review.token, cloud_token: cloud.token, choice,
  }, message);
  const use = (id) => choose(id, "Close the game. Apply this save here and make it the active cloud version?");
  if (review.count && !cloud.error) {
    actions.append(button("Upload save", () => choose("local", "Close the game. Make this device’s save the active cloud version?"), "primary"));
  }
  if (cloud.other && !cloud.error) actions.append(button("Use latest from another device", () => use(cloud.other)));
  detail.append(status);
  if (actions.childElementCount) detail.append(actions);

  const history = element("details", "", "history");
  const versions = element("div", "", "versions");
  if (!review.versions.length) versions.append(element("p", "No snapshots yet."));
  appendDeviceHistory(versions, review.versions, cloud.conflict, (version) => {
    const row = element("div", "", "version");
    const description = element("div");
    const name = version.device + (version.this_device ? " · This device" : "");
    const location = version.uploaded ? (version.head ? "Cloud head" : "Cloud") : "Local snapshot";
    description.append(element("strong", name), element("small", new Date(version.created).toLocaleString()
      + " · " + location + " · " + version.count + (version.count === 1 ? " file" : " files")));
    row.append(description);
    if (!cloud.error) row.append(button("Use this save", () => use(version.id)));
    return row;
  });
  history.append(element("summary", "Save history"), versions);
  detail.append(history);
}

function confirmAction(message) {
  const dialog = $("confirm-dialog");
  $("confirm-message").textContent = message;
  $("confirm-button").onclick = () => dialog.close("continue");
  dialog.returnValue = "";
  dialog.showModal();
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "continue"), { once: true });
  });
}

async function runAction(game, action, data, confirmation) {
  if (busy) return;
  busy = true;
  if (!await confirmAction(confirmation)) {
    busy = false;
    return;
  }
  const clickedButtons = [...$("detail").querySelectorAll("button")];
  clickedButtons.forEach((node) => { node.disabled = true; });
  try {
    const result = await api(action, { id: game.id, ...data });
    notice(result.message);
    await showGame();
  } catch (error) {
    notice(error.message, true);
    await showGame();
  } finally {
    clickedButtons.forEach((node) => { node.disabled = false; });
    busy = false;
  }
}

document.querySelectorAll("[data-close]").forEach((node) => {
  node.addEventListener("click", () => node.closest("dialog").close());
});
$("settings-button").addEventListener("click", openSettings);
bindForm("shortcut-form", async (data) => {
  const result = await api("shortcut", { ...data, id: $("shortcut-form").dataset.game });
  $("shortcut-dialog").close();
  notice(result.message);
});
$("shortcut-browse").addEventListener("click", async () => {
  const form = $("shortcut-form");
  const browse = $("shortcut-browse");
  const game = form.dataset.game;
  browse.disabled = true;
  form.querySelector('[type="submit"]').disabled = true;
  formError(form, "");
  try {
    const result = await api("browse-executable", { id: game });
    if (result.path && form.dataset.game === game) form.elements.exe.value = result.path;
  } catch (error) {
    formError(form, error.message);
  } finally {
    browse.disabled = false;
    form.querySelector('[type="submit"]').disabled = false;
  }
});
$("cloud-settings-button").addEventListener("click", openCloudSettings);
$("cloud-show-code").addEventListener("click", () => {
  const input = $("cloud-form").elements.code;
  const reveal = input.type === "password";
  input.type = reveal ? "text" : "password";
  $("cloud-show-code").setAttribute("aria-pressed", String(reveal));
});
bindForm("cloud-form", async (data) => {
  if (state.cloud?.paired) return;
  const result = await api("cloud", { action: "pair", ...data });
  $("cloud-form").elements.code.value = "";
  $("cloud-dialog").close();
  notice(result.message);
  await refresh();
});
$("cloud-invite").addEventListener("click", async () => {
  const form = $("cloud-form");
  formError(form, "");
  try {
    const invite = await api("cloud", { action: "invite" });
    $("cloud-invite-field").hidden = false;
    $("cloud-invite-code").value = invite.code;
    $("cloud-invite-code").select();
  } catch (error) {
    formError(form, error.message);
  }
});
$("cloud-disconnect").addEventListener("click", async () => {
  if (!window.confirm("Disconnect this device from the cloud library?")) return;
  try {
    const result = await api("cloud", { action: "disconnect" });
    $("cloud-dialog").close();
    notice(result.message);
    await refresh();
  } catch (error) {
    formError($("cloud-form"), error.message);
  }
});
$("exit-button").hidden = document.querySelector('meta[name="savesync-window"]').content !== "true";
$("exit-button").addEventListener("click", async () => {
  if (busy) return;
  $("exit-button").disabled = true;
  try {
    await api("exit");
  } catch (error) {
    $("exit-button").disabled = false;
    notice(error.message, true);
  }
});
$("add-button").addEventListener("click", () => openMap());
$("refresh-button").addEventListener("click", () => {
  if (busy) return;
  notice("");
  refresh().catch((error) => notice(error.message, true));
});
bindForm("settings-form", async (data) => {
  await api("settings", data);
  $("settings-dialog").close();
  notice("Settings saved.");
  await refresh();
});

function discoveryCandidates(results, mode) {
  const folders = new Map();
  const files = new Map();
  for (const result of results) {
    const source = { title: result.title, suggested: Boolean(result.suggested) };
    for (const entry of result.files) {
      if (mode !== "file" && !folders.has(entry.folder)) folders.set(entry.folder, source);
      if (mode !== "directory" && !files.has(entry.path)) files.set(entry.path, source);
    }
  }
  return [...folders].map(([path, source]) => ({ ...source, path, kind: "directory" }))
    .concat([...files].map(([path, source]) => ({ ...source, path, kind: "file" })));
}

function renderMapCandidates(candidates) {
  const box = $("map-candidates");
  box.replaceChildren();
  const singleTitle = candidates.every((candidate) => candidate.title === candidates[0].title);
  for (const candidate of candidates) {
    const row = element("div", "", "scan-file");
    row.append(element("div", (singleTitle ? "" : candidate.title + " · ") + candidate.path, "location"));
    const use = () => {
      const form = $("map-form");
      const fields = form.elements;
      fields.path.value = candidate.path;
      fields.mode.value = candidate.kind;
      box.replaceChildren();
      updateMapSubmit(form);
    };
    row.append(button(candidate.kind === "file" ? "Use this file" : "Use this folder", use));
    box.append(row);
  }
}

async function detectSaveLocation(form, data, id, isCurrent) {
  let scan;
  try {
    scan = await api("discover", {
      title: data.title,
      install: data.install || "",
      prefix: data.prefix || "",
      id,
    });
  } catch (error) {
    if (!isCurrent()) return;
    const box = $("map-candidates");
    box.replaceChildren(element("p", error.message));
    if (!state.ludusavi_ready) {
      box.append(ludusaviInstallButton(() => {
        if (!isCurrent()) return;
        box.replaceChildren(element("p", "Ludusavi installed. Detecting the save location…"));
        form.requestSubmit();
      }));
    }
    return;
  }
  if (!isCurrent()) return;
  const candidates = discoveryCandidates(scan.results, data.mode);
  const exact = candidates.length === 1
    && !candidates[0].suggested
    && candidates[0].title.toLowerCase() === data.title.trim().toLowerCase();
  if (exact) {
    data.path = candidates[0].path;
    data.mode = candidates[0].kind;
    return;
  }
  renderMapCandidates(candidates.slice(0, 30));
  formError(form, candidates.some((candidate) => candidate.suggested)
    ? "Found possible saves in the Proton prefix. Choose the location that belongs to this game."
    : candidates.length ? "Choose one of the detected save locations, or enter the path manually."
      : "No saves found. Try the full store title or enter a save location.");
}

async function saveMapLocation(data) {
  const form = $("map-form");
  const dialog = $("map-dialog");
  const id = form.dataset.game;
  const session = mapSession;
  const isCurrent = () => dialog.open && session === mapSession;
  if (!data.path.trim() && !form.elements.path.required) {
    await detectSaveLocation(form, data, id, isCurrent);
    if (!isCurrent() || !data.path.trim()) return;
  }
  const result = await api("map", { ...data, id });
  if (isCurrent()) dialog.close();
  notice(id ? "Save location configured." : "Game added.");
  await refresh(result.id);
}

function updateMapSubmit(form) {
  const submit = form.querySelector('[type="submit"]');
  submit.textContent = !form.dataset.game ? "Add game"
    : form.elements.path.value.trim() ? "Save location" : "Detect saves";
}

$("map-form").elements.path.addEventListener("input", () => updateMapSubmit($("map-form")));
$("map-rename").addEventListener("click", async (event) => {
  const control = event.currentTarget;
  const form = $("map-form");
  if (!form.elements.title.reportValidity()) return;
  control.disabled = true;
  formError(form, "");
  const session = mapSession;
  try {
    const { id } = await api("rename-game", { id: form.dataset.game, title: form.elements.title.value });
    notice("Game title updated on this device.");
    await refresh(id);
  } catch (error) {
    if (session === mapSession) formError(form, error.message);
  } finally {
    if (session === mapSession) control.disabled = false;
  }
});
bindForm("map-form", (data) => saveMapLocation({ ...data }));
refresh().catch((error) => notice(error.message, true));
