"use strict";

const transferDialog = $("transfer-dialog");
const transferCall = (action, data) => api("transfer/" + action, data);
let transferTimer;
let transferCatalog = [];
let transferInvite = "";
let transferHosts = [];
let transferInFlight = false;
const exeChoices = {};
const exeSelected = {};
const exeLoading = {};

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  const unit = Math.min(4, Math.floor(Math.log(bytes) / Math.log(1024)));
  return (bytes / 1024 ** unit).toLocaleString(undefined, { maximumFractionDigits: 1 }) + " " + units[unit];
}

function transferMessage(text, isError = false) {
  const node = $("transfer-message");
  node.textContent = text;
  node.classList.toggle("form-error", isError);
  node.hidden = !text;
}

async function transferAction(action, data = {}) {
  if (transferInFlight) return;
  transferInFlight = true;
  transferMessage("");
  try {
    const result = await transferCall(action, data);
    if (action === "connect") {
      transferInvite = result.invite || data.invite || "";
      transferCatalog = result.games;
      renderCatalog();
      transferMessage(result.host ? "Connected to " + result.host + "." : "Connected to PC.");
    } else if (action === "scan") {
      replaceOptions($("transfer-exes"), result.executables.map((path) => [path, path]));
      if (result.executables.length === 1) $("share-form").elements.exe.value = result.executables[0];
      transferMessage(result.executables.length + " executables found.");
    } else if (action === "exes") {
      exeChoices[data.id] = result.executables;
      renderTransfer(await transferCall("status"));
      if (!result.executables.length) {
        transferMessage("No Windows executables were found in the downloaded game folder.", true);
      }
    } else {
      renderTransfer(result);
      if (action === "finish" && data.exe) {
        const updated = (result.installs || []).find((item) => item.id === data.id);
        transferMessage(updated?.shortcut === "added_direct"
          ? "Added to Steam! Restart Steam or switch to Gaming Mode if it doesn’t appear right away."
          : "Executable chosen. The Steam shortcut is queued.");
      }
    }
  } catch (error) {
    transferMessage(error.message, true);
  } finally {
    transferInFlight = false;
  }
}

function replaceOptions(select, options) {
  const before = select.value;
  select.replaceChildren(...options.map(([value, label]) => {
    const option = element("option", label);
    option.value = value;
    return option;
  }));
  if (options.some(([value]) => value === before)) select.value = before;
}

function renderCatalog() {
  const container = $("transfer-catalog");
  container.replaceChildren();
  if (!transferCatalog.length) container.append(element("p", "The PC has no shared games yet."));
  for (const game of transferCatalog) {
    const row = element("div", "", "version");
    const label = element("div");
    label.append(element("strong", game.title), element("small", formatBytes(game.bytes)));
    row.append(label, button("Download", () => transferAction("download", {
      id: game.id, invite: transferInvite, folder: $("install-folder").value, steam: $("add-to-steam").checked,
    }), "primary"));
    container.append(row);
  }
}

function loadExeChoices(gameId) {
  if (exeChoices[gameId] || exeLoading[gameId]) return;
  exeLoading[gameId] = true;
  transferCall("exes", { id: gameId }).then((result) => {
    exeChoices[gameId] = result.executables || [];
    if (transferStatus && transferStatus.role === "deck") {
      renderInstalls(transferStatus.installs || []);
    }
  }).catch(() => {
    // User can still click Choose executable manually.
  }).finally(() => {
    exeLoading[gameId] = false;
  });
}

