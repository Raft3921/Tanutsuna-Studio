"""Finalize separate recordings and run local speech recognition after capture."""
from pathlib import Path
import json
import os
import sys
import tempfile
import imageio_ffmpeg
from .jobs import run_command, ffmpeg_executable
from .takes import audio_filter, write_take, publish_take, discard_pending
from .workspace import atomic_text
from .avatar_offline import AVATAR_FILE,AVATAR_SIZE,AVATAR_VERSION


def metadata(path):
    reader = imageio_ffmpeg.read_frames(str(path))
    try: return next(reader)
    finally: reader.close()


def finalize(folder, take, cancel, render_avatar=True):
    try:return _finalize(folder,take,cancel,render_avatar)
    except Exception as exc:
        folder = Path(folder)
        if folder.parent.name == '.pending-recordings' and not folder.exists():
            folder = folder.parent.parent/'01-recordings'/folder.name
        if folder.is_dir():
            take['state'] = 'incomplete'
            take.setdefault('warnings', []).append('保存処理を再開できます：'+str(exc))
            write_take(folder, take)
            if (folder/'screen.mp4').is_file() and (folder/'screen.mp4').stat().st_size >= 128:
                publish_take(folder)
            else: discard_pending(folder)
        raise


def _finalize(folder, take, cancel, render_avatar=True):
    folder = Path(folder)
    # A valid captured file must survive cancellation and every later conversion.
    if (folder/'screen.mp4').is_file() and (folder/'screen.mp4').stat().st_size >= 128:
        folder = publish_take(folder)
    take['state'] = 'processing'; write_take(folder, take)
    video = folder / 'screen.mp4'
    if not video.is_file() or video.stat().st_size < 128:
        raise ValueError('画面映像がありません。収録ログを確認してください。')
    info = metadata(video)
    duration = float(info.get('duration') or take['duration'])
    if duration <= 0: raise ValueError('画面映像の長さを取得できません。')
    if info['size'] != (take.get('width',1920),take.get('height',1080)): raise ValueError('収録の解像度が設定と一致しません。素材を保持しました。')
    if abs(info['fps']-take.get('fps',50)) > .1:
        warning = f'目標{take.get("fps",50)}fpsに対して実測平均{info["fps"]:.2f}fpsでした。PCの負荷と保存先を確認してください。'
        if warning not in take['warnings']: take['warnings'].append(warning)
    for role, stats in take.get('stats', {}).items():
        if stats.get('dropped', 0):
            warning = f'{role}: 処理待ち・欠落が{stats["dropped"]}回ありました。'
            if warning not in take['warnings']: take['warnings'].append(warning)
    take['duration'] = duration
    take['files']['screen'] = video.name
    take.setdefault('media', {})['screen'] =  {'duration': duration, 'width': info['size'][0], 'height': info['size'][1], 'fps': info['fps']}
    origin = take['timestamps'].get('screen', 0)
    for role in ('desktop', 'voice','bgm','sfx'):
        if not take['options'].get(role): continue
        source = next((p for p in [folder / (role+'-source.m4a'), folder / (role+'-source.wav')] if p.is_file() and p.stat().st_size > 44), None)
        if source is None or role not in take['timestamps']:
            warning = f'{role}: 入力サンプルが得られませんでした。音声素材は作成していません。'
            if warning not in take['warnings']: take['warnings'].append(warning)
            continue
        temp, output = folder / (role+'.partial.mp3'), folder / (role+'.mp3')
        offset = take['timestamps'][role] - origin
        run_command([ffmpeg_executable(), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
            '-threads', '1', '-i', source, '-af', audio_filter(offset, duration), '-c:a', 'libmp3lame',
            '-b:a', '128k' if role == 'voice' else '192k', '-ar', '48000', '-threads', '1', temp], folder / 'processing.log', cancel)
        os.replace(temp, output)
        take['files'][role] = output.name
        write_take(folder, take)
    # Keep raw inputs for recovery. Never delete the only copy after a failed conversion.
    write_take(folder, take)
    if take['options'].get('avatar') and 'vtuber' not in take.get('deleted_roles',[]) and (folder / 'vtuber-motion.jsonl').is_file():
        take['avatar_pending'] = not (folder/AVATAR_FILE).is_file()
        write_take(folder, take)
        if render_avatar:
            try: render_avatar_take(folder, take, cancel)
            except Exception as exc:
                take['avatar_pending'] = True
                take['avatar_error'] = str(exc)
                take['warnings'].append('V体映像を再開してください：'+str(exc))
    take['state'] = 'ready'; write_take(folder, take)
    publish_take(folder)
    return take


