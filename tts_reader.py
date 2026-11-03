"""
Natural TTS Reader — Flask web app.

Why this design:
- Each structural block (heading/blockquote/list item/paragraph) is sent to
  edge-tts SEPARATELY and its audio is stitched together, rather than
  guessing how one combined block of text's words map back to blocks. This
  makes the "now reading" view's structure mapping exact instead of a
  fragile guess (edge-tts's own tokenizer doesn't always split
  punctuation-heavy text the same way a plain .split() would, so the
  earlier guess-and-compare approach silently fell back to a flat view on
  most real text).
- Pause/Resume just toggles the browser's real audio position. Nothing is
  ever regenerated or restarted, so it resumes from the exact spot.
- Click any word in the text to jump playback straight to it.
- The word currently being spoken is highlighted live, synced to edge-tts's
  own word-boundary timestamps.
- Runs as a browser tab, so it keeps reading in the background while you
  work on other things.

Install dependencies:
    pip install flask edge-tts

Run:
    python tts_reader_app.py

Then open http://127.0.0.1:5000
"""

import asyncio
import os
import re
import tempfile
import uuid
import webbrowser
from threading import Timer

import edge_tts
from flask import Flask, Response, jsonify, request, send_from_directory

# Curated voice list. Keep this in sync with the <select id="voiceSelect">
# options in INDEX_HTML below if you add/remove voices — this dict is also
# used server-side to reject anything not in the list. edge-tts has ~300
# voices across languages; run `edge-tts --list-voices` to see them all if
# you want to add more.
VOICES = {
    "en-US-AriaNeural": "Aria (US, female)",
    "en-US-GuyNeural": "Guy (US, male)",
    "en-US-JennyNeural": "Jenny (US, female)",
    "en-GB-SoniaNeural": "Sonia (UK, female)",
    "en-GB-RyanNeural": "Ryan (UK, male)",
    "en-AU-NatashaNeural": "Natasha (Australian, female)",
    "en-IN-NeerjaNeural": "Neerja (Indian English, female)",
}
DEFAULT_VOICE = "en-US-AriaNeural"

DEFAULT_RATE = "-15%"  # edge-tts's default pace reads quite fast for pure listening
RATE_PATTERN = re.compile(r"^[+-]\d{1,3}%$")

# edge-tts always requests this fixed output format ("audio-24khz-48kbitrate-
# mono-mp3"), so this bitrate lets us compute each block's exact audio
# duration from its raw byte count alone (same trick edge-tts uses
# internally), with no ffmpeg/mutagen dependency needed.
MP3_BITRATE_BPS = 48000

app = Flask(__name__)
AUDIO_DIR = tempfile.mkdtemp(prefix="tts_reader_")

HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")
BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)$")
LIST_ITEM_RE = re.compile(r"^[-*+]\s+(.*)$")


