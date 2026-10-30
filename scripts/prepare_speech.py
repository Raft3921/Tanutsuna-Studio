"""Reproduce the local speech engine from a pinned upstream release."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
ROOT=Path(__file__).resolve().parents[1]
VERSION='v1.9.2'
BUILD=ROOT/'build/whisper'

def fetch(url,path):
    path.parent.mkdir(parents=True,exist_ok=True)
    if not path.is_file():
        temporary=path.with_suffix(path.suffix+'.download')
        urllib.request.urlretrieve(url,temporary);temporary.replace(path)

def main():
    urls={
        'windows.zip':f'https://github.com/ggml-org/whisper.cpp/releases/download/{VERSION}/whisper-bin-x64.zip',
        'source.tar.gz':f'https://github.com/ggml-org/whisper.cpp/archive/refs/tags/{VERSION}.tar.gz',
        'model.bin':'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny-q5_1.bin'}
    speech=ROOT/'studio/assets/speech';speech.mkdir(parents=True,exist_ok=True)
    files={name:(speech/'ggml-tiny-q5_1.bin' if name=='model.bin' else BUILD/name) for name in urls}
    for name,url in urls.items():fetch(url,files[name])
    win=ROOT/'studio/native/whisper-win';win.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(files['windows.zip']) as z:
        for name in z.namelist():
            if name.endswith('.dll') or Path(name).name=='whisper-cli.exe':(win/Path(name).name).write_bytes(z.read(name))
    with tarfile.open(files['source.tar.gz']) as t:t.extractall(BUILD,filter='data')
    source=BUILD/'whisper.cpp-1.9.2'
    shutil.copy2(source/'LICENSE',speech/'WHISPER-LICENSE.txt')
    if sys.platform=='darwin':
        subprocess.run(['cmake','-S',str(source),'-B',str(BUILD/'mac'),'-DCMAKE_BUILD_TYPE=Release','-DBUILD_SHARED_LIBS=OFF','-DWHISPER_BUILD_TESTS=OFF','-DGGML_METAL=ON'],check=True)
        subprocess.run(['cmake','--build',str(BUILD/'mac'),'--target','whisper-cli','-j','2'],check=True)
        mac=ROOT/'studio/native/whisper-mac';mac.mkdir(parents=True,exist_ok=True)
        shutil.copy2(BUILD/'mac/bin/whisper-cli',mac/'whisper-cli')
    manifest={'version':VERSION,'model':'tiny-q5_1 multilingual','sources':{name:{'url':url,'sha256':hashlib.sha256(files[name].read_bytes()).hexdigest()} for name,url in urls.items()}}
    (speech/'source-manifest.json').write_text(json.dumps(manifest,indent=2))
if __name__=='__main__':main()
