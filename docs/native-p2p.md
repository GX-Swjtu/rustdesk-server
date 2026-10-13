# Native P2P v1 test server

hbbs now coordinates the optional, authenticated NativeP2pV1 strategy. hbbr is
unchanged. The implementation and frp v0.71.0 notice are in
`libs/hbb_common/native-p2p/`. Use the server's own common-library baseline and
cherry-pick only the P2P patch series from the client branch.

```sh
git submodule update --init --recursive
cargo build --locked --release --bin hbbs --bin hbbr
```

Deploy copies of these binaries to a new directory. Running hbbs there creates
an independent database and signing key; keep that key private. In that directory:

```sh
./hbbr -p 22117
./hbbs -p 22116 -r TEST_HOST:22117
```

Allow only the required test ports (22115 TCP, 22116 TCP/UDP, 22117 TCP;
22118/22119 are the upstream WebSocket listeners). Clients must use the matching
test public key. Do not overwrite a production key/database or replace the
production services to run these tests.

The feature is on by default; `NATIVE_P2P=N` disables it. The existing
`ALWAYS_USE_RELAY=Y` gate also excludes it. Both devices must have recently
advertised signed version-1 capabilities. Unsupported/Android peers continue
through the original rendezvous flow. Each device is limited to one attempt;
there are at most 64 sessions, expiring after 45 seconds, and 4096 capabilities.
Signed requests are quota limited and replay checked; candidate UDP public IPs
must match the registered signaling exit. No new unauthenticated punch request
is accepted. Normal RustDesk client identity/login/permission checks still run.

Native Begin requests enforce the same configured server key as ordinary punch
requests. An older native test client without that signed field falls back to
the original connection flow; update clients before switching to this server.
Unsigned legacy registrations do not remove native capabilities. Only a signed
version-0 advertisement disables them and cancels their pending attempts.

The adaptive extension requires both signed capabilities to include
`strategies=1`. Without it, tickets and plans omit all new JSON fields and the
original fixed Mode 2 remains in use. With it, hbbs selects a Mode 1/2 and TTL
pair from at most 1024 network-pair histories, expiring after one hour. Selection
penalizes a choice once; repeated polls reuse it. Only a signed caller report
after the client has authenticated KCP rewards the selected strategy and consumes
the attempt. A UDP diagnostic cannot establish that condition and never reports
success to history. See the common README for limits and the port heuristic.
This extension changes only the common pin and this documentation in hbbs;
no additional server-wrapper or legacy-rendezvous hook is required.

Admission regression (Python 3 and the libsodium shared library are required):

```sh
cargo test --locked --manifest-path libs/hbb_common/native-p2p/Cargo.toml
python3 tests/native_p2p_admission.py /absolute/path/to/hbbs
python3 tests/native_p2p_admission.py /absolute/path/to/hbbs --strategies
# Also verify new advertisements against the admission-fixed v3 server:
python3 tests/native_p2p_admission.py /absolute/path/to/v3/hbbs --strategies --legacy-strategies-server
```

The test starts a temporary keyed hbbs on loopback port 32216, exercises missing,
wrong and correct keys plus unsigned, forged and replayed withdrawals, and stops
the server afterward. `--expect-vulnerable` compares an older native test binary.

For rollback, stop only the test hbbs/hbbr processes and restore the clients'
saved configuration/service paths. Production hbbs/hbbr and frp are independent.

## Existing paths changed

| Existing file | Necessary change |
| --- | --- |
| `Cargo.toml`, `Cargo.lock` | Enable the optional common module. |
| `.gitmodules`, `libs/hbb_common` | Pin only the P2P commits on the server baseline. |
| `src/lib.rs` | Register the new coordinator wrapper. |
| `src/rendezvous_server.rs` | One coordinator field, capability hook and bounded signed TCP extension dispatch. |

Existing peer lookup/registration, TCP punching, relay selection and hbbr data
forwarding are not refactored. The only protobuf changes are new high-numbered
fields. The server does not proxy the new UDP/KCP data path.