def _strip_inline_markdown(text: str) -> str:
    """Cleans a single block's text: links, code ticks, bold/italic markers,
    and stray symbols, so neither the voice nor the display show raw
    Markdown syntax."""
    text = re.sub(r"https?://\S+|www\.\S+", "", text)
    text = re.sub(r"`{1,3}", "", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)  # bold
    text = re.sub(r"\*(.*?)\*", r"\1", text)  # italics
    text = re.sub(r'["\'/\\\[\]#_]', " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_blocks(raw_text: str):
    """
    Splits pasted text into structural blocks (headings, blockquotes, list
    items, paragraphs) so the "now reading" view can mirror the original
    document's layout. Returns a list of {"type": "h1".."h6"|"blockquote"|
    "li"|"p", "text": <cleaned prose for this block>}.
    """
    lines = raw_text.replace("\r\n", "\n").split("\n")
    blocks = []
    para_buf = []

    def flush_para():
        if para_buf:
            cleaned = _strip_inline_markdown(" ".join(para_buf))
            if cleaned:
                blocks.append({"type": "p", "text": cleaned})
            para_buf.clear()

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        if not stripped:
            flush_para()
            i += 1
            continue

        h = HEADER_RE.match(stripped)
        if h:
            flush_para()
            level = min(len(h.group(1)), 6)
            cleaned = _strip_inline_markdown(h.group(2))
            if cleaned:
                blocks.append({"type": f"h{level}", "text": cleaned})
            i += 1
            continue

        bq = BLOCKQUOTE_RE.match(stripped)
        if bq:
            flush_para()
            bq_lines = [bq.group(1)]
            i += 1
            while i < len(lines) and BLOCKQUOTE_RE.match(lines[i].strip()):
                bq_lines.append(BLOCKQUOTE_RE.match(lines[i].strip()).group(1))
                i += 1
            cleaned = _strip_inline_markdown(" ".join(bq_lines))
            if cleaned:
                blocks.append({"type": "blockquote", "text": cleaned})
            continue

        li = LIST_ITEM_RE.match(stripped)
        if li:
            flush_para()
            cleaned = _strip_inline_markdown(li.group(1))
            if cleaned:
                blocks.append({"type": "li", "text": cleaned})
            i += 1
            continue

        para_buf.append(stripped)
        i += 1

    flush_para()
    return blocks


async def synthesize_block(text: str, rate: str, voice: str):
    """Synthesizes ONE block's text and returns (audio_bytes, words), where
    words' start/end times are relative to this block's own audio (each
    edge-tts call starts its own timeline at 0)."""
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate, boundary="WordBoundary")
    audio_chunks = []
    words = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.append(chunk["data"])
        elif chunk["type"] == "WordBoundary":
            start = chunk["offset"] / 10_000_000  # 100ns units -> seconds
            duration = chunk["duration"] / 10_000_000
            words.append({"word": chunk["text"], "start": start, "end": start + duration})
    return b"".join(audio_chunks), words


async def synthesize_document(blocks, out_path: str, rate: str, voice: str):
    """
    Synthesizes each block separately, stitches the raw audio bytes together
    (MP3 frames from the same fixed codec/bitrate concatenate cleanly), and
    shifts each block's word timestamps forward by the exact running
    duration so far — computed from raw audio byte counts, not guesswork.
    Returns (all_words, block_meta) where block_meta maps each block to the
    slice of all_words it owns, with no alignment guessing involved.
    """
    all_words = []
    block_meta = []
    elapsed = 0.0

    with open(out_path, "wb") as out_f:
        for b in blocks:
            text = b["text"]
            if not re.search(r"[.!?]$", text):
                text = text + "."

            audio_bytes, words = await synthesize_block(text, rate, voice)
            if not audio_bytes:
                continue
            out_f.write(audio_bytes)

            start_idx = len(all_words)
            for w in words:
                all_words.append(
                    {"word": w["word"], "start": w["start"] + elapsed, "end": w["end"] + elapsed}
                )
            block_meta.append({"type": b["type"], "start": start_idx, "count": len(words)})

            elapsed += (len(audio_bytes) * 8) / MP3_BITRATE_BPS

    return all_words, block_meta


@app.route("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")


@app.route("/generate", methods=["POST"])
def generate():
    payload = request.get_json(silent=True) or {}
    raw_text = payload.get("text", "")

    blocks = parse_blocks(raw_text)
    if not blocks:
        return jsonify({"error": "No readable text provided."}), 400

    rate = payload.get("rate", DEFAULT_RATE)
    if not RATE_PATTERN.match(rate):
        rate = DEFAULT_RATE

    voice = payload.get("voice", DEFAULT_VOICE)
    if voice not in VOICES:
        voice = DEFAULT_VOICE

    file_id = uuid.uuid4().hex
    out_path = os.path.join(AUDIO_DIR, f"{file_id}.mp3")

    # Flask's dev server (threaded=True below) runs each request in its own
    # worker thread with no event loop of its own, so a plain asyncio.run()
    # here is safe.
    words, block_meta = asyncio.run(synthesize_document(blocks, out_path, rate, voice))

    return jsonify({"audio_url": f"/audio/{file_id}.mp3", "words": words, "blocks": block_meta})


