import { callable, definePlugin, toaster } from "@decky/api";
import { ButtonItem, PanelSection, PanelSectionRow, staticClasses } from "@decky/ui";
import React, { useEffect, useState } from "react";

type Job = {
  id: string;
  title: string;
  exe: string;
  start: string;
  options: string;
  status: string;
  appid: number;
  error: string;
};

interface Details {
  strShortcutExe?: string;
  strShortcutStartDir?: string;
  strShortcutLaunchOptions?: string;
  strLaunchOptions?: string;
}

interface SteamWindow {
  SteamClient: {
    GameSessions?: {
      RegisterForAppLifetimeNotifications(callback: (event: { bRunning: boolean; unAppID: number }) => void): { unregister(): void };
    };
    Apps: {
      AddShortcut(name: string, exe: string, start: string, options: string): Promise<number>;
      SetShortcutExe(id: number, exe: string): void;
      SetShortcutStartDir(id: number, path: string): void;
      SetAppLaunchOptions(id: number, options: string): void;
      SpecifyCompatTool(id: number, tool: string): void;
      RegisterForAppDetails(id: number, callback: (details?: Details) => void): { unregister(): void };
    };
  };
  SteamUIStore?: { RunningApps?: { appid: number; local_per_client_data?: { display_status?: number } }[] };
  appStore?: { GetAppOverviewByAppID(id: number): unknown };
}

const steam = window as unknown as SteamWindow;
const pending = callable<[], Job[]>("jobs");
type Binding = { id: string; auto?: boolean; download?: boolean; pending?: boolean };
type SyncState = { bindings: Record<string, Binding>; games: Record<string, string> };
type SaveResult = { latest?: { device: string; created: string }; pending?: boolean; error?: string; message?: string };
const syncState = callable<[], SyncState>("game_sync_state");
const syncConfig = callable<[number, string, boolean, boolean], void>("game_sync_config");
const syncAction = callable<[string, string], SaveResult>("game_sync_action");
const started = new Set<number>();
const stopped = new Set<number>();
let syncWork: Promise<unknown> = Promise.resolve();

function runningApps(requireSteam = false) {
  let visible: number[] = [];
  try {
    const readyToLaunch = 11; // Steam can retain an exited app in RunningApps.
    const apps = steam.SteamUIStore?.RunningApps;
    if (requireSteam && !Array.isArray(apps)) throw new Error("Steam's running games are unavailable.");
    visible = (apps || [])
      .filter((app) => app.local_per_client_data?.display_status !== readyToLaunch)
      .map(({ appid }) => appid >>> 0);
  } catch (error) {
    if (requireSteam) throw error;
    // Lifecycle events remain available when Steam is rebuilding its UI.
  }
  return new Set([...visible.filter((appid) => appid && !stopped.has(appid)), ...started]);
}

function canDownloadNow() {
  try {
    return runningApps(true).size === 0;
  } catch {
    return false;
  }
}

function serializeSync<T>(work: () => Promise<T>): Promise<T> {
  const request = syncWork.then(work);
  syncWork = request.catch((error) => console.error("SaveSync", error));
  return request;
}

async function afterExit(appid: number, retry = false) {
  const state = await syncState();
  const game = state.bindings[appid];
  if (!game || [...runningApps()].some((id) => state.bindings[id]?.id === game.id)) return;
  const action = retry ? (game.pending ? "retry" : "") : game.download ? "download" : game.auto ? "upload" : "";
  if (!action) return;
  const result = await syncAction(game.id, action);
  if (!result.error && action === "download") await syncConfig(appid, game.id, Boolean(game.auto), false);
  if (!retry && (result.error || result.message)) {
    toaster.toast({ title: state.games[game.id], body: result.error || result.message || "" });
  }
}
const update = callable<[key: string, status: string, appid: number, error: string], void>("update");
const delay = (milliseconds: number) => new Promise<void>((resolve) => setTimeout(resolve, milliseconds));
let processing = false;
let stopping = false;

function readDetails(appid: number): Promise<Details | undefined> {
  return new Promise((resolve) => {
    const registration = steam.SteamClient.Apps.RegisterForAppDetails(appid, (details) => {
      if (details?.strShortcutExe) {
        queueMicrotask(() => {
          clearTimeout(timeout);
          registration.unregister();
        });
        resolve(details);
      }
    });
    const timeout = setTimeout(() => {
      registration.unregister();
      resolve(undefined);
    }, 1600);
  });
}

const stripQuotes = (value = "") => value.replace(/^"(.*)"$/, "$1");