function renderInstalls(installs) {
  const container = $("transfer-installs");
  container.replaceChildren();
  if (!installs.length) return;
  container.append(element("h3", "Downloaded games"));
  for (const game of installs) {
    const row = element("div", "", "version");
    const label = element("div");
    label.append(element("strong", game.title), element("small", game.target));
    if (game.steam) {
      const shortcuts = {
        pending: "Waiting for the SaveSync Decky bridge",
        adding: "Adding to Steam",
        added: "Added to Steam",
        added_direct: "Added to Steam (restart Steam or switch to Gaming Mode if not visible yet)",
        error: "Steam shortcut needs attention in the SaveSync Decky panel",
      };
      if (game.needs_exe) {
        label.append(element("small", "Choose which .exe to launch in Steam:"));
        loadExeChoices(game.id);
      } else {
        label.append(element("small", shortcuts[game.shortcut] || "Steam setup pending"));
      }
    }
    if (game.save_message) label.append(element("small", game.save_message));
    row.append(label);

    const controls = element("div", "", "actions");
    if (game.steam && !game.shortcut) {
      const choices = exeChoices[game.id] || [];
      if (choices.length) {
        const select = document.createElement("select");
        for (const exe of choices) {
          const option = element("option", exe);
          option.value = exe;
          select.append(option);
        }
        select.value = choices.includes(exeSelected[game.id]) ? exeSelected[game.id] : choices[0];
        select.addEventListener("change", () => { exeSelected[game.id] = select.value; });
        controls.append(select);
        controls.append(button("Add to Steam", () => transferAction("finish", {
          id: game.id, exe: select.value,
        }), "primary"));
      } else if (exeLoading[game.id]) {
        controls.append(element("small", "Finding executables…"));
      } else {
        controls.append(button(game.exe ? "Change executable" : "Choose executable",
          () => transferAction("exes", { id: game.id })));
      }
    }
    controls.append(button("Retry setup", () => transferAction("finish", { id: game.id })));
    row.append(controls);
    container.append(row);
  }
}

function renderTransfer(transfer) {
  transferStatus = transfer;
  $("transfer-host").hidden = transfer.role !== "host";
  $("transfer-deck").hidden = transfer.role !== "deck";
  const job = transfer.job;
  const progress = $("transfer-progress");
  progress.hidden = !job.running;
  if (job.running) {
    $("transfer-job-label").textContent = job.label + (job.total ? " · " + formatBytes(job.done) + " / " + formatBytes(job.total) : "…");
    const meter = progress.querySelector("progress");
    meter.removeAttribute("value");
    if (job.total) {
      meter.max = job.total;
      meter.value = job.done;
    }
  } else if (job.error || job.message) {
    const key = job.label + (job.error || job.message) + (job.installed || "");
    if (progress.dataset.result !== key) {
      progress.dataset.result = key;
      transferMessage(job.error || job.message, Boolean(job.error));
      if (job.installed && state) {
        refresh().catch(() => {});
      }
    }
  }

  if (transfer.role === "host") {
    $("transfer-start-form").hidden = Boolean(transfer.code);
    $("transfer-offer").hidden = !transfer.code;
    if (transfer.code) {
      $("transfer-code").textContent = transfer.code;
      const endpoint = transfer.port && transfer.port !== 47835 ? transfer.address + ":" + transfer.port : transfer.address;
      $("transfer-host-address").textContent = endpoint ? "PC address: " + endpoint : "";
      $("transfer-locked").hidden = !transfer.locked;
      const list = $("transfer-offers");
      list.replaceChildren();
      if (transfer.games.length) {
        list.append(element("h3", "Shared games"));
        for (const game of transfer.games) {
          const row = element("div", "", "version");
          row.append(element("strong", game.title),
            button("Stop sharing", () => transferAction("remove", { id: game.id })));
          list.append(row);
        }
      } else if (!job.running) {
        list.append(element("p", "No games are shared yet. Add a game’s install folder in the Game library, or share a folder below.", "hint"));
      }
    } else {
      const address = $("transfer-address");
      replaceOptions($("transfer-addresses"), transfer.addresses.map((ip) => [ip, ip]));
      if (!address.value && transfer.addresses.length) address.value = transfer.addresses[0];
    }
    $("transfer-stop").disabled = Boolean(job.running);
    $("share-form").querySelector('[type="submit"]').disabled = Boolean(job.running);
  } else if (transfer.role === "deck") {
    if (!$("install-folder").value) $("install-folder").value = transfer.default_parent;
    renderInstalls(transfer.installs);
    for (const control of $("transfer-catalog").querySelectorAll("button")) control.disabled = Boolean(job.running);
  }
}

