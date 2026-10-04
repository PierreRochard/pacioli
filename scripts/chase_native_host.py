#!/usr/bin/python3 -u
"""Firefox native messaging to a private, same-user Unix socket. No HTTP listener."""
import json
import os
from pathlib import Path
import select
import socket
import stat
import struct
import sys
import time
import uuid

MAX_MESSAGE = 8 * 1024 * 1024


def socket_path():
    return Path('/run/user') / str(os.getuid()) / 'pacioli-chase-extension.sock'


def read_exact(source, size):
    result = bytearray()
    while len(result) < size:
        chunk = source.read(size - len(result))
        if not chunk:
            raise EOFError()
        result.extend(chunk)
    return bytes(result)


def read_message(source, maximum=MAX_MESSAGE):
    length, = struct.unpack('=I', read_exact(source, 4))
    if not 0 < length <= maximum:
        raise ValueError('message_size_invalid')
    result = json.loads(read_exact(source, length))
    if not isinstance(result, dict):
        raise ValueError('message_invalid')
    return result


def write_message(target, value):
    data = json.dumps(value, separators=(',', ':')).encode()
    if len(data) > MAX_MESSAGE:
        raise ValueError('message_size_invalid')
    # SocketIO.write may accept only part of a large screenshot response.
    # Buffered stdout usually hides this; the unbuffered Unix peer does not.
    remaining = memoryview(struct.pack('=I', len(data)) + data)
    while remaining:
        written = target.write(remaining)
        if written is None or written <= 0:
            raise OSError('message_write_failed')
        remaining = remaining[written:]
    target.flush()


def main():
    os.umask(0o077)
    path = socket_path()
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise SystemExit('Unsafe bridge socket')
        probe = socket.socket(socket.AF_UNIX)
        try:
            probe.connect(str(path))
        except (ConnectionRefusedError, FileNotFoundError):
            path.unlink(missing_ok=True)
        else:
            raise SystemExit('A bridge is already connected')
        finally:
            probe.close()
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(path)); os.chmod(path, 0o600); listener.listen(1)
        try:
            while True:
                ready, _, _ = select.select([listener, sys.stdin.buffer], [], [])
                if sys.stdin.buffer in ready:
                    # EOF closes the host; unsolicited replies never become commands.
                    read_message(sys.stdin.buffer)
                    continue
                peer, _ = listener.accept()
                with peer:
                    _, uid, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if uid != os.getuid():
                        continue
                    peer.settimeout(65)
                    with peer.makefile('rwb', buffering=0) as stream:
                        try:
                            command = read_message(stream, 65536)
                            if command.get('op') not in ('ping','prepare','observe','act','download','release','diagnostics'):
                                raise ValueError('unsupported_operation')
                            command['id'] = uuid.uuid4().hex
                            write_message(sys.stdout.buffer, command)
                            deadline = time.monotonic() + 60
                            while True:
                                remaining = deadline - time.monotonic()
                                if remaining <= 0 or not select.select([sys.stdin.buffer], [], [], remaining)[0]:
                                    raise TimeoutError()
                                response = read_message(sys.stdin.buffer)
                                if response.get('id') == command['id']:
                                    write_message(stream, response)
                                    break
                        except (EOFError, OSError, ValueError, TimeoutError):
                            # An action with unknown outcome is never automatically retried.
                            try: write_message(stream, {'error': 'bridge_connection_lost'})
                            except OSError: pass
        finally:
            path.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except (EOFError, BrokenPipeError, KeyboardInterrupt):
        pass
