# Native UDP punching test server

hbbs now coordinates the optional, authenticated native-udp-punch capability. hbbr is
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
`ALWAYS_USE_RELAY=Y` gate also excludes it. Both devices must recently advertise
the single signed `native-udp-punch` capability. Old experimental clients,
upstream clients and disabled peers do not enter the native coordinator. Maintained
Android clients can advertise the same capability. A new graphical caller shows
an explicit unsupported result until the user chooses relay or cancels.
There are no separate V1/V2 or `strategies` declarations. The protobuf envelope
name is unchanged; this expansion adds no schema field or listener port.

The common coordinator selects one of five role/TTL/delay families and stores
at most 1024 network-profile records for one hour. Selection costs one point;
only a caller report after the existing KCP/device-identity handshake rewards
two points and consumes the attempt. Native attempts are bounded at 60 seconds,
one per device and 64 server-wide, with 4096 capability records. Signed disable
requests cancel sessions; unsigned, forged and replayed withdrawals do not.
Clients honor shorter tickets issued by an older server. No-strategy results use
the existing signed Error string with a bounded `native-p2p-reason:` JSON body
containing only validated network candidates. Strict Plan/Ticket messages and
the capability declaration are unchanged. Graphical retries obtain new sessions;
the server never creates a background retry loop.

Public candidates must be observed through device-signed TCP requests for that
session. Signatures and participant eligibility are checked before adding the
source IP; at most four exits and four supplementary observations are allowed.
STUN addresses alone are insufficient. Only the native module uses these lists;
original rendezvous source-IP checks remain unchanged. This expansion needs
no additional hbbs wrapper change and does not change hbbr.

Regression tests (Linux root for isolated namespaces, Python 3, libsodium and iproute2):

```sh
cargo test --locked --manifest-path libs/hbb_common/native-p2p/Cargo.toml
sudo python3 tests/native_p2p_admission.py /absolute/path/to/hbbs
```

The admission test uses an isolated namespace and ephemeral keyed server/database.
It checks original admission-key rejection, new signed admission, unsigned/forged/
signed/replayed withdrawals and disabled-peer fallback. Common tests cover
capability incompatibility, session membership, source proofs, quotas and expiry.
The client repository's NAT lab adds Linux conntrack/SNAT, LAN fixtures, multiple
exits, packet loss, cancellation and authenticated KCP communication. Neither
UDP nor KCP diagnostics claim full RustDesk desktop acceptance.

The 2026-10-14 on-demand validation is recorded in the client repository's
`tools/native-p2p/ACCEPTANCE-20261014.md`: final Mode 0–4 groups are 10/10 each,
multi-egress 9/10, and real Windows cold connections meet the requested
threshold in both directions. Android builds and true-device checks are
reported separately. This expansion pins common `a7fd568` on the server's own
baseline (the client-equivalent patch is `ce2570b`), with no new hbbs runtime
hook or protobuf schema change relative to v4.

The subsequent P2P-first waiting change pins common `94949fe`; it changes only
the native coordinator lease/reason behavior and common transport ownership.
Its tests, waiting UI, packages and rollback record are in the client repository's
`tools/native-p2p/WAITING-20261014.md` and `artifacts/p2p-waiting-20261014`.

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
