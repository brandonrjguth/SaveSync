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
  SteamUIStore?: { RunningApps?: { appid: number }[] };
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

function runningApps() {
  let visible: number[] = [];
  try {
    visible = (steam.SteamUIStore?.RunningApps || []).map(({ appid }) => appid >>> 0);
  } catch { /* Lifecycle events remain available when Steam is rebuilding its UI. */ }
  return new Set([...visible.filter((appid) => appid && !stopped.has(appid)), ...started]);
}

function canDownloadNow() {
  try {
    return Array.isArray(steam.SteamUIStore?.RunningApps) && runningApps().size === 0;
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
  const [state, setState] = useState<SyncState>({ bindings: {}, games: {} });
  const [active, setActive] = useState<number[]>([]);
  const [selected, setSelected] = useState<number>();
  const [closedGame, setClosedGame] = useState("");
  const [details, setDetails] = useState<SaveResult>({});
  const appid = selected && active.includes(selected) ? selected : active.length === 1 ? active[0] : 0;
  const binding = state.bindings[appid];
  const idleGame = !active.length && state.games[closedGame] ? closedGame : "";
  const selectedGame = binding?.id || idleGame;
  const refresh = async () => {
    try {
      setJobs(await pending());
      setState(await syncState());
      setActive([...runningApps()]);
      setError("");
    } catch (reason) {
      setError(String(reason));
    }
  };
  useEffect(() => {
    void refresh();
    const timer = setInterval(refresh, 2500);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    let valid = true;
    setDetails({});
    if (selectedGame) void serializeSync(() => syncAction(selectedGame, "status"))
      .then((result) => { if (valid) setDetails(result); }).catch((reason) => { if (valid) setError(String(reason)); });
    return () => { valid = false; };
  }, [selectedGame]);
  const configure = async (id: string, auto: boolean, download: boolean) => {
    try {
      await serializeSync(() => syncConfig(appid, id, auto, download));
      await refresh();
    } catch (reason) { setError(String(reason)); }
  };
  const upload = async () => {
    if (!binding) return;
    try { setDetails(await serializeSync(() => syncAction(binding.id, "upload"))); }
    catch (reason) { setError(String(reason)); }
  };
  const downloadNow = async () => {
    if (!idleGame) return;
    try {
      const result = await serializeSync(() => canDownloadNow()
        ? syncAction(idleGame, "download")
        : Promise.resolve({error: "Close the game before downloading a save."}));
      setDetails((previous) => ({...previous, ...result}));
    } catch (reason) { setError(String(reason)); }
  };
  return (
    <PanelSection title="SaveSync">
      {active.length > 1 && active.map((id) => <PanelSectionRow key={id}>
        <ButtonItem layout="below" onClick={() => setSelected(id)}>{state.games[state.bindings[id]?.id] || "Steam app " + id}</ButtonItem>
      </PanelSectionRow>)}
      {binding && <>
        <PanelSectionRow><div>{state.games[binding.id]}{details.latest && <div>
          Latest cloud save · {details.latest.device} · {new Date(details.latest.created).toLocaleString()}
        </div>}{details.pending && <div>Upload pending</div>}{<div>{details.error || details.message}</div>}</div></PanelSectionRow>
        <PanelSectionRow><ButtonItem layout="below" onClick={upload}>Upload checkpoint</ButtonItem></PanelSectionRow>
        <PanelSectionRow><ButtonItem layout="below" onClick={() => configure(binding.id, Boolean(binding.auto), !binding.download)}>
          {binding.download ? "Cancel download" : "Download on exit"}
        </ButtonItem></PanelSectionRow>
        <PanelSectionRow><ButtonItem layout="below" onClick={() => configure(binding.id, !binding.auto, Boolean(binding.download))}>
          {binding.auto ? "Disable auto-upload" : "Enable auto-upload"}
        </ButtonItem></PanelSectionRow>
      </>}
      {appid > 0 && !binding && Object.entries(state.games).map(([key, title]) => (
        <PanelSectionRow key={key}><ButtonItem layout="below" onClick={() => configure(key, false, false)}>Link to {title}</ButtonItem></PanelSectionRow>
      ))}
      {!active.length && (idleGame ? <>
        <PanelSectionRow><div>{state.games[idleGame]}{details.latest && <div>
          Latest cloud save · {details.latest.device} · {new Date(details.latest.created).toLocaleString()}
        </div>}{<div>{details.error || details.message}</div>}</div></PanelSectionRow>
        <PanelSectionRow><ButtonItem layout="below" onClick={downloadNow}>Download latest save</ButtonItem></PanelSectionRow>
        <PanelSectionRow><ButtonItem layout="below" onClick={() => setClosedGame("")}>Choose another game</ButtonItem></PanelSectionRow>
      </> : Object.entries(state.games).map(([key, title]) => (
        <PanelSectionRow key={key}><ButtonItem layout="below" onClick={() => setClosedGame(key)}>{title}</ButtonItem></PanelSectionRow>
      )))}
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={async () => { await processQueue(true); await refresh(); }}>Retry Steam setup</ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <div>{error || (jobs.length ? "" : "Download a game in SaveSync to add it here.")}</div>
      </PanelSectionRow>
      {jobs.map((job) => (
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
