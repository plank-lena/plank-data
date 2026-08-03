"""Build dist/dashboard_pipeline.zip — a self-contained pipeline bundle."""

import zipfile
from pathlib import Path

ROOT = Path(__file__).parent
DIST = ROOT / 'dist'

FILES = [
    (ROOT / 'src' / 'config.py',                        'config.py'),
    (ROOT / 'src' / 'extract.py',                       'extract.py'),
    (ROOT / 'src' / 'compute.py',                       'compute.py'),
    (ROOT / 'src' / 'render.py',                        'render.py'),
    (ROOT / 'src' / 'validate.py',                      'validate.py'),
    (ROOT / 'src' / 'pipeline.py',                      'pipeline.py'),
    (ROOT / 'template' / 'dashboard.template.html',     'template/dashboard.template.html'),
]


def build():
    DIST.mkdir(exist_ok=True)
    zip_path = DIST / 'dashboard_pipeline.zip'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for src, arcname in FILES:
            if not src.exists():
                raise FileNotFoundError(f'Missing: {src}')
            zf.write(src, arcname)
    print(f'Built: {zip_path}')
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            info = zf.getinfo(name)
            print(f'  {name}  ({info.compress_size:,} bytes compressed)')
    return zip_path


if __name__ == '__main__':
    build()
