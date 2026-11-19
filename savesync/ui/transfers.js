"use strict";

const transferDialog = $("transfer-dialog");
const transferCall = (action, data) => api("transfer/" + action, data);
let transferTimer;
let transferCatalog = [];
let transferInvite = "";
let transferInFlight = false;

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
      transferInvite = data.invite;
      transferCatalog = result.games;
      renderCatalog();
    } else if (action === "scan") {
      replaceOptions($("transfer-exes"), result.executables.map((path) => [path, path]));
      if (result.executables.length === 1) $("share-form").elements.exe.value = result.executables[0];
      transferMessage(result.executables.length + " executables found.");
    } else {
      renderTransfer(result);
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
        error: "Steam shortcut needs attention in the SaveSync Decky panel",
      };
      label.append(element("small", shortcuts[game.shortcut] || "Steam setup pending"));
    }
    if (game.save_message) label.append(element("small", game.save_message));
    row.append(label, button("Retry setup", () => transferAction("finish", { id: game.id })));
    container.append(row);
  }
}

function renderTransfer(transfer) {
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
    }
  }

  if (transfer.role === "host") {
    $("transfer-start-form").hidden = Boolean(transfer.invite);
    $("transfer-offer").hidden = !transfer.invite;
    if (transfer.invite) {
      $("transfer-code").value = transfer.invite;
      const list = $("transfer-offers");
      list.replaceChildren();
      for (const game of transfer.games) {
        const row = element("div", "", "version");
        row.append(element("strong", game.title),
          button("Stop sharing", () => transferAction("remove", { id: game.id })));
        list.append(row);
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

$("transfer-button").addEventListener("click", () => {
  transferMessage("");
  if (state) {
    const entries = state.games.filter((game) => game.path).map((game) => [game.id, game.title]);
    replaceOptions($("transfer-save"), [["", "None"], ...entries]);
  }
  transferDialog.showModal();
  tickTransfers();
  transferTimer = setInterval(tickTransfers, 1000);
});

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
transferForm("transfer-connect-form", "connect");
$("transfer-stop").addEventListener("click", () => transferAction("stop"));
$("transfer-scan").addEventListener("click", () => transferAction("scan", {
  folder: $("share-form").elements.folder.value,
}));
$("transfer-copy").addEventListener("click", async () => {
  $("transfer-code").select();
  try {
    await navigator.clipboard.writeText($("transfer-code").value);
    transferMessage("Connection code copied.");
  } catch {
    transferMessage("Copy the selected connection code.");
  }
});
transferCall("status").then((transfer) => {
  $("transfer-button").hidden = !transfer.role;
}).catch((error) => notice(error.message, true));