def render_avatar_take(folder, take, cancel, force=False):
    folder = Path(folder)
    if 'vtuber' in take.get('deleted_roles',[]):
        take['avatar_pending']=False
        return take
    previous=take.get('files',{}).get('vtuber')
    if force or not (folder/AVATAR_FILE).is_file():
        if getattr(sys, 'frozen', False): command = [sys.executable, '--studio-worker', 'avatar', str(folder)]
        else:
            root = Path(__file__).resolve().parents[1]
            entry = root / ('run.py' if (root/'run.py').is_file() else 'launch.py')
            command = [sys.executable, str(entry), '--studio-worker', 'avatar', str(folder)]
        run_command(command, folder / 'avatar-render.log', cancel)
    output = folder/AVATAR_FILE
    if not output.is_file() or output.stat().st_size < 128:
        raise ValueError('V体映像が完成していません。動きデータを保持しました。再開できます。')
    info = metadata(output)
    if info['size'] != (AVATAR_SIZE,AVATAR_SIZE) or abs(info['fps']-take.get('fps',50)) > .1 or abs(float(info.get('duration',0))-take['duration']) > .08:
        take['avatar_pending'] = True
        write_take(folder,take)
        raise ValueError('V体映像の長さ・サイズが収録と一致しません。作り直しで復旧できます。')
    if previous and previous!=AVATAR_FILE:take.setdefault('replaced_files',{})['vtuber']=previous
    take['files']['vtuber'] = AVATAR_FILE
    take.setdefault('media',{})['vtuber']={'duration':take['duration'],'width':AVATAR_SIZE,'height':AVATAR_SIZE,
        'fps':take.get('fps',50),'alpha':True,'codec':'png','renderVersion':AVATAR_VERSION}
    take['avatar_pending'] = False
    take.pop('avatar_error', None)
    take['warnings'] = [w for w in take.get('warnings',[]) if not w.startswith('V体映像を再開してください：')]
    write_take(folder, take)
    return take


def speech_tools():
    base = Path(__file__).parent
    engine = base / 'native' / ('whisper-win/whisper-cli.exe' if sys.platform == 'win32' else 'whisper-mac/whisper-cli')
    model = base / 'assets/speech/ggml-tiny-q5_1.bin'
    return engine, model


def transcribe(folder, take, cancel):
    folder = Path(folder)
    voice = folder / take['files'].get('voice', 'voice.mp3')
    if not voice.is_file(): raise ValueError('文字起こし用のマイク録音がありません。')
    engine, model = speech_tools()
    if not engine.is_file() or not model.is_file():
        raise ValueError('ローカル文字起こしエンジンがありません。配布物を再ビルドしてください。')
    wav = folder / 'transcription-input.wav'
    run_command([ffmpeg_executable(), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
        '-threads', '1', '-i', voice, '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', wav], folder/'speech.log', cancel)
    # One CPU thread, greedy decoding, local multilingual quantized tiny model.
    prefix = folder / 'transcript'
    run_command([engine, '-m', model, '-f', wav, '-l', 'ja', '-t', '1', '-bs', '1', '-bo', '1',
        '-otxt', '-osrt', '-of', prefix, '-ng'], folder / 'speech.log', cancel, cwd=engine.parent)
    if not prefix.with_suffix('.txt').is_file(): raise ValueError('文字起こしの出力がありません。')
    take['files']['transcript'] = 'transcript.txt'
    take['files']['subtitles'] = 'transcript.srt'
    write_take(folder, take)
    return prefix.with_suffix('.txt').read_text(encoding='utf-8').strip()


def synthesize(folder, text, executable, preset, cancel):
    folder = Path(folder)
    executable = Path(executable)
    if sys.platform == 'darwin' and executable.suffix == '.app':
        import plistlib
        info = plistlib.loads((executable/'Contents/Info.plist').read_bytes())
        executable = executable/'Contents/MacOS'/info['CFBundleExecutable']
    if not executable.is_file(): raise ValueError('環境設定でAquesTalkPlayer.exeを登録してください。')
    if not text.strip(): raise ValueError('文字起こしを確認し、読み上げる文章を入力してください。')
    # Windows /F takes Shift-JIS; Mac -F takes UTF-8.
    try: encoded = text.replace('\r\n', '\n').replace('\n', '\r\n').encode('cp932' if sys.platform == 'win32' else 'utf-8')
    except UnicodeEncodeError: raise ValueError('ゆっくり音声に使えない文字があります。絵文字などを取り除いてください。')
    script = folder / 'yukkuri-input.txt'
    script.write_bytes(encoded)
    temporary = folder / 'yukkuri.partial.wav'
    flag = '/' if sys.platform == 'win32' else '-'
    args = [executable, flag+'F', str(script.resolve()), flag+'W', str(temporary.resolve())]
    if preset.strip(): args += [flag+'P', preset.strip()]
    run_command(args, folder / 'yukkuri.log', cancel, cwd=executable.parent)
    if not temporary.is_file() or temporary.stat().st_size <= 44: raise ValueError('AquesTalkPlayerが音声を出力しませんでした。')
    output = folder / 'yukkuri.wav'; os.replace(temporary, output)
    return str(output)
