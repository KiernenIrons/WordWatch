# WordWatch

An offline Windows speech coach: spot words you want to avoid, play your own alert sound, and build vocabulary with live daily challenges.

## Download version 1

**[Download the Windows installer](https://github.com/KiernenIrons/WordWatch/releases/latest/download/WordWatch-1.0.0-Setup.exe)**

Run the installer, then open WordWatch from your Desktop or Start menu. All three speech models are included; no Python, account, API key, or model downloads are needed. The large download contains the offline models. Windows may display an unknown-publisher warning because this release is not code-signed.

Windows 10/11, 64-bit Intel/AMD. Allow about 2 GB for the installed app, plus space for the installer. Vosk CPU is the first-run default and works without NVIDIA hardware. The two NVIDIA models require a compatible CUDA-capable GPU and driver; GPU compatibility has not been tested across all cards. Choose microphone, audio output and transcription model in **Settings**. NVIDIA recognition generally offers better accuracy than the small CPU model. Recognition and alert timing depend on hardware, background load and speech; errors are possible.

## Features

- Live transcription with a 2,000-word on-screen backlog.
- Custom trigger words, phrases and editable preset categories.
- Beep or a custom WAV, MP3, FLAC or OGG; volume and quiet lead-in controls.
- Fast, Careful and transcript-only modes; incomplete-word guards reduce alerts such as “hell” during “hello.”
- Select microphone and alert output independently.
- Vosk English CPU, NVIDIA Nemotron English and Nemotron 3.5 engines.
- Ten vocabulary words with definitions, rotating every 24 hours, with a countdown.
- Live personal ranking of the current challenge words. Confirmed scores persist; earlier periods remain in the local database.

Close quits listening; minimize keeps it running. The app starts listening when opened, but does not register itself to launch with Windows. Pause stops capture. Provisional text/counts can change as recognition improves; only finalized challenge counts are saved.

## Privacy

Speech recognition runs on this PC. WordWatch does not upload audio or transcripts and does not save them. Settings, your imported alert sound and vocabulary counts are stored in `%LOCALAPPDATA%\WordWatch`. NVIDIA uses an authenticated localhost connection. Uninstall removes the program but preserves personal settings and scores. No developer settings, sound recordings or personal scores are included in this release.

## Build from source

On Windows with Python 3.11:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python test_daily_challenge.py
python wordwatch.py --self-test self-test.txt
python -m PyInstaller --noconfirm WordWatch.spec
```

For source execution, put the release's `nvidia-runtime` folder alongside `wordwatch.py`, or set `WORDWATCH_RUNTIME` to its location. For packaged execution, put that folder alongside `WordWatch.exe`. The older `Documents\WordWatch\nvidia-runtime` location remains supported. The installer contains the runtime and weights, which are excluded from Git history.

To build the installer with Inno Setup 6, create a `payload` directory containing the built `WordWatch.exe`, `nvidia-runtime`, license notices and README. Run `ISCC.exe /DPayloadDir="C:\absolute\payload" /DReleaseDir="C:\absolute\artifacts" installer.iss`.

`--smoke-test` exercises the UI hidden, without starting microphone capture. Set `LOCALAPPDATA` to a disposable folder when testing so your real settings and scores are not modified.

## Licenses

WordWatch's original source is MIT licensed. Bundled speech engines, models and libraries retain their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). WordWatch is not affiliated with NVIDIA or Alpha Cephei.