async function registerGame(job: Job) {
  const apps = steam.SteamClient.Apps;
  let appid = job.appid;
  try {
    if (appid && !steam.appStore?.GetAppOverviewByAppID(appid)) {
      throw new Error("The previous shortcut is unavailable. Check the Steam library.");
    }
    if (!appid) {
      for (const executable of [job.exe, `"${job.exe}"`]) {
        const result = await apps.AddShortcut(job.title, executable, "", "");
        if (Number.isInteger(result) && result) {
          appid = result >>> 0;
          break;
        }
      }
      if (!appid) throw new Error("Steam could not create the shortcut.");
      await update(job.id, "adding", appid, "");
      for (let attempt = 0; attempt < 25 && !steam.appStore?.GetAppOverviewByAppID(appid); attempt++) {
        await delay(150);
      }
    }

    apps.SetShortcutStartDir(appid, job.start);
    apps.SetAppLaunchOptions(appid, job.options);
    // This Steam-internal name selects Valve's Proton Experimental.
    apps.SpecifyCompatTool(appid, "proton_experimental");
    for (let attempt = 0; attempt < 3; attempt++) {
      await delay(350);
      const details = await readDetails(appid);
      if (!details || stripQuotes(details.strShortcutExe) !== job.exe) {
        apps.SetShortcutExe(appid, job.exe);
        continue;
      }
      if (details?.strShortcutExe !== `"${job.exe}"` && job.exe.includes(" ")) {
        apps.SetShortcutExe(appid, `"${job.exe}"`);
        continue;
      }
      const options = details.strShortcutLaunchOptions || details.strLaunchOptions;
      if (options === job.options && stripQuotes(details.strShortcutStartDir) === job.start) {
        await update(job.id, "added", appid, "");
        toaster.toast({ title: "SaveSync", body: `${job.title} was added to your Steam library.` });
        return;
      }
    }
    throw new Error("Steam did not confirm the shortcut target, start directory and launch options.");
  } catch (error) {
    await update(job.id, "error", appid, error instanceof Error ? error.message : String(error));
  }
}

async function processQueue(retry = false) {
  if (processing || stopping || !steam.SteamClient?.Apps?.AddShortcut || !steam.appStore) return;
  processing = true;
  try {
    for (const job of await pending()) {
      if (stopping) break;
      if (job.status === "pending" || job.status === "adding" || (retry && job.status === "error")) {
        await registerGame(job);
      }
    }
  } catch (error) {
    console.error("SaveSync Bridge", error);
  } finally {
    processing = false;
  }
}

