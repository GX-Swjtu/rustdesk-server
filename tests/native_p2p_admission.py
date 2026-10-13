"""Run with Python 3 and libsodium: native_p2p_admission.py /absolute/path/to/hbbs.

Uses an isolated network namespace, loopback server and ephemeral database.
Run as root on Linux. The namespace gives signed requests a real routable-source
fixture without changing host routes or accepting loopback in production.
"""
import ctypes as C
import ctypes.util
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import uuid

if not os.environ.get("NATIVE_P2P_TEST_SOURCE"):
    assert os.geteuid() == 0, "isolated network fixture requires root"
    namespace = "np-admission-" + uuid.uuid4().hex[:8]
    subprocess.run(["ip", "netns", "add", namespace], check=True)
    try:
        subprocess.run(["ip", "-n", namespace, "link", "set", "lo", "up"], check=True)
        subprocess.run(["ip", "-n", namespace, "addr", "add", "192.0.2.1/24", "dev", "lo"], check=True)
        env = dict(os.environ, NATIVE_P2P_TEST_SOURCE="192.0.2.1")
        outcome = subprocess.run(["ip", "netns", "exec", namespace, sys.executable, str(pathlib.Path(__file__).resolve()), *sys.argv[1:]], env=env)
    finally:
        subprocess.run(["ip", "netns", "delete", namespace], check=True)
    sys.exit(outcome.returncode)
SOURCE = os.environ["NATIVE_P2P_TEST_SOURCE"]

sodium = C.CDLL(ctypes.util.find_library('sodium'))
sodium.sodium_init()

def varint(n):
    result = bytearray()
    while n >= 128:
        result.append((n & 127) | 128)
        n >>= 7
    result.append(n)
    return bytes(result)

def field(n, value):
    if isinstance(value, int):
        return varint(n << 3) + varint(value)
    if isinstance(value, str):
        value = value.encode()
    return varint(n << 3 | 2) + varint(len(value)) + value

def fields(data):
    offset = 0
    def readvar():
        nonlocal offset
        result = shift = 0
        while True:
            b = data[offset]
            offset += 1
            result |= (b & 127) << shift
            if not b & 128:
                return result
            shift += 7
    result = {}
    while offset < len(data):
        tag = readvar()
        if tag & 7 == 0:
            value = readvar()
        elif tag & 7 == 2:
            n = readvar()
            value = data[offset:offset+n]
            offset += n
        else:
            raise ValueError('unsupported wire type')
        result[tag >> 3] = value
    return result

def keypair(kind):
    pk = C.create_string_buffer(32)
    sk = C.create_string_buffer(64 if kind == 'sign' else 32)
    getattr(sodium, 'crypto_' + kind + '_keypair')(pk, sk)
    return pk.raw, sk

def signed(device, action):
    body = json.dumps(dict(id=device['id'], nonce=list(os.urandom(24)),
                           time=int(time.time()), action=action), separators=(',', ':')).encode()
    message = b'rustdesk-native-udp-punch\0' + body
    signature = C.create_string_buffer(64)
    length = C.c_ulonglong()
    assert sodium.crypto_sign_detached(signature, C.byref(length), message,
                                      C.c_ulonglong(len(message)), device['sk']) == 0
    return json.dumps(dict(body=list(body), signature=list(signature.raw)), separators=(',', ':')).encode()

def exact(sock, n):
    result = b''
    while len(result) < n:
        data = sock.recv(n-len(result))
        if not data:
            raise EOFError('server closed')
        result += data
    return result

def tcp(message):
    header_size = next(n for n in range(1, 5) if len(message) < 1 << (8*n-2))
    packet = (len(message)*4 + header_size-1).to_bytes(header_size, 'little') + message
    with socket.create_connection(('127.0.0.1', 32216), timeout=2, source_address=(SOURCE, 0)) as sock:
        sock.sendall(packet)
        first = exact(sock, 1)
        header = first + exact(sock, first[0] & 3)
        return fields(exact(sock, int.from_bytes(header, 'little') >> 2))

def rpc(device, action):
    reply = fields(tcp(field(1000, field(1, 1) + field(2, signed(device, action))))[1000])
    envelope = json.loads(reply[2])
    return json.loads(bytes(envelope['body']))['reply']

def register(name):
    pk, sk = keypair('sign')
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((SOURCE, 0))
    sock.settimeout(2)
    device = dict(id=name, sk=sk, socket=sock)
    sock.sendto(field(15, field(1, name) + field(2, os.urandom(16)) + field(3, pk)), ('127.0.0.1', 32216))
    assert fields(fields(sock.recv(65535))[16]).get(1, 0) == 0
    advertise(device)
    return device

