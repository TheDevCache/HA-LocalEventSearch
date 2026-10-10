# Local Event Scout for Windows

The same Event Scout as the Home Assistant integration, as a small Windows app. It runs in the tray (near the clock), searches every night, and shows your events on a page in your browser.

## Install

1. Download `LocalEventScout.exe` from the [latest release](https://github.com/TheDevCache/HA-LocalEventSearch/releases/latest).
2. Run it. Windows may warn that the app is from an unknown publisher, because it isn't code-signed. Choose **More info → Run anyway**.
3. Your browser opens the setup guide. You'll go through:
   - your town;
   - your interests;
   - connecting an AI provider (Google Gemini or OpenRouter, see the main README for keys and costs);
   - optionally, finding your town's event calendars.

The page lives at <http://127.0.0.1:47821/>. To reopen it, use the tray icon's **Open Event Scout**, or bookmark the page.

## Good to know

- **Nightly search:** it runs only while the computer is on and the app is running. Turn on **Start with Windows** in Settings → This app. If the computer was off at search time, the missed search runs a minute after the app starts.
- **Your data:** settings and results are in `%APPDATA%\LocalEventScout`. API keys are in Windows Credential Manager, under "LocalEventScout".
- **Calendar feed:** calendar apps on the same computer can subscribe to the feed link. For a phone on your home Wi-Fi, turn on network sharing in Settings → This app. That shares only the calendar feed, on port 47822. Windows Firewall may ask to allow it.
- **Weather:** forecasts come from [Open-Meteo](https://open-meteo.com) for your first location.
- **Updates:** the app checks GitHub once a day and shows a banner when a new version is out. To update, download the new `.exe`, quit the old one from the tray, and run the new one.
- **Uninstall:**
  1. Quit from the tray.
  2. Turn off Start with Windows first, or delete the `LocalEventScout` value under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.
  3. Delete the `.exe` and `%APPDATA%\LocalEventScout`.

## Command line

```
LocalEventScout.exe [--port 47821] [--data-dir PATH] [--background] [--no-tray] [--self-test --report FILE]
```

## Building

```
pip install -r windows/requirements.txt pyinstaller
python windows/build.py        # → windows/dist/LocalEventScout.exe
python windows/run.py          # or run from the checkout without building
```

The app imports the shared engine from `custom_components/local_event_scout/scout/`, so both builds always have the same features. GitHub Actions builds and smoke-tests the `.exe` on every push. Changing the version number (in `scout/engine.py` and `manifest.json`) on main publishes a release with the `.exe` attached; pushing a tag such as `v0.6.0` works too.