function Panel() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState("");
  const [libraryError, setLibraryError] = useState("");
  const [state, setState] = useState<SyncState>();
  const [active, setActive] = useState<number[]>([]);
  const [selected, setSelected] = useState<number>();
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [libraryGame, setLibraryGame] = useState("");
  const [details, setDetails] = useState<{ game: string; result: SaveResult }>();
  const [downloading, setDownloading] = useState(false);
  const games = state?.games || {};
  const bindings = state?.bindings || {};
  const titles = Object.entries(games).sort(([, a], [, b]) => a.localeCompare(b));
  const showLibrary = libraryOpen || !active.length;
  const appid = selected && active.includes(selected) ? selected : active.length === 1 ? active[0] : 0;
  const binding = bindings[appid];
  const selectedGame = showLibrary ? (games[libraryGame] ? libraryGame : "") : (binding?.id || "");
  const result = details?.game === selectedGame ? details.result : undefined;
  const failedJobs = jobs.filter((job) => job.status === "error");
  const refresh = async () => {
    setActive([...runningApps()]);
    await Promise.all([
      pending().then(setJobs).catch((reason) => console.error("SaveSync shortcuts", reason)),
      syncState().then((value) => { setState(value); setLibraryError(""); })
        .catch((reason) => { setLibraryError(String(reason)); }),
    ]);
  };
  useEffect(() => {
    void refresh();
    const timer = setInterval(refresh, 2500);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    let valid = true;
    if (selectedGame) void serializeSync(async () => {
      if (valid) {
        const status = await syncAction(selectedGame, "status");
        if (valid) setDetails({ game: selectedGame, result: status });
      }
    }).catch((reason) => { if (valid) setDetails({ game: selectedGame, result: { error: String(reason) } }); });
    return () => { valid = false; };
  }, [selectedGame]);
  const configure = async (id: string, auto: boolean, download: boolean) => {
    setError("");
    try {
      await serializeSync(() => syncConfig(appid, id, auto, download));
      await refresh();
    } catch (reason) { setError(String(reason)); }
  };
  const upload = async () => {
    if (!binding) return;
    setError("");
    try {
      const status = await serializeSync(() => syncAction(binding.id, "upload"));
      setDetails({ game: binding.id, result: status });
    }
    catch (reason) { setError(String(reason)); }
  };
  const downloadNow = async () => {
    if (!selectedGame || downloading) return;
    setError("");
    setDownloading(true);
    try {
      const status = await serializeSync(() => canDownloadNow()
        ? syncAction(selectedGame, "download")
        : Promise.resolve({ error: "Close any running games and try again once Steam shows they have exited." }));
      setDetails((previous) => ({
        game: selectedGame,
        result: { latest: previous?.game === selectedGame ? previous.result.latest : undefined, ...status },
      }));
    } catch (reason) { setError(String(reason)); }
    finally { setDownloading(false); }
  };
  return (
    <PanelSection title="SaveSync">
      {!!active.length && <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => { setError(""); setLibraryOpen(!showLibrary); }}>
          {showLibrary ? "Back to running games" : "Browse linked games"}
        </ButtonItem>
      </PanelSectionRow>}
      {!showLibrary && active.length > 1 && active.map((id) => <PanelSectionRow key={id}>
        <ButtonItem layout="below" onClick={() => { setError(""); setSelected(id); }}>
          {games[bindings[id]?.id] || "Steam app " + id}
        </ButtonItem>
      </PanelSectionRow>)}
      {selectedGame && <PanelSectionRow><div>{games[selectedGame]}{result?.latest && <div>
        Latest cloud save · {result.latest.device} · {new Date(result.latest.created).toLocaleString()}
      </div>}{result?.pending && <div>Upload pending</div>}{(result?.error || result?.message) &&
        <div>{result.error || result.message}</div>}</div></PanelSectionRow>}
      {!showLibrary && binding && <>
        <PanelSectionRow><ButtonItem layout="below" onClick={upload}>Upload checkpoint</ButtonItem></PanelSectionRow>
        <PanelSectionRow><ButtonItem layout="below" onClick={() => configure(binding.id, Boolean(binding.auto), !binding.download)}>
          {binding.download ? "Cancel download" : "Download on exit"}
        </ButtonItem></PanelSectionRow>
        <PanelSectionRow><ButtonItem layout="below" onClick={() => configure(binding.id, !binding.auto, Boolean(binding.download))}>
          {binding.auto ? "Disable auto-upload" : "Enable auto-upload"}
        </ButtonItem></PanelSectionRow>
      </>}
      {!showLibrary && appid > 0 && !binding && titles.map(([key, title]) => (
        <PanelSectionRow key={key}><ButtonItem layout="below" onClick={() => configure(key, false, false)}>Link to {title}</ButtonItem></PanelSectionRow>
      ))}
      {showLibrary && (selectedGame ? <>
        <PanelSectionRow><ButtonItem layout="below" disabled={downloading} onClick={downloadNow}>
          {downloading ? "Downloading…" : "Download latest save"}
        </ButtonItem></PanelSectionRow>
        <PanelSectionRow><ButtonItem layout="below" disabled={downloading} onClick={() => { setError(""); setLibraryGame(""); }}>
          Choose another game
        </ButtonItem></PanelSectionRow>
      </> : titles.map(([key, title]) => (
        <PanelSectionRow key={key}><ButtonItem layout="below" onClick={() => setLibraryGame(key)}>{title}</ButtonItem></PanelSectionRow>
      )))}
      {!libraryError && !state && <PanelSectionRow><div>Loading linked games…</div></PanelSectionRow>}
      {state && !titles.length && !libraryError && <PanelSectionRow>
        <div>Link a save folder for a game in the SaveSync app to see it here.</div>
      </PanelSectionRow>}
      {libraryError && <PanelSectionRow><div>Could not load linked games. {libraryError}</div></PanelSectionRow>}
      {error && <PanelSectionRow><div>{error}</div></PanelSectionRow>}
      {!!failedJobs.length && <PanelSectionRow>
        <ButtonItem layout="below" onClick={async () => { await processQueue(true); await refresh(); }}>Retry Steam setup</ButtonItem>
      </PanelSectionRow>}
      {jobs.filter((job) => job.status !== "added").map((job) => (
        <PanelSectionRow key={job.id}><div>{job.title} · {job.error || job.status}</div></PanelSectionRow>
      ))}
    </PanelSection>
  );
}

export default definePlugin(() => {
  stopping = false;
  const events = steam.SteamClient?.GameSessions?.RegisterForAppLifetimeNotifications?.(({ bRunning, unAppID }) => {
    const appid = unAppID >>> 0;
    if (bRunning) {
      started.add(appid);
      stopped.delete(appid);
    } else {
      started.delete(appid);
      stopped.add(appid);
      void serializeSync(() => afterExit(appid)).catch(console.error);
    }
  });
  const retryTimer = setInterval(() => {
    for (const appid of stopped) void serializeSync(() => afterExit(appid, true)).catch(console.error);
  }, 45000);
  const timer = setInterval(() => void processQueue(), 2500);
  void processQueue();
  return {
    name: "SaveSync Bridge",
    titleView: <div className={staticClasses.Title}>SaveSync Bridge</div>,
    content: <Panel />,
    icon: <span>↔</span>,
    onDismount() {
      stopping = true;
      clearInterval(timer);
      clearInterval(retryTimer);
      events?.unregister();
      started.clear();
      stopped.clear();
    },
  };
});
