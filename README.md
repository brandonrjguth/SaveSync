# SaveSync

SaveSync moves PC game saves between Windows, Steam Deck and an Android staging folder. It keeps previous versions and pauses when it finds competing changes. Games can come from Steam, GOG, other launchers or a standalone install.

## Open on Windows

Double-click `SaveSync.exe` on 64-bit Windows. It opens a dedicated window using Microsoft Edge or Google Chrome, whichever is installed. The Windows build includes Python. You can pin SaveSync to the taskbar or make a desktop shortcut.

If you're running from the source archive, install [Python 3.10 or newer](https://www.python.org/downloads/windows/) and double-click `SaveSync.pyw`. To host game transfers from source, also install `cryptography` with `python -m pip install cryptography`.

## Add to Steam Deck Gaming Mode

Do this setup once in Desktop Mode. After that, open **SaveSync** from **Library > Non-Steam** in Gaming Mode. If SaveSync is already in Steam, extract the update over the existing app folder so the shortcut still works.

1. In Discover, install **Google Chrome** or **Chromium**, or use the one you already have. SaveSync uses it to show its own window. The launcher uses SteamOS's `python3` and requires Python 3.10 or newer.
2. Extract `SaveSync-SteamDeck.tar.gz` into a folder you intend to keep, such as `Home/Applications`. The archive marks `SaveSync.sh` executable. If you copied the files another way, in Dolphin open the script's **Properties > Permissions** and enable **Is executable**.
3. In the desktop Steam client, select **Games > Add a Non-Steam Game to My Library > Browse** and choose the extracted `SaveSync.sh`. Select **All Files** if the script is hidden by the file filter. Add the shortcut, then rename it **SaveSync** in its Steam properties if it appears as **SaveSync.sh**. Leave the compatibility tool option off.
4. Return to Gaming Mode and open **SaveSync**. Use the touchscreen or set the shortcut's Steam Input layout to **Keyboard (WASD) and Mouse** for pointer controls. Press **Steam + X** for the on-screen keyboard. The **Exit** button returns you to Steam.

See [Valve's Steam Deck Desktop FAQ](https://help.steampowered.com/en/faqs/view/671A-4453-E8D2-323C) for the Non-Steam library workflow.

## Connect your devices

1. Create a folder for your shared library. In SaveSync **Settings** on each device, enter its path to the same mounted network folder or to a local copy mirrored by your file-sync provider. On the Deck, that path must also be accessible in Gaming Mode. For a mirrored folder, keep its files available offline and let transfers finish before switching devices. SaveSync stores snapshots under `SaveSync-v1`.
2. To find save locations, download [Ludusavi](https://github.com/mtkennerly/ludusavi/releases/latest) for each desktop platform. Extract it and point **Settings > Ludusavi executable** to the actual executable, or put it on your PATH. SaveSync uses Ludusavi's preview scan and existing scan configuration. Manual paths work without it.

## Find and sync a game

1. On the device with the progress you want, make a save in the game and close it. In SaveSync, select **Find saves**. Search by the exact store title or leave the title blank to scan all detected games. Choose the matching dedicated save folder or a single save file. **Add game** lets you enter the path directly.
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

1. Put both devices on the same trusted network. On Windows, open **Game transfer > Start sharing** with the PC's private LAN address shown in the app. If Windows Firewall prompts, allow SaveSync on the private network. The host listens on TCP port 47835 while sharing is active.
2. Enter the game title and its Windows install folder. Use **Find executables** and pick the actual launch executable. If you want to carry progress over, select the matching **Link a save entry**. With the game closed, select **Share game**. SaveSync publishes newer local saves if the linked entry is ready to upload.
3. On the Deck, open SaveSync from **Library > Non-Steam**. Under **Game transfer**, paste the PC's full connection code and choose **Connect to PC**. Keep the code private. It grants access to the folders shared for that session.
4. Choose an install folder and select **Download** next to the game. Leave **Add to Steam with Proton Experimental** selected. Keep the PC's SaveSync window open until the transfer finishes. The new entry appears under **Library > Non-Steam**. If Steam setup needs attention, use the SaveSync Bridge panel in Decky's Quick Access menu.

SaveSync can map a linked save location automatically when it lives under the game folder or under Windows Documents, Saved Games, or AppData. If the same shared save library is already available on the Deck, SaveSync pulls the current snapshot during setup. For a custom location, map the local path in **Game library**. Select **Retry setup** in **Game transfer** if the shared save library was still arriving. Later uploads and downloads still happen on demand from **Game library** with the game closed.

The game needs to be playable from its copied folder under Proton. Titles that require an installer, registry entries, launchers or DRM may need their original installation process. Ending the sharing session invalidates the connection code. Keep the bridge installed for automatic Steam registration of future downloads.

## Android with GameNative

The Android workflow uses GameNative's **Export Files** and **Import Files** to move saves across its container boundary. SaveSync runs in [Termux](https://github.com/termux/termux-app#installation) using the same interface. Automatic discovery inside GameNative and background sync are not implemented.

1. In Termux, run `pkg install python` and `termux-setup-storage`. Copy this project from Downloads to the Termux home folder, then run `python -m savesync --no-browser` from the project directory. Open the loopback address it prints in your Android browser.
2. Use a file-sync provider that mirrors the shared library into an Android folder Termux can read and write. Enter that folder in SaveSync **Settings**. Create a separate staging location in shared storage for each game's save files. Keep it outside the shared library.
3. In a [GameNative build with file import and export](https://github.com/utkarshdalal/GameNative/releases), close the game. Export its saves to Downloads and copy the files into the staging location. For a directory entry, match the desktop save folder's relative layout. Map the corresponding desktop library entry to that staging folder or file.
4. After playing on Android, export the current saves into staging before checking and uploading them. Before playing, download any newer version into staging, then import the files from there into the original GameNative container folder with **Replace existing files** enabled. If the downloaded snapshot removed a file, reconcile the old container copy manually. For a single file with a different name in the container, give the staging file the Android name.

## Files and limits

- Local configuration and recovery ZIPs are under `%LOCALAPPDATA%\SaveSync` on Windows and `~/.local/share/savesync` on Linux and Termux by default. Use `--data-dir PATH` for another location. The ZIP stores saved data under `files/`, and a single-file entry stores it as `files/save`.
- Snapshots are capped at 20,000 files and 8 GiB of uncompressed data. Registry entries, links, and junctions within save locations are outside the file-sync workflow.
- SaveSync currently runs on demand. It uses your existing folder transport and serves its interface only on the device running it. Keep the game closed during sync.
