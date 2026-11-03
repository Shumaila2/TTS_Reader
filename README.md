<div align="center">

# 🔊 Natural TTS Reader

**Paste any text and listen to it in a natural voice, with the current word highlighted** 🎧

![Python](https://img.shields.io/badge/Python-3-b82e43?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Windows-.exe-b82e43?logo=windows&logoColor=white)
![Status](https://img.shields.io/badge/Status-v1.0-2f7a16)



</div>

A small app that reads your text out loud while you do something else.

It started as a desktop script that read one sentence at a time, and grew into a browser-based reader that plays your whole text as one smooth recording, highlights each word as it is spoken, and lets you click any word to jump straight to it.

**Current Version: v1.0 — Working**

---

## 🎬 Demo

https://github.com/user-attachments/assets/816a7ed3-ae2a-47af-90ca-f45f54034346

---

## ✨ Features

* 🗣️ Natural neural voices, not the robotic kind
* 🎵 Reads your **whole text as one continuous recording**, with no chopped-up sentences and no clicking "next"
* 🖍️ The word being spoken is **highlighted live**
* 👆 **Click any word** to jump straight there
* ⏯️ Pause and resume from the **exact spot** you stopped, nothing is regenerated
* 🔁 Restart button, and Space bar to pause or resume
* 🐢 6 speed options, with a slow, easy-listening pace as the default
* 🎙️ 7 voices to pick from (US, UK, Australian and Indian English)
* 📄 Keeps your document's layout: headings, quotes, lists and paragraphs look the same in the reading box
* 🧹 Strips Markdown symbols and links so they are not read aloud
* ⏸️ Takes a natural pause between paragraphs, headings and list items
* 🌐 Opens itself in your browser, and keeps reading even if you switch to another tab or app
* 🎨 Every color lives in one place, so it is easy to restyle

---

## 🧠 How It Works

```text
Pasted text
   ↓
Split into blocks (headings, quotes, list items, paragraphs)
   ↓
Each block → edge-tts → audio + the time every word is spoken
   ↓
Audio stitched into one MP3, word times shifted to match
   ↓
The browser plays it, highlights the current word,
and jumps when you click a word
```

Each block is sent to the voice **separately**, so the word timings for that block come straight from the voice service. This keeps the reading box lined up with the audio, instead of guessing which word belongs where.

The page you see is plain HTML/CSS/JS served by a tiny Flask server running on your own PC.

---

## 📁 Project Structure

```text
TTS_Reader/
│
├── tts_reader.py
├── tts_reader.ico
└── README.md
```

| File             | Purpose                                                                 |
| ---------------- | ----------------------------------------------------------------------- |
| `tts_reader.py`  | The whole app: the Flask server, the text processing, and the web page  |
| `tts_reader.ico` | The app icon used when building the `.exe`                              |

> The web page (HTML, CSS and JavaScript) is stored inside `tts_reader.py` itself, so there is no `templates` folder to carry around. That also makes building the `.exe` simpler.

---

## 🔄 Version History

### v1.0 — First Release

Built in about three days, starting from a small `pygame` script that read text one sentence at a time. It was rebuilt as a browser-based reader that plays the whole text as one continuous recording, highlights the current word, lets you click any word to jump, and pauses and resumes from the exact spot.

It also keeps your document's layout in the reading box, takes natural pauses between paragraphs, has speed and voice options, opens the browser by itself, and can be packaged as a standalone `.exe` with its own icon.

---

## 🛠️ Technologies Used

* 🐍 Python 3
* 🌶️ Flask (the local server)
* 🗣️ edge-tts (the voices and word timings)
* 🌐 HTML, CSS and JavaScript
* 📦 PyInstaller (builds the `.exe`)

---

## ⚙️ Setup

### Run from source

```bash
pip install flask edge-tts
python tts_reader.py
```

Your browser opens at `http://127.0.0.1:5000` by itself. Paste your text, press **Generate & Play**, and click any word whenever you want to jump.

<details>
<summary>📦 Build the .exe yourself</summary>

Run this from the folder that contains `tts_reader.py` and `tts_reader.ico`:

```bash
pyinstaller --onefile --noconsole --icon="tts_reader.ico" --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module PySide6 --exclude-module matplotlib tts_reader.py
```

The finished app is `dist\tts_reader.exe`. If something goes wrong, build once without `--noconsole` so the error stays visible in a console window.

> Windows may show a firewall prompt or a "Windows protected your PC" message the first time, because the app opens a local port and is unsigned. That is normal.

</details>

## 📥 Download

Just want the app? Open the **[latest release](https://github.com/Shumaila2/TTS_Reader/releases/latest)**, download `tts_reader.exe`, and run it. No Python needed. Your browser opens by itself with the reader.

> The first launch can take a few seconds while the app unpacks itself. Windows may also show "Windows protected your PC" or a firewall prompt, because the app is unsigned and opens a local port. Click *More info*, then *Run anyway*, and allow access if asked.

> You need an internet connection, because the voices come from an online service.

---

## 🎨 Customizing

| What to change              | Where                                                                 |
| --------------------------- | --------------------------------------------------------------------- |
| Colors (buttons, highlight) | The `:root { ... }` block at the top of the `<style>` section         |
| Voices in the list          | The `VOICES` dictionary, and the matching `<select id="voiceSelect">` |
| Default speed               | `DEFAULT_RATE` near the top of the file                               |

Run `edge-tts --list-voices` to see every available voice.

---

## ⚠️ Current Limitations

* 🌐 Needs an internet connection, because the voices come from an online service
* 🧩 Built on `edge-tts`, an unofficial library, so it can stop working if the service it uses changes
* 🇬🇧 Only English voices are in the list for now
* ⌨️ Space bar works only while the app's browser tab is in focus
* ⏳ The whole text is generated before it starts playing, so long texts take a moment
* 🔤 Bold and italic are not shown, and links are removed from the text
* ✂️ Apostrophes are removed, so a word like "don't" appears as "don t" in the reading box
* 🪟 The packaged `.exe` is Windows only

---

## 🔮 Future Improvements

> Features listed here are **not part of v1.0 unless they are actually implemented.**

* 🧩 **Browser extension:** read the text you select on any web page, without copying and pasting
* ⌨️ Keyboard shortcuts that work even when the app is not in focus
* 🌍 More voices and languages

---

## 🏁 v1.0 — Working

v1.0 is the current working version of the project.

It will only need changes when there is a **real bug, a change in the voice service, or a practical reason to change how it works.**
