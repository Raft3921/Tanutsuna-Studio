"""Offline, cancellable Whisper transcription for timeline audio clips."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.request
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from .audio_tools import ffmpeg_executable
from .jobs import run_command


MODELS = {
    'small-q5_1': {
        'label': '高精度（約190 MB）',
        'file': 'ggml-small-q5_1.bin',
        'url': 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q5_1.bin',
        'sha256': 'ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb',
    },
    'medium-q5_0': {
        'label': '高精度・重め（約539 MB）',
        'file': 'ggml-medium-q5_0.bin',
        'url': 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium-q5_0.bin',
        'sha256': '19fea4b380c3a618ec4723c3eef2eb785ffba0d0538cf43f8f235e7b3b34220f',
    },
    'large-v3-turbo-q5_0': {
        'label': '最高精度（約574 MB）',
        'file': 'ggml-large-v3-turbo-q5_0.bin',
        'url': 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin',
        'sha256': '394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2',
    },
}


def engine_path():
    base = Path(__file__).parent / 'native'
    name = 'whisper-win/whisper-cli.exe' if os.name == 'nt' else 'whisper-mac/whisper-cli'
    path = base / name
    if not path.is_file():
        raise ValueError('文字起こしエンジンがありません。Studioを最新版に更新してください。')
    return path


def model_folder():
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    root = Path(location) if location else Path.home() / '.tanutsuna-studio'
    folder = root / 'speech' / 'models'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _cancelled(cancel):
    if cancel.is_set():
        raise RuntimeError('処理を中断しました。元の音声は保持しています。')


def ensure_model(name, cancel, progress):
    if name not in MODELS:
        raise ValueError('文字起こしモデルを選び直してください。')
    definition = MODELS[name]
    destination = model_folder() / definition['file']
    if destination.is_file():
        return destination
    partial = destination.with_suffix(destination.suffix + '.part')
    digest = hashlib.sha256()
    try:
        progress(f'{definition["label"]}のモデルを取得しています…')
        with urllib.request.urlopen(definition['url'], timeout=30) as response, partial.open('wb') as output:
            total = int(response.headers.get('Content-Length') or 0)
            received = 0
            while True:
                _cancelled(cancel)
                block = response.read(1024 * 1024)
                if not block:
                    break
                output.write(block)
                digest.update(block)
                received += len(block)
                if total:
                    progress(f'モデル取得中… {received * 100 // total}%')
        _cancelled(cancel)
        if digest.hexdigest() != definition['sha256']:
            raise ValueError('文字起こしモデルの確認に失敗しました。ダウンロードをやり直してください。')
        os.replace(partial, destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return destination


def transcribe_clip(asset_path, source_in, duration, timeline_start, rate, model, language, cancel, progress):
    """Return timeline-second SRT cues from one media clip; all inference stays local."""
    asset_path = Path(asset_path)
    if not asset_path.is_file():
        raise ValueError('選んだクリップの素材ファイルが見つかりません。')
    engine = engine_path()
    with tempfile.TemporaryDirectory(prefix='tanutsuna-transcript-') as temp:
        temp = Path(temp)
        audio = temp / 'audio.wav'
        log = temp / 'transcription.log'
        progress('選んだクリップから音声を取り出しています…')
        try:
            run_command([ffmpeg_executable(), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                '-threads', '1', '-ss', f'{source_in / rate:.9f}', '-i', asset_path,
                '-t', f'{duration / rate:.9f}', '-map', '0:a:0', '-vn', '-ar', '16000', '-ac', '1',
                '-c:a', 'pcm_s16le', audio], log, cancel)
        except RuntimeError:
            details = log.read_text(encoding='utf-8', errors='replace').lower() if log.is_file() else ''
            if 'matches no streams' in details or 'does not contain any stream' in details:
                raise ValueError('このクリップから音声トラックを読み取れません。音声付きの動画または音声クリップを選んでください。')
            raise
        if not audio.is_file() or audio.stat().st_size <= 44:
            raise ValueError('クリップから音声を取り出せませんでした。音声付きの動画・音声クリップを選んでください。')
        model_file = ensure_model(model, cancel, progress)
        prefix = temp / 'result'
        threads = min(4, max(1, (os.cpu_count() or 2) - 1))
        progress('音声をローカルで解析しています…')
        run_command([engine, '-m', model_file, '-f', audio, '-l', language,
            '-t', str(threads), '-bs', '5', '-bo', '5', '-ojf', '-of', prefix, '-ng'],
            log, cancel, cwd=engine.parent)
        result_file = prefix.with_suffix('.json')
        if not result_file.is_file():
            raise ValueError('文字起こし結果を作成できませんでした。')
        cues = parse_whisper_json(result_file)
        if not cues:
            raise ValueError('音声から文字を検出できませんでした。')
        offset = timeline_start / rate
        return [(offset + start, offset + end, text) for start, end, text in cues]


def _json_time_seconds(value):
    if isinstance(value, (int, float)):
        return float(value) / 1000
    if isinstance(value, str):
        match = re.fullmatch(r'(\d+):(\d{2}):(\d{2})[,.](\d+)', value.strip())
        if match:
            hours, minutes, seconds, millis = match.groups()
            return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + float('0.' + millis)
    return None


def parse_whisper_json(path):
    """Read timed Whisper tokens, falling back to segment timing when needed."""
    with Path(path).open(encoding='utf-8') as source:
        document = json.load(source)
    result = []
    for segment in document.get('transcription', []):
        segment_times = segment.get('timestamps') or {}
        segment_offsets = segment.get('offsets') or {}
        segment_start = _json_time_seconds(segment_offsets.get('from'))
        segment_end = _json_time_seconds(segment_offsets.get('to'))
        if segment_start is None:
            segment_start = _json_time_seconds(segment_times.get('from'))
        if segment_end is None:
            segment_end = _json_time_seconds(segment_times.get('to'))
        text = str(segment.get('text') or '')
        tokens = [token for token in (segment.get('tokens') or [])
                  if str(token.get('text') or '').strip() and not str(token.get('text', '')).startswith('<|')]
        if not tokens:
            if segment_start is not None and segment_end is not None and segment_end > segment_start and text.strip():
                result.append((segment_start, segment_end, text))
            continue
        fallback_start = segment_start if segment_start is not None else 0.0
        fallback_end = segment_end if segment_end is not None and segment_end > fallback_start else fallback_start + .01
        weights = [max(1, len(_graphemes(str(token.get('text') or '')))) for token in tokens]
        total = sum(weights)
        cursor = fallback_start
        consumed = 0
        for token, weight in zip(tokens, weights):
            timestamps = token.get('timestamps') or {}
            offsets = token.get('offsets') or {}
            start = _json_time_seconds(offsets.get('from'))
            end = _json_time_seconds(offsets.get('to'))
            if start is None:
                start = _json_time_seconds(timestamps.get('from'))
            if end is None:
                end = _json_time_seconds(timestamps.get('to'))
            if start is None or end is None or end <= start:
                start = cursor
                end = fallback_start + (fallback_end - fallback_start) * ((consumed + weight) / total)
            start = min(max(fallback_start, start), max(fallback_start, fallback_end - .01))
            end = min(fallback_end, max(start + .01, end))
            result.append((start, end, str(token.get('text') or '')))
            cursor = end
            consumed += weight
    return result


def _graphemes(text):
    clusters = []
    for char in text:
        if clusters and (char == '\u200d' or clusters[-1].endswith('\u200d') or
                         char in ('\ufe0e', '\ufe0f') or '\u0300' <= char <= '\u036f'):
            clusters[-1] += char
        else:
            clusters.append(char)
    return clusters


def format_caption(text, max_chars=20, max_words=8, line_break=True, chars_per_line=12):
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        return []
    spaced = bool(re.search(r'\s', text))
    units = re.findall(r'\S+', text) if spaced else _graphemes(text)
    chunks, current, word_count = [], '', 0
    for unit in units:
        candidate = (current + (' ' if current and spaced else '') + unit)
        next_words = word_count + (1 if spaced else len(_graphemes(unit)))
        if current and (len(_graphemes(candidate)) > max_chars or
                        (max_words and spaced and next_words > max_words)):
            chunks.append(current)
            current, word_count = unit, 1 if spaced else len(_graphemes(unit))
        else:
            current, word_count = candidate, next_words
    if current:
        chunks.append(current)
    if not line_break:
        return chunks
    result = []
    for chunk in chunks:
        parts = re.findall(r'\S+', chunk) if re.search(r'\s', chunk) else _graphemes(chunk)
        lines, line = [], ''
        for part in parts:
            candidate = line + (' ' if line and re.search(r'\s', chunk) else '') + part
            if line and len(_graphemes(candidate)) > chars_per_line:
                lines.append(line)
                line = part
            else:
                line = candidate
        if line:
            lines.append(line)
        result.append('\n'.join(lines))
    return result


def split_timed_cues(cues, max_chars, max_words, line_break, chars_per_line, rate):
    max_chars = max(1, int(max_chars))
    max_words = max(0, int(max_words))
    tokens = []
    for start, end, text in cues:
        text = re.sub(r'\s+', ' ', str(text))
        if not text.strip() or end <= start:
            continue
        clusters = _graphemes(text)
        if len(clusters) <= max_chars:
            tokens.append((float(start), float(end), text))
            continue
        weights = [1] * len(clusters)
        total = sum(weights)
        cursor = float(start)
        for index in range(0, len(clusters), max_chars):
            part = ''.join(clusters[index:index + max_chars])
            if not part.strip():
                continue
            through = sum(weights[:index + max_chars])
            stop = float(end) if index + max_chars >= len(clusters) else float(start) + (float(end) - float(start)) * through / total
            tokens.append((cursor, max(cursor + 1 / max(1, rate), stop), part))
            cursor = stop

    result, group = [], []

    def flush():
        if not group:
            return
        text = ''.join(item[2] for item in group).strip()
        if line_break and text:
            text = _wrap_caption(text, max(1, int(chars_per_line)))
        if text:
            result.append((group[0][0], group[-1][1], text))
        group.clear()

    for token in tokens:
        start, end, text = token
        if group and start - group[-1][1] > max(.24, 6 / max(1, rate)):
            flush()
        candidate = ''.join(item[2] for item in group) + text
        spaced_language = bool(re.search(r'\s', candidate.strip()))
        char_count = len(_graphemes(candidate.strip()))
        if spaced_language:
            word_count = len(re.findall(r'\S+', candidate))
        else:
            # Whisper's timed token boundaries are the best available word
            # units for languages such as Japanese that do not use spaces.
            word_count = sum(1 for item in group if item[2].strip()) + (1 if text.strip() else 0)
        if group and (char_count > max_chars or (max_words and word_count > max_words)):
            flush()
        group.append(token)
        if re.search(r'[。！？.!?…][」』）)]*$', text.strip()):
            flush()
    flush()
    return result


def _wrap_caption(text, width):
    if len(_graphemes(text)) <= width:
        return text
    spaced = bool(re.search(r'\s', text))
    parts = re.findall(r'\S+\s*', text) if spaced else _graphemes(text)
    lines, line = [], ''
    for part in parts:
        candidate = line + part
        if line and len(_graphemes(candidate.strip())) > width:
            lines.append(line.strip())
            line = part.lstrip()
        else:
            line = candidate
    if line.strip():
        lines.append(line.strip())
    return '\n'.join(lines)
