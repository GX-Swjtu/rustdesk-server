use crate::{common::get_arg, peer::PeerMap};
use hbb_common::{
    anyhow::{Context, Result},
    native_p2p::{self as p2p, coordinator::{Coordinator, Device, Outcome}},
    protobuf::Message,
    rendezvous_proto::{NativeP2pV1, RegisterPeer, RendezvousMessage},
    sodiumoxide::crypto::sign,
    tokio,
};
use std::{net::SocketAddr, sync::{Arc, Mutex}, time::Duration};

#[derive(Clone)]
pub(crate) struct NativeP2p(Arc<Mutex<Coordinator>>);

impl NativeP2p {
    pub fn new() -> Self {
        let state = Arc::new(Mutex::new(Coordinator::default()));
        let weak = Arc::downgrade(&state);
        tokio::spawn(async move {
            loop {
                tokio::time::sleep(Duration::from_secs(1)).await;
                let Some(state) = weak.upgrade() else { break; };
                if let Ok(now) = p2p::protocol::now() { state.lock().unwrap().expire(now); }
            }
        });
        Self(state)
    }

    fn enabled() -> bool {
        get_arg("NATIVE_P2P").to_uppercase() != "N" && get_arg("ALWAYS_USE_RELAY").to_uppercase() != "Y"
    }

    pub async fn register(&self, rp: &RegisterPeer, address: SocketAddr, peers: &PeerMap, key: Option<&sign::SecretKey>) -> Result<()> {
        if !Self::enabled() || rp.native_p2p_version != p2p::VERSION || key.is_none() {
            self.0.lock().unwrap().remove(&rp.id);
            return Ok(());
        }
        let key = key.context("native server key unavailable")?;
        let peer = peers.get(&rp.id).await.context("native device key not registered yet")?;
        let pk = peer.read().await.pk.clone();
        let public = sign::PublicKey::from_slice(&pk).context("invalid registered device key")?;
        let signed_key = sign::sign(&hbb_common::message_proto::IdPk { id: rp.id.clone(), pk, ..Default::default() }.write_to_bytes()?, key);
        self.0.lock().unwrap().advertise(Device { id: rp.id.clone(), key: public, signed_key, address }, &rp.native_p2p_capability)
    }

    pub fn handle(&self, message: &NativeP2pV1, source: SocketAddr, key: Option<&sign::SecretKey>) -> Result<Outcome> {
        hbb_common::anyhow::ensure!(Self::enabled() && message.version == p2p::VERSION, "native disabled or unsupported");
        self.0.lock().unwrap().handle(&message.payload, source, key.context("native server key unavailable")?)
    }
}

pub(crate) fn envelope(payload: Vec<u8>) -> RendezvousMessage {
    let mut message = RendezvousMessage::new();
    message.set_native_p2p_v1(NativeP2pV1 { version: p2p::VERSION, payload: payload.into(), ..Default::default() });
    message
}
