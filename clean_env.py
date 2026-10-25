"""Remove only validated AppleDouble sidecars that confuse runtime file discovery."""
from pathlib import Path
import sys


def clean_appledouble(root):
    count=0
    for path in Path(root).rglob('._*'):
        if path.is_file():
            with path.open('rb') as stream: signature=stream.read(4)
            if signature==b'\x00\x05\x16\x07':
                path.unlink();count+=1
    return count


if __name__=='__main__':
    root=Path(sys.argv[1] if len(sys.argv)>1 else '.venv')
    print(f'Removed {clean_appledouble(root)} AppleDouble metadata sidecars from {root}.')
