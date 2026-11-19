"use strict";

const $ = (id) => document.getElementById(id);
const session = document.querySelector('meta[name="savesync-token"]').content;
let state;
let selected;
let scanTarget;
let reviewRequest = 0;
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
    item.setAttribute("aria-current", String(game.id === selected));
    item.append(element("span", game.path ? "Mapped on this device" : "Set up on this device"));
    list.append(item);
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

function openMap(game, path = game?.path || "", mode = game?.mode || "auto", title = game?.title || "") {
  const form = $("map-form");
  form.dataset.game = game?.id || "";
  const fields = form.elements;
  fields.title.value = title;
  fields.install.value = game?.install || "";
  fields.install.closest("label").hidden = false;
  fields.path.value = path;
  fields.mode.value = mode;
  fields.title.readOnly = Boolean(game);
  $("map-candidates").replaceChildren();
  $("map-heading").textContent = game ? "Set local save location" : "Add game";
  form.querySelector('[type="submit"]').textContent = game ? "Save location" : "Add game";
  openDialog("map-dialog");
}

function openDiscovery(game) {
  scanTarget = game;
  const fields = $("discover-form").elements;
  fields.title.value = game?.title || "";
  fields.title.readOnly = Boolean(game);
  fields.install.value = game?.install || "";
  fields.prefix.value = "";
  $("scan-results").replaceChildren();
  openDialog("discover-dialog");
  if (game) $("discover-form").requestSubmit();
}

const statuses = {
  empty: ["No saves found", "Make a save in the game, then check again."],
  upload: ["Ready to upload", "Publish this device’s save files to the shared folder."],
  download: ["Ready to download", "Apply the shared version to this device."],
  current: ["Up to date", "This device has the same saves as the shared version."],
  conflict: ["Different save versions found", "Choose the progress you want to continue. Shared versions stay in history, and overwritten local files get a recovery copy."],
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
    tools.append(button(game.path ? "Change location" : "Enter path", () => openMap(game)));
    if (game.path) tools.append(button("Find local saves", () => openDiscovery(game)));
    if (game.can_detect) tools.append(button("Detect active save location", () => runSaveDetection(game, "start")));
    if (game.install && state.platform === "Windows") tools.append(button("Send to Steam Deck", () => openTransfer(game)));
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
      button("Find local saves", () => openDiscovery(game), "primary"));
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
    detail.append(button("Check again", showGame));
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
  actions.append(button("Check again", showGame));
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
  detail.append(actions);

  if (review.versions.length) {
    const history = element("details", "", "history");
    history.open = review.status === "conflict";
    const versions = element("div", "", "versions");
    for (const version of review.versions) {
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
      versions.append(row);
    }
    history.append(element("summary", "Version history"), versions);
    detail.append(history);
  }
}

