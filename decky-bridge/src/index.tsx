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
    Apps: {
      AddShortcut(name: string, exe: string, start: string, options: string): Promise<number>;
      SetShortcutExe(id: number, exe: string): void;
      SetShortcutStartDir(id: number, path: string): void;
      SetAppLaunchOptions(id: number, options: string): void;
      SpecifyCompatTool(id: number, tool: string): void;
      RegisterForAppDetails(id: number, callback: (details?: Details) => void): { unregister(): void };
    };
  };
  appStore?: { GetAppOverviewByAppID(id: number): unknown };
}

const steam = window as unknown as SteamWindow;
const pending = callable<[], Job[]>("jobs");
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
  const refresh = async () => {
    try {
      setJobs(await pending());
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
  return (
    <PanelSection title="SaveSync">
      <PanelSectionRow>
        <ButtonItem onClick={async () => { await processQueue(true); await refresh(); }}>Retry Steam setup</ButtonItem>
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
    },
  };
});