def advertise(device, version=1, payload=None):
    sock = device['socket']
    if payload is None:
        capability = {'capability': 'native-udp-punch', 'enabled': bool(version)}
        payload = signed(device, {'Advertise': capability})
    packet = field(1, device['id']) + field(1000, version) + field(1001, payload)
    sock.sendto(field(6, packet), ('127.0.0.1', 32216))
    while 7 not in fields(sock.recv(65535)):
        pass  # Consume invitations already queued before the registration response.
    return payload

def begin(caller, peer, key=None):
    pk, _ = keypair('box')
    action = dict(target=peer['id'], box_pk=list(pk))
    if key is not None:
        action['licence_key'] = key
    return rpc(caller, {'Begin': action})

binary = pathlib.Path(sys.argv[1]).resolve(strict=True)
for kind in [socket.SOCK_STREAM, socket.SOCK_DGRAM]:
    with socket.socket(socket.AF_INET, kind) as check:
        if kind == socket.SOCK_STREAM:
            check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        check.bind(('127.0.0.1', 32216))
result = {}
with tempfile.TemporaryDirectory(prefix='rustdesk-native-review-') as directory:
    with open(pathlib.Path(directory) / 'hbbs.log', 'w') as logfile:
        server = subprocess.Popen([str(binary), '-b', '127.0.0.1', '-p', '32216', '-k', '_'],
                                  cwd=directory, stdout=logfile, stderr=subprocess.STDOUT)
        devices = []
        try:
            for _ in range(40):
                assert server.poll() is None, 'isolated hbbs exited before readiness'
                try:
                    with socket.create_connection(('127.0.0.1', 32216), timeout=.1):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                raise TimeoutError('isolated hbbs did not start')
            caller = register('reviewcaller01')
            peer = register('reviewtarget01')
            devices = [caller, peer]
            old_reply = fields(tcp(field(8, field(1, peer['id'])))[11])
            result['legacy_without_server_key_failure'] = old_reply.get(3)
            for name, key in [('missing_key', None), ('wrong_key', 'wrong')]:
                reply = begin(caller, peer, key)
                result[name] = next(iter(reply))
                if 'Ticket' in reply:
                    rpc(caller, {'Cancel': {'session': reply['Ticket']['session']}})
            server_key = (pathlib.Path(directory) / 'id_ed25519.pub').read_text().strip()
            first = begin(caller, peer, server_key)
            result['correct_key'] = next(iter(first))
            result['negotiated_capability'] = first['Ticket']['capability']
            session = first['Ticket']['session']
            result['session_before_unsigned_withdrawal'] = rpc(caller, {'Poll': {'session': session}})
            # An unrelated UDP socket, no device signature and no device key.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as unrelated:
                unrelated.bind(('127.0.0.2', 0))
                unrelated.sendto(field(6, field(1, peer['id'])), ('127.0.0.1', 32216))
            time.sleep(.15)
            result['session_after_unsigned_withdrawal'] = rpc(caller, {'Poll': {'session': session}})
            advertise(peer)
            forged = signed(dict(id=peer['id'], sk=caller['sk']), {'Advertise': {'capability': 'native-udp-punch', 'enabled': False}})
            advertise(peer, 0, forged)
            result['session_after_forged_withdrawal'] = rpc(caller, {'Poll': {'session': session}})
            withdrawal = advertise(peer, 0)
            result['session_after_signed_withdrawal'] = rpc(caller, {'Poll': {'session': session}})
            result['disabled_target'] = begin(caller, peer, server_key)
            advertise(peer)
            advertise(peer, 0, withdrawal)
            last = begin(caller, peer, server_key)
            result['after_reenable_and_replayed_withdrawal'] = next(iter(last)) if isinstance(last, dict) else last
        finally:
            for device in devices:
                device['socket'].close()
            server.terminate()
            server.wait(timeout=5)
            result['isolated_server_stopped'] = True
print(json.dumps(result, indent=2))
assert result['legacy_without_server_key_failure'] == 3
assert result['correct_key'] == 'Ticket'
assert result['negotiated_capability'] == 'native-udp-punch'
assert result['session_before_unsigned_withdrawal'] == 'Waiting'
assert result['disabled_target'] == 'Unsupported'
assert 'Error' in result['session_after_signed_withdrawal']
assert result['missing_key'] == result['wrong_key'] == 'Error'
assert result['session_after_unsigned_withdrawal'] == 'Waiting'
assert result['session_after_forged_withdrawal'] == 'Waiting'
assert result['after_reenable_and_replayed_withdrawal'] == 'Ticket'