@app.route("/audio/<filename>")
def audio(filename):
    return send_from_directory(AUDIO_DIR, filename, mimetype="audio/mpeg")


INDEX_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Natural TTS Reader</title>
<style>
  /* =====================================================================
     COLORS — edit values here to restyle the whole app. Nothing else in
     the stylesheet below hardcodes a color; it all reads from these.
     ===================================================================== */
  :root {
    --bg: #0f1115;              /* page background */
    --panel-bg: #1b1e24;        /* textarea / reading box background */
    --panel-border: #2a2d35;    /* borders around those boxes */
    --text: #e6e6e6;            /* main text color */
    --text-muted: #9098a8;      /* subtitle / status text */
    --label-color: #ff8a8a;     /* "1. YOUR TEXT" / "2. NOW READING" labels */

    --accent: #d90429;          /* Generate button + highlighted word + blockquote border */
    --accent-text: #ffffff;     /* text color on top of --accent */

    --secondary-btn-bg: #2a2d35;     /* Pause/Restart button background */
    --secondary-btn-hover: #353945;  /* Pause/Restart hover */

    --word-hover-bg: #3a1c22;   /* background when hovering a word before playback reaches it */
  }

  body { font-family: system-ui, -apple-system, sans-serif; max-width: 820px;
         margin: 40px auto; padding: 0 20px; background: var(--bg); color: var(--text); }
  h1.title { font-size: 1.35rem; font-weight: 600; }
  p.sub { color: var(--text-muted); margin-top: -8px; font-size: 0.9rem; }
  textarea { width: 100%; min-height: 150px; padding: 12px; font-size: 1rem;
             border-radius: 8px; border: 1px solid var(--panel-border); background: var(--panel-bg);
             color: var(--text); box-sizing:border-box; resize: vertical; }
  .controls { margin: 14px 0; display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  button { padding: 9px 18px; border-radius: 6px; border: none; background: var(--accent);
           color: var(--accent-text); cursor:pointer; font-size:0.95rem; font-weight: 500; }
  button:disabled { opacity:0.35; cursor:not-allowed; }
  button.secondary { background: var(--secondary-btn-bg); color: var(--text); }
  button.secondary:hover:not(:disabled) { background: var(--secondary-btn-hover); }
  #status { margin: 4px 0 12px; color: var(--text-muted); font-size:0.88rem; min-height: 1.2em; }
  .section-label { font-size: 0.85rem; font-weight: 600; color: var(--label-color); text-transform: uppercase;
                    letter-spacing: 0.03em; margin: 18px 0 6px; }
  #reader { line-height: 2.1; font-size: 1.15rem; padding: 18px; background: var(--panel-bg);
            border-radius: 10px; max-height: 460px; overflow-y: auto; border: 1px solid var(--panel-border); }
  #reader h1, #reader h2, #reader h3, #reader h4, #reader h5, #reader h6 {
    margin: 18px 0 8px; color: var(--text); line-height:1.4;
  }
  #reader h1:first-child, #reader h2:first-child, #reader h3:first-child { margin-top: 0; }
  #reader h1 { font-size: 1.5rem; }
  #reader h2 { font-size: 1.32rem; }
  #reader h3 { font-size: 1.2rem; }
  #reader h4, #reader h5, #reader h6 { font-size: 1.08rem; }
  #reader p { margin: 10px 0; }
  #reader blockquote { margin: 10px 0; padding: 4px 16px; border-left: 3px solid var(--accent);
                        color: var(--text-muted); }
  #reader ul { margin: 8px 0; padding-left: 26px; }
  #reader li { margin: 4px 0; }
  .word { cursor: pointer; padding: 1px 3px; border-radius: 4px; }
  .word:hover { background: var(--word-hover-bg); }
  .word.active { background: var(--accent); color: var(--accent-text); }
  .hint { color: var(--text-muted); font-size:0.8rem; margin-top: 10px; opacity: 0.8; }
  select { padding: 8px 10px; border-radius: 6px; border: 1px solid var(--panel-border);
           background: var(--panel-bg); color: var(--text); font-size:0.9rem; }
  label { font-size: 0.85rem; color: var(--text-muted); }