function renderDiscoveredHosts() {
  const status = $("transfer-discovered");
  const pickerLabel = $("transfer-host-picker-label");
  const picker = $("transfer-host-picker");
  if (!transferHosts.length) {
    status.textContent = "No sharing PC found yet. Start sharing on your PC, then select Find PC or enter its IP below.";
    pickerLabel.hidden = true;
    picker.replaceChildren();
    return;
  }
  if (transferHosts.length === 1) {
    const host = transferHosts[0];
    status.textContent = "Found PC: " + host.name + " (" + host.address + ")";
    pickerLabel.hidden = true;
    replaceOptions(picker, [[host.address + ":" + host.port, host.name + " (" + host.address + ")"]]);
    return;
  }
  status.textContent = transferHosts.length + " sharing PCs found on this network.";
  pickerLabel.hidden = false;
  replaceOptions(picker, transferHosts.map((host) => [
    host.address + ":" + host.port,
    host.name + " (" + host.address + ")",
  ]));
}

async function discoverTransferHosts() {
  if (!transferStatus || transferStatus.role !== "deck") return;
  $("transfer-discovered").textContent = "Searching for a sharing PC on this network…";
  $("transfer-discover").disabled = true;
  try {
    const result = await transferCall("discover", {});
    transferHosts = result.hosts || [];
    renderDiscoveredHosts();
  } catch (error) {
    $("transfer-discovered").textContent = "Could not scan network. Enter the PC IP address manually below.";
  } finally {
    $("transfer-discover").disabled = false;
  }
}

async function tickTransfers() {
  if (!transferDialog.open || transferInFlight) return;
  transferInFlight = true;
  try {
    renderTransfer(await transferCall("status"));
  } catch (error) {
    transferMessage(error.message, true);
  } finally {
    transferInFlight = false;
  }
}

function openTransfer(game = null) {
  transferMessage("");
  if (state) {
    const entries = state.games.filter((item) => item.path).map((item) => [item.id, item.title]);
    replaceOptions($("transfer-save"), [["", "None"], ...entries]);
    if (game) {
      const share = $("share-form").elements;
      share.title.value = game.title || "";
      share.folder.value = game.install || "";
      if (game.path) $("transfer-save").value = game.id;
    }
  }
  transferDialog.showModal();
  tickTransfers().then(() => {
    if (!transferStatus) return;
    if (transferStatus.role === "deck") {
      discoverTransferHosts();
    } else if (transferStatus.role === "host" && transferStatus.code && game && game.install
      && !transferStatus.job?.running && !transferStatus.games.some((item) => item.title === game.title)) {
      transferAction("share_library", { id: game.id });
    }
  });
  transferTimer = setInterval(tickTransfers, 1000);
}

$("transfer-button").addEventListener("click", () => openTransfer());

transferDialog.addEventListener("close", () => {
  clearInterval(transferTimer);
  if (state) refresh().catch((error) => notice(error.message, true));
});

function transferForm(id, action) {
  $(id).addEventListener("submit", (event) => {
    event.preventDefault();
    transferAction(action, Object.fromEntries(new FormData(event.currentTarget)));
  });
}

transferForm("transfer-start-form", "start");
transferForm("share-form", "share");
$("transfer-connect-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const code = $("transfer-code-input").value.trim();
  let address = $("transfer-manual-address").value.trim();
  let port = "";
  if (!address && transferHosts.length) {
    const chosen = $("transfer-host-picker").value || (transferHosts[0].address + ":" + transferHosts[0].port);
    const parts = chosen.split(":");
    address = parts[0] || "";
    port = parts[1] || "";
  }
  transferAction("connect", { code, address, port });
});
$("transfer-discover").addEventListener("click", () => discoverTransferHosts());
$("transfer-stop").addEventListener("click", () => transferAction("stop"));
$("transfer-scan").addEventListener("click", () => transferAction("scan", {
  folder: $("share-form").elements.folder.value,
}));
$("transfer-copy").addEventListener("click", async () => {
  const code = $("transfer-code").textContent.trim();
  try {
    await navigator.clipboard.writeText(code);
    transferMessage("Pairing code copied.");
  } catch {
    transferMessage("Pairing code: " + code);
  }
});
transferCall("status").then((transfer) => {
  transferStatus = transfer;
  $("transfer-button").hidden = !transfer.role;
}).catch((error) => notice(error.message, true));
