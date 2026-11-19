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
4. To also open **SaveSync** from **Gaming Mode**, open the Application Menu, right-click **SaveSync**, and select **Add to Steam**. In Gaming Mode, use the D-pad to move, **A** to select and **B** to dismiss a dialog. If the browser does not receive gamepad input, map the shortcut's D-pad to the arrow keys, **A** to **Enter** and **B** to **Escape** in Steam Input. **Steam + X** opens the on-screen keyboard. The **Exit** button returns you to Steam.

SaveSync opens in its own window using Chrome, Chromium, Edge, Brave, or Firefox (whichever is installed from Discover or pre-installed). See [Valve's Steam Deck Desktop FAQ](https://help.steampowered.com/en/faqs/view/671A-4453-E8D2-323C) for the Non-Steam library workflow.

## Connect your devices

### Cloud saves

1. Open **Settings > Cloud saves** on the first device. Enter your server's HTTPS URL and the bootstrap code from its administrator.
2. Select **Pair another device** to generate an 8-character, single-use code. On the Deck or Android, enter the same HTTPS URL and that code. You can omit the dash and use lowercase. Select **Show code** to check what you typed. The code expires after 10 minutes. Each device keeps its pairing across restarts.
3. After playing, close the game and select **Upload save** under its title. On the next device, open the same game entry and choose **Use latest from another device**, or pick a timestamped snapshot in **Save history**. The newest save from each device is visible by default. Expand **older saves** under a device to see the rest. Map the local save path first if needed. Choosing a historical version makes it the active cloud version and keeps the other versions available.

### Shared-folder saves

1. Create a folder for your shared library. In SaveSync **Settings** on each device, enter its path to the same mounted network folder or to a local copy mirrored by your file-sync provider. On the Deck, that path must also be accessible in Gaming Mode. For a mirrored folder, keep its files available offline and let transfers finish before switching devices. SaveSync stores snapshots under `SaveSync-v1`.
2. To find save locations, SaveSync uses [Ludusavi](https://github.com/mtkennerly/ludusavi/releases/latest). Select **Download Ludusavi** in Settings, or the download button shown while adding a game, and SaveSync downloads and sets it up automatically on Windows and Steam Deck. To use a copy you already have, point **Settings > Ludusavi executable** at it or put it on your PATH. SaveSync uses Ludusavi's preview scan and existing scan configuration. Manual paths work without it.

## Find and sync a game using a shared folder

1. On the device with the progress you want, make a save in the game and close it. In SaveSync, select **Add game**, enter the title and, on Windows, optionally the game's install folder — it is remembered for transfers to a Steam Deck. The save location is detected automatically when Ludusavi is set up, and you can confirm or change it. If detection finds nothing, enter the path manually. **Find saves** scans by exact store title, or all detected games, and lists each dedicated save folder or single save file to choose from.
2. Use **Upload saves**. On the other device, select **Refresh** in SaveSync once the shared folder has finished transferring. Select the existing library entry and map its local save path with **Find local saves** or **Enter path**. For a new device with no saves, you can enter the destination path even if it does not exist yet.
3. Use **Download saves** before playing on the second device. After playing, close the game, select **Check again**, and upload the new progress before moving between devices.
4. If progress differs, pick this device's files or a shared version. **Version history** lets you restore an older shared snapshot as the latest version. If a local restore is interrupted, select **Recover original files** before syncing again.

Use a dedicated folder whose relative filenames and folder layout match on both devices, or choose single-file mode if the filename differs. Each entry maps one location per device. For a title with multiple unrelated save paths, add a separate entry for each. Folder sync can remove files absent from the chosen version. It preserves a recovery ZIP in the local app data folder before overwriting or removing files. SaveSync keeps previously published snapshots and does not prune history automatically.

### Steam Deck and DRM-free games

If a transferred Windows game uses a different Proton prefix from the one SaveSync selected, open its entry in **Game library** and choose **Detect active save location**. Launch the game from the Steam shortcut you normally use, make a test save, then close it. Return to SaveSync and select **Check for new save**. SaveSync updates the mapping when one matching location changes. Sync for that game pauses during detection. If the test save conflicts with the Windows snapshot, choose the Windows version in **Version history** to continue from your PC progress.

Ludusavi checks Windows, native Linux, and Proton paths according to its scanner settings. Add any extra Steam libraries, GOG installs, or other launcher roots in [Ludusavi's roots settings](https://github.com/mtkennerly/ludusavi/blob/master/docs/help/roots.md). For a non-Steam shortcut or a DRM-free Wine install, open **Wine and Proton options** while finding saves and provide the prefix containing `drive_c`. A Steam Proton prefix is normally at `<Steam library>/steamapps/compatdata/<id>/pfx`. If the scan cannot identify a title's files, you can still map a known save location. Games with incompatible native Linux and Windows save formats need matching game versions or their own conversion step.

If a title already uses Steam Cloud and you want SaveSync to manage its progress, turn Steam Cloud off for that title on each device after confirming the current copy you want. Other launcher cloud-sync features can also overwrite the same files.

## Sync around game sessions

On Steam Deck, install the matching **SaveSync Decky Bridge** and SaveSync app updates. With a game open, open the bridge in Quick Access. Transferred games with a linked save location are matched to their Steam shortcut. For another game, map its save location in SaveSync first, then link the running game in the bridge. Enable **Auto-upload on exit**, queue a download until exit, or upload a checkpoint after the game has finished writing it. The bridge waits for Steam to report the game closed before applying a queued download. Automatic actions keep conflicting progress for you to review in SaveSync.

To pull a cloud save before playing on Steam Deck, open **SaveSync Bridge** in Quick Access with your games closed. Choose a game from the linked list, select **Download latest save**, and wait for the result before launching. Any game with a save location mapped on the Deck appears in the list. If progress conflicts, select the version you want in SaveSync's **Save history**.

On Windows, open a mapped game's page and select **Create sync shortcut**. Select **Browse…** to choose the executable from the game's install folder, and add any launch arguments. Launch it from the new desktop shortcut to attempt a cloud download before launch and upload after the game process exits. Choose the game executable directly, since a separate launcher can exit before the game. Existing shortcuts continue to launch the game normally.

If the cloud is unavailable on exit, SaveSync keeps local progress and can retry a pending upload after the game closes again, or before the next Windows shortcut launch. A force-close can only upload files the game finished writing. If the wrapper or Steam itself is terminated, the exit hook may not run.

## Transfer a Windows game to the Deck

This copies a portable Windows game folder to the Deck over your local network. SaveSync verifies each file before making the installation available. If paired to a cloud server, use **Game library** for saves after installing the game.

### One-time Steam setup

Install [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader#installation) and then install `SaveSync-Decky-Bridge.zip` through **Decky Settings > Developer > Install Plugin from ZIP File**. Enable Developer Mode to show the ZIP installer. The bridge creates the Non-Steam shortcuts while Steam is running in Gaming Mode. It selects Proton Experimental and uses a dedicated prefix for each game. Install Proton Experimental from Steam if it is missing. Steam persists new shortcuts when it exits normally.

### Copy a game

1. Put both devices on the same trusted network. On Windows, open **Game transfer > Start sharing** with the PC's private LAN address shown in the app. If Windows Firewall prompts, allow SaveSync on the private network. The host listens on TCP and UDP port 47835 while sharing is active and shows a 6-character pairing code (for example, `K7M-4P9`).
2. Enter the game title and its Windows install folder. Games added with an install folder offer **Send to Steam Deck** from the library, which fills this form in advance. Optionally use **Find executables** and pick the launch executable, or leave it blank to choose on the Deck after the download. If you want to carry progress over, select the matching **Link a save entry**. With the game closed, select **Share game**. SaveSync publishes newer local saves if the linked entry is ready to upload.
3. On the Deck, open SaveSync and select **Game transfer**. SaveSync automatically finds your PC on the local network (or you can expand **Can’t find your PC? Enter IP manually** if your router blocks local discovery). Type the 6-character pairing code shown on Windows and choose **Connect to PC**. Keep the code private; after 5 wrong guesses the session locks until you start a new one.
4. Choose an install folder and select **Download** next to the game. Leave **Add to Steam with Proton Experimental** selected. Keep the PC's SaveSync window open until the transfer finishes. If the executable was left blank on the PC, SaveSync lists the downloaded game's executables: pick one and the Steam shortcut is queued. The new entry appears under **Library > Non-Steam**. If Steam setup needs attention, use the SaveSync Bridge panel in Decky's Quick Access menu.

The Deck remembers the PC from the first short-code pairing and reconnects when you open **Game transfer** while the PC is sharing. Use **Connect to saved PC** to retry. **Forget PC** clears the Deck's saved connection. On Windows, **Forget paired devices** revokes the local transfer credentials.

SaveSync can map a linked save location automatically when it lives under the game folder or under Windows Documents, Saved Games, or AppData. After a transfer, use **Game library** to select a cloud save. If the game uses a different Proton prefix, use **Detect active save location**. For a custom location, map the local path in **Game library**. On installations using the older shared-folder workflow, **Retry setup** can apply a snapshot that arrived after the transfer.

The game needs to be playable from its copied folder under Proton. Titles that require an installer, registry entries, launchers or DRM may need their original installation process. Ending the sharing session invalidates the connection code. Keep the bridge installed for automatic Steam registration of future downloads.

## Android with GameNative

The Android workflow uses GameNative's **Export Files** and **Import Files** to move saves across its container boundary. SaveSync runs in [Termux](https://github.com/termux/termux-app#installation) using the same interface. Automatic discovery inside GameNative and background sync are not implemented.

1. In Termux, run `pkg install python` and `termux-setup-storage`. Copy this project from Downloads to the Termux home folder, then run `python -m savesync --no-browser` from the project directory. Open the loopback address it prints in your Android browser.
2. Pair Termux SaveSync to the cloud server through **Settings > Cloud saves**, or configure a mirrored shared folder that Termux can read and write. Create a separate staging location in shared storage for each game's save files, outside the shared library.
3. In a [GameNative build with file import and export](https://github.com/utkarshdalal/GameNative/releases), close the game. Export its saves to Downloads and copy the files into the staging location. For a directory entry, match the desktop save folder's relative layout. Map the corresponding desktop library entry to that staging folder or file.
4. After playing on Android, export the current saves into staging before checking and uploading them. Before playing, download any newer version into staging, then import the files from there into the original GameNative container folder with **Replace existing files** enabled. If the downloaded snapshot removed a file, reconcile the old container copy manually. For a single file with a different name in the container, give the staging file the Android name.

## Files and limits

- Local configuration and recovery ZIPs are under `%LOCALAPPDATA%\SaveSync` on Windows and `~/.local/share/savesync` on Linux and Termux by default. Use `--data-dir PATH` for another location. A Ludusavi copy downloaded by SaveSync is installed under the same folder in `tools/ludusavi`. The ZIP stores saved data under `files/`, and a single-file entry stores it as `files/save`.
- Snapshots are capped at 20,000 files and 8 GiB of uncompressed data. Registry entries, links, and junctions within save locations are outside the file-sync workflow.
- SaveSync saves and restores on demand. Keep the game closed while uploading or applying a version.

## Host the cloud server

The server is a private library for one household or other group of devices you trust. Run it with Python 3.10 or newer behind your existing HTTPS reverse proxy. Its HTTP backend binds to loopback on port 47836, with health available at `/health`.

For a Linux systemd install, copy the `savesync` Python package into `/opt/savesync-cloud` and install `deploy/savesync-cloud.service`. Generate a high-entropy first-device code in `/etc/savesync-cloud/bootstrap` readable only by root. For example, use `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'` and redirect it there with `umask 077`. Enable the service with `systemctl enable --now savesync-cloud` after `systemctl daemon-reload`. Point a dedicated Caddy site at `reverse_proxy 127.0.0.1:47836`, or use the equivalent for your proxy. The HTTPS URL is what clients enter in Settings.

Back up `/var/lib/savesync-cloud` while the service is stopped to capture both the SQLite index and the save archives. Device tokens and single-use pairing codes are stored on the server as hashes.
