"""Private transport to the toolbar-selected Firefox tab."""
import json
import os
from pathlib import Path
import socket
import stat
import struct
import re


class LocalChaseError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class FirefoxBridge:
    def __init__(self, heartbeat=lambda: None):
        self.heartbeat = heartbeat
        self.path = Path('/run/user') / str(os.getuid()) / 'pacioli-chase-extension.sock'

    def command(self, op, **kwargs):
        self.heartbeat()
        try:
            info = self.path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise LocalChaseError('bridge_unavailable')
            with socket.socket(socket.AF_UNIX) as sock:
                sock.settimeout(5 if op in ('ping','release') else 65); sock.connect(str(self.path))
                _, uid, _ = struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != os.getuid():
                    raise LocalChaseError('bridge_unavailable')
                encoded = json.dumps(dict(op=op, **kwargs)).encode()
                if len(encoded) > 65536:
                    raise LocalChaseError('invalid_bridge_request')
                sock.sendall(struct.pack('=I', len(encoded)) + encoded)
                def read(size):
                    data = bytearray()
                    while len(data) < size:
                        chunk = sock.recv(min(size-len(data), 65536))
                        if not chunk: raise OSError()
                        data.extend(chunk)
                    return bytes(data)
                length, = struct.unpack('=I', read(4))
                if not 0 < length <= 8 * 1024 * 1024:
                    raise LocalChaseError('invalid_bridge_response')
                response = json.loads(read(length))
        except (OSError, ValueError):
            raise LocalChaseError('bridge_unavailable') from None
        if response.get('error'):
            code = response['error']
            raise LocalChaseError(code if isinstance(code, str) and re.fullmatch('[a-z_]{3,48}', code)
                                  else 'invalid_bridge_response')
        if not isinstance(response.get('result'), dict):
            raise LocalChaseError('invalid_bridge_response')
        self.heartbeat()
        return response['result']


def read_qfx_download(path):
    from pacioli.functions.ofx_file_import import MAX_OFX_BYTES
    path = Path(path)
    if not path.is_absolute() or path.suffix.lower() not in ('.qfx', '.ofx'):
        raise LocalChaseError('invalid_qfx_download')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or not 0 < info.st_size <= MAX_OFX_BYTES:
            raise LocalChaseError('invalid_qfx_download')
        with os.fdopen(descriptor, 'rb', closefd=False) as source:
            content = source.read(MAX_OFX_BYTES+1)
        after = os.fstat(descriptor)
        if len(content) != info.st_size or (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
            raise LocalChaseError('download_changed')
        os.fchmod(descriptor, 0o600)
        return content
    finally:
        os.close(descriptor)
