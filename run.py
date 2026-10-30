from pathlib import Path
import sys

# Development uses a local snapshot; the Windows distribution bundles this same package.
sys.path.insert(0, str(Path(__file__).parent / 'vendor/cutraft-win/app'))
if '--studio-worker' in sys.argv:
    from studio.worker import main
    sys.exit(main(sys.argv[sys.argv.index('--studio-worker')+1:]))
from studio.main import main
sys.exit(main())
