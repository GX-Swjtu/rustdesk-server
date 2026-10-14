//! Optional transport compatibility for native clients signed into an account API.
//! Account tokens remain API credentials, not remote-control authorization.
use hbb_common::{
    anyhow::{anyhow, bail, Context},
    bytes::{Bytes, BytesMut},
    bytes_codec::BytesCodec,
    futures_util::{stream::SplitSink, SinkExt, StreamExt},
    protobuf::{Message, UnknownValueRef},
    rendezvous_proto::{rendezvous_message, KeyExchange, RendezvousMessage},
    tcp::Encrypt,
    tokio::{net::TcpStream, sync::Semaphore, time::{timeout, Duration}},
    tokio_util::codec::{Decoder, Encoder, Framed, FramedParts},
    ResultType,
};
use once_cell::sync::Lazy;
use sodiumoxide::crypto::{box_, generichash, secretbox, sign};
use std::io;

static ENABLED: Lazy<bool> = Lazy::new(|| {
    std::env::var("HBBS_ACCOUNT_HANDSHAKE").as_deref() == Ok("Y")
});
static HANDSHAKES: Semaphore = Semaphore::const_new(32);
const HANDSHAKE_LIMIT: Duration = Duration::from_secs(5);
const MAX_SIGNAL_BYTES: usize = 1024 * 1024;

pub(crate) fn enabled() -> bool { *ENABLED }

/// Existing OSS and native-P2P requests speak first. Leave their socket untouched.
/// Signed-in clients instead wait for the server's KeyExchange greeting.
pub(crate) async fn waiting_for_greeting(stream: &TcpStream) -> ResultType<bool> {
    let mut byte = [0u8; 1];
    match timeout(Duration::from_secs(1), stream.peek(&mut byte)).await {
        Ok(result) => { result?; Ok(false) }
        Err(_) => Ok(true),
    }
}

pub(crate) struct SecureCodec {
    framing: BytesCodec,
    sending: Encrypt,
    receiving: Encrypt,
}
impl SecureCodec {
    fn new(sending: secretbox::Key, receiving: secretbox::Key) -> Self {
        let mut framing = BytesCodec::new();
        framing.set_max_packet_length(MAX_SIGNAL_BYTES);
        Self { framing, sending: Encrypt::new(sending), receiving: Encrypt::new(receiving) }
    }
}
impl Decoder for SecureCodec {
    type Item = BytesMut;
    type Error = io::Error;
    fn decode(&mut self, src: &mut BytesMut) -> io::Result<Option<BytesMut>> {
        let Some(mut bytes) = self.framing.decode(src)? else { return Ok(None); };
        // Reject clear one-byte frames too; Encrypt::dec tolerates legacy keepalives.
        if bytes.len() < 16 {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "Invalid encrypted signal"));
        }
        self.receiving.dec(&mut bytes)?;
        Ok(Some(bytes))
    }
}
impl Encoder<Bytes> for SecureCodec {
    type Error = io::Error;
    fn encode(&mut self, data: Bytes, dst: &mut BytesMut) -> io::Result<()> {
        if data.len() > MAX_SIGNAL_BYTES - 16 {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "Signal too large"));
        }
        self.framing.encode(self.sending.enc(&data).into(), dst)
    }
}
pub(crate) type SecureSink = SplitSink<Framed<TcpStream, SecureCodec>, Bytes>;

// Wire-compatible with the client's KX v1. Keep these additions local: the OSS
// server's older hbb_common proto only knows KeyExchange.keys (field 1).
fn directional_key(key: &secretbox::Key, direction: u8, initiator: &[u8], responder: &[u8]) -> ResultType<secretbox::Key> {
    let failed = |_| anyhow!("Handshake key derivation failed");
    let mut hash = generichash::State::new(Some(32), Some(&key.0)).map_err(failed)?;
    for bytes in [b"rdkx-spl".as_slice(), &[direction], &1u32.to_le_bytes(), &1u32.to_le_bytes(), initiator, responder] {
        hash.update(bytes).map_err(failed)?;
    }
    secretbox::Key::from_slice(hash.finalize().map_err(failed)?.as_ref())
        .context("Invalid derived key")
}

fn signed_parameters(public: &[u8;32], signing: &sign::SecretKey) -> Vec<u8> {
    // KxParams { bytes pk = 1; uint32 version = 2; }, prefixed for domain separation.
    let mut bytes = b"rdkx-params".to_vec();
    bytes.extend([0x0a, 32]);
    bytes.extend(public);
    bytes.extend([0x10, 1]);
    sign::sign(&bytes, signing)
}

pub(crate) async fn accept(stream: TcpStream, signing: &sign::SecretKey)
    -> ResultType<Framed<TcpStream, SecureCodec>>
{
    let _permit = HANDSHAKES.try_acquire().map_err(|_| anyhow!("Handshake capacity reached"))?;
    timeout(HANDSHAKE_LIMIT, async {
        let mut framing = BytesCodec::new();
        framing.set_max_packet_length(1024);
        let mut framed = Framed::new(stream, framing);
        let (public, secret) = box_::gen_keypair();
        let mut advertised_public = public.0;
        // X25519 ignores this bit. New clients require a signed parameter transcript.
        advertised_public[31] |= 0x80;
        let mut exchange = KeyExchange {
            keys: vec![sign::sign(&advertised_public, signing).into()],
            ..Default::default()
        };
        exchange.special_fields.mut_unknown_fields().add_varint(2, 1);
        exchange.special_fields.mut_unknown_fields().add_length_delimited(3, signed_parameters(&advertised_public, signing));
        let mut greeting = RendezvousMessage::new();
        greeting.set_key_exchange(exchange);
        framed.send(greeting.write_to_bytes()?.into()).await?;
        let reply = framed.next().await.context("Handshake peer closed")??;
        let reply = RendezvousMessage::parse_from_bytes(&reply)?;
        let Some(rendezvous_message::Union::KeyExchange(exchange)) = reply.union else {
            bail!("Expected key exchange reply");
        };
        // v0 reuses the same key/nonce in both directions. Do not enable that
        // transport for long-lived API bearer credentials on the public network.
        if exchange.keys.len() != 2 || exchange.special_fields.unknown_fields().get(2) != Some(UnknownValueRef::Varint(1)) {
            bail!("Invalid key exchange reply");
        }
        let key = Encrypt::decode(&exchange.keys[1], &exchange.keys[0], &secret)?;
        let receiving = directional_key(&key, 1, &exchange.keys[0], &advertised_public)?;
        let sending = directional_key(&key, 2, &exchange.keys[0], &advertised_public)?;
        let old = framed.into_parts();
        let mut parts = FramedParts::new(old.io, SecureCodec::new(sending, receiving));
        parts.read_buf = old.read_buf;
        parts.write_buf = old.write_buf;
        Ok(Framed::from_parts(parts))
    }).await.map_err(|_| anyhow!("Key exchange timed out"))?
}

#[cfg(test)]
#[path = "account_handshake_tests.rs"]
mod tests;
