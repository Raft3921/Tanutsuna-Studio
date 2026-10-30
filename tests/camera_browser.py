from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from studio.tracking import TrackerServer
server=TrackerServer()
try:
    server.start()
    subprocess.run(['node','tests/camera_browser.cjs',server.url],cwd=ROOT,check=True,timeout=90)
    assert server.state.generation>=2
    print('Native localhost bridge received actual MediaPipe camera lifecycle messages.')
finally:server.stop()
