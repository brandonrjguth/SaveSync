# SaveSync

SaveSync moves PC game saves between Windows, Steam Deck and an Android staging folder. It keeps previous versions and pauses when it finds competing changes. Games can come from Steam, GOG, other launchers or a standalone install.

## Install on Windows

Download `SaveSync-Setup.exe` from the [GitHub Releases](https://github.com/brandonrjguth/SaveSync/releases) page and run it. No Python installation or administrator privileges are required — everything is bundled. It adds **SaveSync** to your Start Menu (and optionally your Desktop) and opens a dedicated window.

If you're running from source instead, install [Python 3.10 or newer](https://www.python.org/downloads/windows/), run `python -m pip install -r requirements.txt`, and double-click `SaveSync.pyw`.

## Install on Steam Deck

Download `SaveSync-SteamDeck.tar.gz` from the [GitHub Releases](https://github.com/brandonrjguth/SaveSync/releases) page in Desktop Mode. No Python installation or manual dependencies are required — everything is bundled.

1. Right-click `SaveSync-SteamDeck.tar.gz` in Dolphin and choose **Extract > Extract archive here**.
2. Open the extracted `SaveSync-SteamDeck` folder and double-click **Install SaveSync** (or run `./install.sh` in Konsole).
3. The installer copies SaveSync to `~/.local/share/SaveSync` and creates **SaveSync** shortcuts on your Desktop and in the Application Menu. Re-running the installer with a newer release updates the app while keeping your settings and library intact.
4. To also open **SaveSync** from **Gaming Mode**, open the Application Menu, right-click **SaveSync**, and select **Add to Steam**. In Gaming Mode, use the touchscreen or set the shortcut's Steam Input layout to **Keyboard (WASD) and Mouse**, and press **Steam + X** for the on-screen keyboard. The **Exit** button returns you to Steam.

SaveSync opens in its own window using Chrome, Chromium, Edge, Brave, or Firefox (whichever is installed from Discover or pre-installed). See [Valve's Steam Deck Desktop FAQ](https://help.steampowered.com/en/faqs/view/671A-4453-E8D2-323C) for the Non-Steam library workflow.

## Connect your devices

1. Create a folder for your shared library. In SaveSync **Settings** on each device, enter its path to the same mounted network folder or to a local copy mirrored by your file-sync provider. On the Deck, that path must also be accessible in Gaming Mode. For a mirrored folder, keep its files available offline and let transfers finish before switching devices. SaveSync stores snapshots under `SaveSync-v1`.
2. To find save locations, SaveSync uses [Ludusavi](https://github.com/mtkennerly/ludusavi/releases/latest). Select **Download Ludusavi** in Settings, or the download button shown while adding a game, and SaveSync downloads and sets it up automatically on Windows and Steam Deck. To use a copy you already have, point **Settings > Ludusavi executable** at it or put it on your PATH. SaveSync uses Ludusavi's preview scan and existing scan configuration. Manual paths work without it.

## Find and sync a game

1. On the device with the progress you want, make a save in the game and close it. In SaveSync, select **Add game**, enter the title and, on Windows, optionally the game's install folder — it is remembered for transfers to a Steam Deck. The save location is detected automatically when Ludusavi is set up, and you can confirm or change it. If detection finds nothing, enter the path manually. **Find saves** scans by exact store title, or all detected games, and lists each dedicated save folder or single save file to choose from.
2. Use **Upload saves**. On the other device, select **Refresh** in SaveSync once the shared folder has finished transferring. Select the existing library entry and map its local save path with **Find local saves** or **Enter path**. For a new device with no saves, you can enter the destination path even if it does not exist yet.
3. Use **Download saves** before playing on the second device. After playing, close the game, select **Check again**, and upload the new progress before moving between devices.
4. If progress differs, pick this device's files or a shared version. **Version history** lets you restore an older shared snapshot as the latest version. If a local restore is interrupted, select **Recover original files** before syncing again.

Use a dedicated folder whose relative filenames and folder layout match on both devices, or choose single-file mode if the filename differs. Each entry maps one location per device. For a title with multiple unrelated save paths, add a separate entry for each. Folder sync can remove files absent from the chosen version. It preserves a recovery ZIP in the local app data folder before overwriting or removing files. SaveSync keeps previously published snapshots and does not prune history automatically.

### Steam Deck and DRM-free games

Ludusavi checks Windows, native Linux, and Proton paths according to its scanner settings. Add any extra Steam libraries, GOG installs, or other launcher roots in [Ludusavi's roots settings](https://github.com/mtkennerly/ludusavi/blob/master/docs/help/roots.md). For a non-Steam shortcut or a DRM-free Wine install, open **Wine and Proton options** while finding saves and provide the prefix containing `drive_c`. A Steam Proton prefix is normally at `<Steam library>/steamapps/compatdata/<id>/pfx`. If the scan cannot identify a title's files, you can still map a known save location. Games with incompatible native Linux and Windows save formats need matching game versions or their own conversion step.

If a title already uses Steam Cloud and you want SaveSync to manage its progress, turn Steam Cloud off for that title on each device after confirming the current copy you want. Other launcher cloud-sync features can also overwrite the same files.

## Transfer a Windows game to the Deck

This copies a portable Windows game folder to the Deck over your local network. SaveSync verifies each file before making the installation available. Game files travel directly from the PC. Linked saves continue to use the shared save folder configured on both devices.

### One-time Steam setup

Install [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader#installation) and then install `SaveSync-Decky-Bridge.zip` through **Decky Settings > Developer > Install Plugin from ZIP File**. Enable Developer Mode to show the ZIP installer. The bridge creates the Non-Steam shortcuts while Steam is running in Gaming Mode. It selects Proton Experimental and uses a dedicated prefix for each game. Install Proton Experimental from Steam if it is missing. Steam persists new shortcuts when it exits normally.

### Copy a game

1. Put both devices on the same trusted network. On Windows, open **Game transfer > Start sharing** with the PC's private LAN address shown in the app. If Windows Firewall prompts, allow SaveSync on the private network. The host listens on TCP and UDP port 47835 while sharing is active and shows a 6-character pairing code (for example, `K7M-4P9`).
2. Enter the game title and its Windows install folder. Games added with an install folder offer **Send to Steam Deck** from the library, which fills this form in advance. Optionally use **Find executables** and pick the launch executable, or leave it blank to choose on the Deck after the download. If you want to carry progress over, select the matching **Link a save entry**. With the game closed, select **Share game**. SaveSync publishes newer local saves if the linked entry is ready to upload.
3. On the Deck, open SaveSync and select **Game transfer**. SaveSync automatically finds your PC on the local network (or you can expand **Can’t find your PC? Enter IP manually** if your router blocks local discovery). Type the 6-character pairing code shown on Windows and choose **Connect to PC**. Keep the code private; after 5 wrong guesses the session locks until you start a new one.
4. Choose an install folder and select **Download** next to the game. Leave **Add to Steam with Proton Experimental** selected. Keep the PC's SaveSync window open until the transfer finishes. If the executable was left blank on the PC, SaveSync lists the downloaded game's executables: pick one and the Steam shortcut is queued. The new entry appears under **Library > Non-Steam**. If Steam setup needs attention, use the SaveSync Bridge panel in Decky's Quick Access menu.

SaveSync can map a linked save location automatically when it lives under the game folder or under Windows Documents, Saved Games, or AppData. If the same shared save library is already available on the Deck, SaveSync pulls the current snapshot during setup. When you use **Find local saves** for a transferred game on the Deck, the scan starts by itself and pre-fills the game's Proton prefix. For a custom location, map the local path in **Game library**. Select **Retry setup** in **Game transfer** if the shared save library was still arriving. Later uploads and downloads still happen on demand from **Game library** with the game closed.

The game needs to be playable from its copied folder under Proton. Titles that require an installer, registry entries, launchers or DRM may need their original installation process. Ending the sharing session invalidates the connection code. Keep the bridge installed for automatic Steam registration of future downloads.

## Android with GameNative

The Android workflow uses GameNative's **Export Files** and **Import Files** to move saves across its container boundary. SaveSync runs in [Termux](https://github.com/termux/termux-app#installation) using the same interface. Automatic discovery inside GameNative and background sync are not implemented.

1. In Termux, run `pkg install python` and `termux-setup-storage`. Copy this project from Downloads to the Termux home folder, then run `python -m savesync --no-browser` from the project directory. Open the loopback address it prints in your Android browser.
2. Use a file-sync provider that mirrors the shared library into an Android folder Termux can read and write. Enter that folder in SaveSync **Settings**. Create a separate staging location in shared storage for each game's save files. Keep it outside the shared library.
3. In a [GameNative build with file import and export](https://github.com/utkarshdalal/GameNative/releases), close the game. Export its saves to Downloads and copy the files into the staging location. For a directory entry, match the desktop save folder's relative layout. Map the corresponding desktop library entry to that staging folder or file.
4. After playing on Android, export the current saves into staging before checking and uploading them. Before playing, download any newer version into staging, then import the files from there into the original GameNative container folder with **Replace existing files** enabled. If the downloaded snapshot removed a file, reconcile the old container copy manually. For a single file with a different name in the container, give the staging file the Android name.

## Files and limits

- Local configuration and recovery ZIPs are under `%LOCALAPPDATA%\SaveSync` on Windows and `~/.local/share/savesync` on Linux and Termux by default. Use `--data-dir PATH` for another location. A Ludusavi copy downloaded by SaveSync is installed under the same folder in `tools/ludusavi`. The ZIP stores saved data under `files/`, and a single-file entry stores it as `files/save`.
- Snapshots are capped at 20,000 files and 8 GiB of uncompressed data. Registry entries, links, and junctions within save locations are outside the file-sync workflow.
- SaveSync currently runs on demand. It uses your existing folder transport and serves its interface only on the device running it. Keep the game closed during sync.