</style>
</head>
<body>
  <h1 class="title">Natural TTS Reader</h1>
  <p class="sub">Paste text, generate once, then click any word to jump — pause and resume pick up exactly where you left off.</p>

  <div class="section-label">1. Your text (edit here)</div>
  <textarea id="textInput" placeholder="Paste an article, notes, or anything you want read aloud..."></textarea>

  <div class="controls">
    <button id="genBtn">Generate &amp; Play</button>
    <button id="playPauseBtn" class="secondary" disabled>Pause</button>
    <button id="restartBtn" class="secondary" disabled>Restart</button>
    <label for="rateSelect">Speed:</label>
    <select id="rateSelect">
      <option value="-30%">Slowest</option>
      <option value="-20%">Slower</option>
      <option value="-15%" selected>Slow (default)</option>
      <option value="-10%">Slightly slow</option>
      <option value="+0%">Normal</option>
      <option value="+10%">Faster</option>
    </select>
    <label for="voiceSelect">Voice:</label>
    <select id="voiceSelect">
      <option value="en-US-AriaNeural" selected>Aria (US, female)</option>
      <option value="en-US-GuyNeural">Guy (US, male)</option>
      <option value="en-US-JennyNeural">Jenny (US, female)</option>
      <option value="en-GB-SoniaNeural">Sonia (UK, female)</option>
      <option value="en-GB-RyanNeural">Ryan (UK, male)</option>
      <option value="en-AU-NatashaNeural">Natasha (Australian, female)</option>
      <option value="en-IN-NeerjaNeural">Neerja (Indian English, female)</option>
    </select>
  </div>

  <div id="status"></div>
  <div class="section-label">2. Now reading — click any word to jump here</div>
  <div id="reader"></div>
  <audio id="player"></audio>

  <p class="hint">Tip: Space bar toggles pause/resume while this tab is focused. Playback keeps going even if you switch to another tab or app.</p>

<script>
const textInput = document.getElementById('textInput');
const genBtn = document.getElementById('genBtn');
const playPauseBtn = document.getElementById('playPauseBtn');
const restartBtn = document.getElementById('restartBtn');
const rateSelect = document.getElementById('rateSelect');
const voiceSelect = document.getElementById('voiceSelect');
const statusEl = document.getElementById('status');
const reader = document.getElementById('reader');
const player = document.getElementById('player');

let words = [];
let wordSpans = [];
let activeIndex = -1;
let highlightLoopRunning = false;

genBtn.addEventListener('click', async () => {
  const text = textInput.value.trim();
  if (!text) { statusEl.textContent = "Paste some text first."; return; }

  genBtn.disabled = true;
  statusEl.textContent = "Generating natural speech... (a moment for longer text)";
  reader.innerHTML = "";

  try {
    const res = await fetch('/generate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text, rate: rateSelect.value, voice: voiceSelect.value})
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Generation failed');
    }
    const data = await res.json();

    words = data.words;
    activeIndex = -1;

    if (data.blocks) {
      renderStructured(data.blocks);
    } else {
      renderFlat();
    }
    reader.scrollIntoView({behavior: 'smooth', block: 'start'});

    player.src = data.audio_url;
    await player.play();
    playPauseBtn.disabled = false;
    restartBtn.disabled = false;
    playPauseBtn.textContent = "Pause";
    statusEl.textContent = "Reading...";
  } catch (err) {
    statusEl.textContent = "Error: " + err.message;
  } finally {
    genBtn.disabled = false;
  }
});

