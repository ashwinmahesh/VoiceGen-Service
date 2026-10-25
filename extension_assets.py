"""Explicit, pinned extension downloads. Serving never accesses the network."""
import hashlib
from pathlib import Path
import urllib.request
from uuid import uuid4
from config import EXTENSION_DIR, EXTENSION_SPECS, prepare_storage
from assets import verify_spec


def download_extensions():
    prepare_storage()
    for key,spec in EXTENSION_SPECS.items():
        folder=EXTENSION_DIR/spec['subdir']
        folder.mkdir(parents=True,exist_ok=True)
        for item in spec['files']:
            target=folder/item['path']
            if target.is_file():
                blob=target.read_bytes()
                digest=hashlib.sha1(f'blob {len(blob)}\0'.encode()+blob).hexdigest()
                if len(blob)==item['size'] and digest==item['git_sha1']:continue
            target.parent.mkdir(parents=True,exist_ok=True)
            temporary=target.with_name('.'+target.name+'.'+uuid4().hex+'.part')
            try:
                with urllib.request.urlopen(spec['base_url']+item['path'],timeout=60) as response,temporary.open('wb') as stream:
                    downloaded=0
                    while chunk:=response.read(1024*1024):
                        downloaded+=len(chunk)
                        if downloaded>item['size']:raise RuntimeError('Extension exceeds pinned file size')
                        stream.write(chunk)
                blob=temporary.read_bytes()
                digest=hashlib.sha1(f'blob {len(blob)}\0'.encode()+blob).hexdigest()
                if len(blob)!=item['size'] or digest!=item['git_sha1']:
                    raise RuntimeError('Extension checksum mismatch: '+item['path'])
                temporary.replace(target)
            finally:temporary.unlink(missing_ok=True)
        errors=verify_spec(spec,folder,key,full=True)
        if errors:raise RuntimeError('; '.join(errors))
        print(f'Verified extension {key}@{spec["revision"]}',flush=True)


if __name__=='__main__':download_extensions()
