# Android phone setup

Seva is a browser PWA. It uses the phone's own microphone while the listening
screen is open. There is no Android app or foreground service in this repository,
so reliable listening after closing the browser is **not** supported.

## Requirements

- Android Chrome (or another Chromium browser with `getUserMedia`).
- An **HTTPS** address. Chrome blocks microphone capture on plain HTTP except
  for `localhost`, which is not reachable from another device.
- A working Seva server with `SEVA_ENABLE_DEVICE_MIC=1` and a Gnani key.
- A persistent `SEVA_DATA_DIR` if you want generated alert audio to survive a
  restart. On Render's free tier this directory is ephemeral.

## First run

1. Open the HTTPS Seva address in Chrome.
2. Complete the setup screen and confirm the language and the token/place you
   are waiting for.
3. On the listening screen, tap **Start** or **Tap to start**.
4. When Chrome asks for microphone permission, choose **Allow**.
5. Keep the screen open and the phone out of a pocket or bag. Cloth over the
   microphone is the most common cause of missed announcements.
6. Press **Stop** when done. Closing the page, switching apps, or locking the
   screen may pause or end capture.

If the tab is closed, the browser stops the microphone. Seva cannot listen in
the background after the tab is closed. For true background listening, an
Android app with a foreground service and a persistent notification would be
required; that is outside this implementation.

## Install as a home-screen app

Use Chrome's menu and choose **Add to Home screen** / **Install app**. This
opens Seva in a standalone window but does not change the browser's microphone
or background restrictions.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| "Secure connection needed" | Use the `https://` address, not `http://` or a LAN IP over HTTP. |
| "Microphone blocked" | Chrome site settings → Permissions → Microphone → Allow, then reload. |
| "No microphone found" | Confirm another app is not holding the microphone and that the device has one. |
| "Microphone unavailable" | Close other apps using the microphone and try again. |
| No alerts | Keep the screen open, raise media volume, and confirm the watched token/place matches the announcement. |
| "Live listening unavailable" | The server has no speech-provider key or device listening is disabled. |
| Audio stops on lock | Expected for a browser PWA. Keep the screen awake, or use a native app with a foreground service. |

Seva uploads only short clips that pass the local loudness gate. Silence is not
uploaded. Temporary clips are deleted after processing; generated alert audio
may remain cached on the server. See `web/privacy.html` for the user-facing
description.