function makeWordSpan(i) {
  const w = words[i];
  const span = document.createElement('span');
  span.className = 'word';
  span.textContent = w.word + ' ';
  span.addEventListener('click', () => jumpTo(i));
  wordSpans[i] = span;
  return span;
}

function renderStructured(blocks) {
  reader.innerHTML = "";
  wordSpans = [];
  let currentList = null;

  blocks.forEach(b => {
    let container;
    if (b.type === 'li') {
      if (!currentList) {
        currentList = document.createElement('ul');
        reader.appendChild(currentList);
      }
      container = document.createElement('li');
      currentList.appendChild(container);
    } else {
      currentList = null;
      const tag = b.type === 'blockquote' ? 'blockquote' : /^h[1-6]$/.test(b.type) ? b.type : 'p';
      container = document.createElement(tag);
      reader.appendChild(container);
    }
    for (let i = b.start; i < b.start + b.count && i < words.length; i++) {
      container.appendChild(makeWordSpan(i));
    }
  });
}

function renderFlat() {
  reader.innerHTML = "";
  wordSpans = [];
  const p = document.createElement('p');
  reader.appendChild(p);
  for (let i = 0; i < words.length; i++) {
    p.appendChild(makeWordSpan(i));
  }
}

function jumpTo(i) {
  if (!words[i]) return;
  player.currentTime = words[i].start;
  setActiveWord(i);
  if (player.paused) player.play();
  playPauseBtn.textContent = "Pause";
  statusEl.textContent = "Reading...";
}

function setActiveWord(idx) {
  if (idx === activeIndex) return;
  if (activeIndex !== -1 && wordSpans[activeIndex]) wordSpans[activeIndex].classList.remove('active');
  if (wordSpans[idx]) {
    wordSpans[idx].classList.add('active');
    wordSpans[idx].scrollIntoView({block: 'nearest', behavior: 'smooth'});
  }
  activeIndex = idx;
}

function findWordIndex(t) {
  let idx = -1;
  for (let i = 0; i < words.length; i++) {
    if (words[i].start <= t) idx = i; else break;
  }
  return idx;
}

function highlightLoop() {
  if (player.paused || player.ended) { highlightLoopRunning = false; return; }
  const idx = findWordIndex(player.currentTime);
  if (idx !== -1) setActiveWord(idx);
  requestAnimationFrame(highlightLoop);
}

function ensureHighlightLoop() {
  if (!highlightLoopRunning) {
    highlightLoopRunning = true;
    requestAnimationFrame(highlightLoop);
  }
}

player.addEventListener('play', ensureHighlightLoop);

player.addEventListener('ended', () => {
  statusEl.textContent = "Finished reading.";
  playPauseBtn.textContent = "Play";
});

playPauseBtn.addEventListener('click', () => {
  if (player.paused) {
    player.play();
    playPauseBtn.textContent = "Pause";
    statusEl.textContent = "Reading...";
  } else {
    player.pause();
    playPauseBtn.textContent = "Play";
    statusEl.textContent = "Paused — resume any time from this exact spot.";
  }
});

restartBtn.addEventListener('click', () => {
  player.currentTime = 0;
  activeIndex = -1;
  player.play();
  playPauseBtn.textContent = "Pause";
  statusEl.textContent = "Reading...";
});

document.addEventListener('keydown', (e) => {
  if (e.code === 'Space' && document.activeElement !== textInput) {
    e.preventDefault();
    playPauseBtn.click();
  }
});
</script>
</body>
</html>
"""

def open_browser():
    """Opens the default browser once the server has had a moment to start."""
    webbrowser.open_new("http://127.0.0.1:5000/")


if __name__ == "__main__":
    # Give Flask a moment to bind the port before the browser tries to load it.
    Timer(1.2, open_browser).start()
    app.run(debug=False, threaded=True, port=5000)