"""Separate offline avatar process: recording only stores small pose samples."""
import json
import math
import os
from pathlib import Path
import sys


def render_avatar(folder):
    os.environ['OPENBLAS_NUM_THREADS'] = '1'
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QImage, QColor, QPainter
    from PySide6.QtCore import Qt
    from .avatar_view import SoftwareViewport
    from .avatar_model import AvatarModel
    from .tracking import Pose
    from .avatar_gl import configure_opengl
    from .avatar_offline import OfflineAvatar,downsample,AVATAR_SIZE,AVATAR_FILE
    from cutraft.media_cache import ffmpeg_executable, process_options
    from .jobs import lower_priority
    import subprocess
    lower_priority()
    configure_opengl()
    app = QApplication.instance() or QApplication([])
    folder = Path(folder)
    take = json.loads((folder / 'take.json').read_text(encoding='utf-8'))
    # Iterate the motion log instead of loading hours of pose data into RAM.
    motion = (folder / 'vtuber-motion.jsonl').open(encoding='utf-8')
    header = json.loads(next(motion))
    current = {'time': 0, 'pose': Pose().__dict__}
    def next_pose():
        for line in motion:
            row = json.loads(line)
            if 'pose' in row: return row
        return None
    following = next_pose()
    model=AvatarModel();renderer=None;widget=None
    try:
        renderer=OfflineAvatar(model)
        print('透過GPU描画: 2160px → 1080px、4x MSAA',flush=True)
    except Exception as exc:
        print('互換描画: '+str(exc),flush=True)
        widget=SoftwareViewport(model);widget.resize(AVATAR_SIZE*2,AVATAR_SIZE*2)
        widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground,True)
        widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground,True)
        widget.setAutoFillBackground(False)
        widget.background=QColor(0,0,0,0)
    fps=take.get('fps',50)
    temporary = folder / 'vtuber.partial.mov'
    command = [ffmpeg_executable(), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
        '-threads', '1', '-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', f'{AVATAR_SIZE}x{AVATAR_SIZE}', '-r', str(fps), '-i', 'pipe:0',
        '-an', '-c:v', 'png', '-compression_level', '3', '-pred', 'mixed', '-pix_fmt', 'rgba', '-threads', '1',
        '-movflags', '+faststart', str(temporary)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, **process_options())
    try:
        duration = take['duration']
        for frame in range(max(1, math.ceil(duration*fps))):
            now = frame / fps
            while following and following['time'] <= now:
                current = following; following = next_pose()
            pose = dict(current['pose'])
            if following:
                factor = max(0, min(1, (now-current['time'])/max(.0001, following['time']-current['time'])))
                pose = {k: v+(following['pose'].get(k,v)-v)*factor for k,v in pose.items()}
            if renderer:image=renderer.frame(Pose(**pose),now,header.get('orbit',0))
            else:
                widget.pose,widget.now,widget.orbit=Pose(**pose),now,header.get('orbit',0)
                image=QImage(AVATAR_SIZE*2,AVATAR_SIZE*2,QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent);widget.render(image)
                image=downsample(image,AVATAR_SIZE)
            process.stdin.write(image.constBits().tobytes())
            if frame % fps == 0: print(f'{frame/fps:.1f}/{duration:.1f}', flush=True)
        process.stdin.close()
        if process.wait(): raise RuntimeError('VTuber映像を保存できませんでした。')
        os.replace(temporary, folder / AVATAR_FILE)
    finally:
        motion.close()
        if process.poll() is None: process.kill(); process.wait()
        if renderer:renderer.close()
    return 0


def main(args):
    if len(args) != 2 or args[0] != 'avatar': raise ValueError('Unknown worker job')
    return render_avatar(args[1])