function renderCloudHistory(game, review, detail) {
  const cloud = review.cloud;
  const status = element("div", "", "status-box" + (cloud.error || cloud.conflict ? " attention" : ""));
  const label = cloud.error ? "Cloud unavailable" : cloud.conflict ? "Different save versions found"
    : cloud.current ? "Up to date with the cloud" : cloud.latest ? "Cloud save available" : "Ready for cloud saves";
  status.append(element("h3", label));
  if (cloud.error) status.append(element("p", cloud.error));
  if (cloud.conflict) status.append(element("p", "Choose which progress to keep. The other versions stay in history."));
  const actions = element("div", "", "actions");
  const choose = (choice, message) => runAction(game, "sync", {
    token: review.token, cloud_token: cloud.token, choice,
  }, message);
  const use = (id) => choose(id, "Close the game. Apply this save here and make it the active cloud version?");
  if (review.count && !cloud.error) {
    actions.append(button("Upload save", () => choose("local", "Close the game. Make this device’s save the active cloud version?"), "primary"));
  }
  if (cloud.other && !cloud.error) actions.append(button("Use latest from another device", () => use(cloud.other)));
  actions.append(button("Check again", showGame));
  detail.append(status, actions);

  const history = element("section", "", "versions");
  history.append(element("h3", "Save history"));
  if (!review.versions.length) history.append(element("p", "No snapshots yet."));
  for (const version of review.versions) {
    const row = element("div", "", "version");
    const description = element("div");
    const name = version.device + (version.this_device ? " · This device" : "");
    const location = version.uploaded ? (version.head ? "Cloud head" : "Cloud") : "Local snapshot";
    description.append(element("strong", name), element("small", new Date(version.created).toLocaleString()
      + " · " + location + " · " + version.count + (version.count === 1 ? " file" : " files")));
    row.append(description);
    if (!cloud.error) row.append(button("Use this save", () => use(version.id)));
    history.append(row);
  }
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

function showScan(results) {
  const container = $("scan-results");
  container.replaceChildren();
  if (!results.length) {
    container.append(element("p", "No existing save files found. Make a save in the game, close it, then try again. You can also enter the save path manually."));
  } else if (scanTarget && state.platform === "Linux") {
    container.append(element("p", "If more than one folder appears, make a new save from the Steam shortcut you use, then scan again and choose the folder containing that save."));
  }
  for (const result of results) {
    const card = element("section", "", "scan-game");
    card.append(element("h3", result.title));
    const titleMatches = state.games.filter((game) => game.title === result.title);
    const existing = scanTarget || (titleMatches.length === 1 ? titleMatches[0] : null);
    const folders = new Map();
    for (const { path, folder, modified } of result.files) {
      const location = folders.get(folder) || { files: [], modified: null };
      location.files.push(path);
      if (modified != null) location.modified = Math.max(location.modified ?? modified, modified);
      folders.set(folder, location);
    }

    const pick = (path, mode) => {
      $("discover-dialog").close();
      openMap(existing, path, mode, result.title);
    };
    for (const [folder, { files, modified }] of folders) {
      const group = element("div", "", "scan-file");
      group.append(element("div", folder, "location"));
      if (modified != null) {
        group.append(element("p", "Most recent file change · " + new Date(modified).toLocaleString(), "hint"));
      }
      if (!existing || existing.mode === "directory") {
        group.append(button("Use this folder", () => pick(folder, "directory")));
      }
      if (!existing || existing.mode === "file") {
        const choices = element("details");
        choices.append(element("summary", "Choose a single file"));
        for (const file of files) {
          const option = element("div", "", "scan-file");
          option.append(element("div", file, "location"), button("Use this file", () => pick(file, "file")));
          choices.append(option);
        }
        group.append(choices);
      }
      card.append(group);
    }
    container.append(card);
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
$("discover-button").addEventListener("click", () => openDiscovery());
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
    for (const entry of result.files) {
      if (mode !== "file" && !folders.has(entry.folder)) folders.set(entry.folder, result.title);
      if (mode !== "directory" && !files.has(entry.path)) files.set(entry.path, result.title);
    }
  }
  return [...folders].map(([path, title]) => ({ title, path, kind: "directory" }))
    .concat([...files].map(([path, title]) => ({ title, path, kind: "file" })));
}

function renderMapCandidates(candidates) {
  const box = $("map-candidates");
  box.replaceChildren();
  const singleTitle = candidates.every((candidate) => candidate.title === candidates[0].title);
  for (const candidate of candidates) {
    const row = element("div", "", "scan-file");
    row.append(element("div", (singleTitle ? "" : candidate.title + " · ") + candidate.path, "location"));
    const use = () => {
      const fields = $("map-form").elements;
      fields.path.value = candidate.path;
      fields.mode.value = candidate.kind;
      box.replaceChildren();
    };
    row.append(button(candidate.kind === "file" ? "Use this file" : "Use this folder", use));
    box.append(row);
  }
}

async function detectSaveLocation(form, data) {
  let scan;
  const current = state.games.find((entry) => entry.id === form.dataset.game);
  try {
    scan = await api("discover", {
      title: data.title,
      install: data.install || current?.install || "",
      id: current?.id || "",
    });
  } catch (error) {
    const box = $("map-candidates");
    box.replaceChildren(element("p", error.message));
    if (!state.ludusavi_ready) {
      box.append(ludusaviInstallButton(async () => {
        box.replaceChildren(element("p", "Ludusavi installed. Detecting the save location…"));
        await detectSaveLocation(form, data);
        if (data.path.trim()) await saveMapLocation(data);
      }));
    }
    return;
  }
  const candidates = discoveryCandidates(scan.results, data.mode);
  const exact = candidates.length === 1
    && candidates[0].title.toLowerCase() === data.title.trim().toLowerCase();
  if (exact) {
    data.path = candidates[0].path;
    data.mode = candidates[0].kind;
    return;
  }
  renderMapCandidates(candidates.slice(0, 30));
  formError(form, candidates.length
    ? "Choose one of the detected save locations, or enter the path manually."
    : "No existing saves were detected for this title. Enter the save location manually.");
}

async function saveMapLocation(data) {
  const form = $("map-form");
  if (!data.path.trim()) {
    await detectSaveLocation(form, data);
    if (!data.path.trim()) {
      formError(form, "A save location is needed. Pick a detected one, enter the path, or cancel.");
      return;
    }
  }
  const result = await api("map", { ...data, id: form.dataset.game });
  $("map-dialog").close();
  notice(form.dataset.game ? "Save location configured." : "Game added.");
  await refresh(result.id);
}

bindForm("map-form", (data) => saveMapLocation({ ...data }));
bindForm("discover-form", async (data) => {
  $("scan-results").replaceChildren(element("p", "Looking for save files…"));
  try {
    const result = await api("discover", { ...data, id: scanTarget?.id || "" });
    showScan(result.results);
  } catch (error) {
    $("scan-results").replaceChildren();
    if (!state.ludusavi_ready) {
      $("scan-results").append(element("p", error.message));
      $("scan-results").append(ludusaviInstallButton(() => $("discover-form").requestSubmit()));
      return;
    }
    throw error;
  }
});
refresh().catch((error) => notice(error.message, true));
