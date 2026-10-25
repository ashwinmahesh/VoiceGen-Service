"""Pinned asset verification; full checksums at setup, stat checks at runtime."""
import hashlib
import json
from config import MODEL_DIR, MODEL_SPECS, EXTENSION_DIR, EXTENSION_SPECS
from voice_catalog import atomic_json


def verify_assets(key, full=False):
    return verify_spec(MODEL_SPECS[key], MODEL_DIR/MODEL_SPECS[key]['subdir'], key, full)


def verify_spec(spec, folder, key, full=False):
    errors, stamps = [], {}
    for item in spec['files']:
        path = folder/item['path']
        try:
            stat = path.stat()
            if stat.st_size != item['size']:
                raise ValueError('wrong size')
            if path.suffix == '.json':
                json.loads(path.read_text())
            if full:
                digest = hashlib.sha256() if 'sha256' in item else hashlib.sha1()
                if 'sha256' not in item:
                    digest.update(f'blob {stat.st_size}\0'.encode())
                with path.open('rb') as stream:
                    while block := stream.read(8*1024*1024):
                        digest.update(block)
                if digest.hexdigest() != item.get('sha256', item.get('git_sha1')):
                    raise ValueError('checksum mismatch')
            stamps[item['path']] = [stat.st_size, stat.st_mtime_ns]
        except (OSError, ValueError) as exc:
            errors.append(f'{key}/{item["path"]}: {exc}')
    if not errors:
        manifest = folder/'verified.json'
        if full:
            atomic_json(manifest, {('repo' if 'repo' in spec else 'source'):spec.get('repo',spec.get('source')), 'revision':spec['revision'], 'files':stamps})
        else:
            try:
                saved = json.loads(manifest.read_text())
                if saved != {('repo' if 'repo' in spec else 'source'):spec.get('repo',spec.get('source')), 'revision':spec['revision'], 'files':stamps}:
                    errors.append(f'{key}: assets changed since verification')
            except (OSError, ValueError):
                errors.append(f'{key}: assets have not been checksum verified')
    if full and errors:
        (folder/'verified.json').unlink(missing_ok=True)
    return errors


def all_errors(full=False):
    return ([error for key in MODEL_SPECS for error in verify_assets(key,full=full)] +
            [error for key,spec in EXTENSION_SPECS.items() for error in verify_spec(spec,EXTENSION_DIR/spec['subdir'],key,full=full)])
