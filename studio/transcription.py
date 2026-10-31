"""Offline, cancellable Whisper transcription for timeline audio clips."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import urllib.request
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from .audio_tools import ffmpeg_executable
from .jobs import run_command
from .subtitle_library import parse_srt


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
            '-t', str(threads), '-bs', '5', '-bo', '5', '-osrt', '-of', prefix, '-ng'],
            log, cancel, cwd=engine.parent)
        srt = prefix.with_suffix('.srt')
        if not srt.is_file():
            raise ValueError('文字起こし結果を作成できませんでした。')
        cues = parse_srt(srt)
        if not cues:
            raise ValueError('音声から文字を検出できませんでした。')
        offset = timeline_start / rate
        return [(offset + start, offset + end, text) for start, end, text in cues]


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
    result = []
    for start, end, text in cues:
        parts = format_caption(text, max_chars, max_words, line_break, chars_per_line)
        weights = [max(1, len(_graphemes(part.replace('\n', '').replace(' ', '')))) for part in parts]
        total = sum(weights)
        cursor = start
        for index, (part, weight) in enumerate(zip(parts, weights)):
            stop = end if index == len(parts) - 1 else start + (end-start) * sum(weights[:index+1]) / total
            if stop - cursor < 1 / rate and index < len(parts)-1:
                continue
            result.append((cursor, stop, part))
            cursor = stop
    return result
