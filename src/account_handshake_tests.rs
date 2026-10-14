use super::*;
use hbb_common::{
    rendezvous_proto::TestNatRequest,
    tokio::{self, net::TcpListener},
};
use sodiumoxide::crypto::secretbox;

async fn pair() -> (TcpStream, TcpStream) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let client = TcpStream::connect(listener.local_addr().unwrap()).await.unwrap();
    let (server, _) = listener.accept().await.unwrap();
    (client, server)
}

async fn client_exchange(socket: TcpStream, signing: sign::PublicKey) -> Framed<TcpStream, SecureCodec> {
    let mut client = Framed::new(socket, BytesCodec::new());
    let greeting = client.next().await.unwrap().unwrap();
    let greeting = RendezvousMessage::parse_from_bytes(&greeting).unwrap();
    let Some(rendezvous_message::Union::KeyExchange(greeting)) = greeting.union else { panic!("no greeting") };
    let public = sign::verify(&greeting.keys[0], &signing).unwrap();
    let Some(UnknownValueRef::LengthDelimited(signed)) = greeting.special_fields.unknown_fields().get(3) else { panic!("no signed parameters") };
    let params = sign::verify(signed, &signing).unwrap();
    assert_eq!(&params[..13], b"rdkx-params\x0a\x20");
    assert_eq!(&params[13..45], public.as_slice());
    assert_eq!(&params[45..], &[0x10,1]);
    assert_eq!(greeting.special_fields.unknown_fields().get(2), Some(UnknownValueRef::Varint(1)));
    let (ours, secret) = box_::gen_keypair();
    let key = secretbox::gen_key();
    let boxed = box_::seal(&key.0, &box_::Nonce([0;24]), &box_::PublicKey::from_slice(&public).unwrap(), &secret);
    let mut reply = RendezvousMessage::new();
    let mut exchange = KeyExchange { keys: vec![ours.0.to_vec().into(), boxed.into()], ..Default::default() };
    exchange.special_fields.mut_unknown_fields().add_varint(2,1);
    reply.set_key_exchange(exchange);
    client.send(reply.write_to_bytes().unwrap().into()).await.unwrap();
    let old=client.into_parts();
    let mut parts=FramedParts::new(old.io, SecureCodec::new(
        directional_key(&key,1,&ours.0,&public).unwrap(), directional_key(&key,2,&ours.0,&public).unwrap()));
    parts.read_buf=old.read_buf;
    parts.write_buf=old.write_buf;
    Framed::from_parts(parts)
}

#[tokio::test]
async fn plaintext_first_packet_is_not_consumed_or_prefixed() {
    use hbb_common::tokio::io::{AsyncReadExt, AsyncWriteExt};
    let (mut client, mut server) = pair().await;
    client.write_all(&[12, 162, 1, 0]).await.unwrap();
    assert!(!waiting_for_greeting(&server).await.unwrap());
    let mut bytes = [0;4];
    server.read_exact(&mut bytes).await.unwrap();
    assert_eq!(bytes, [12,162,1,0]);
    assert!(timeout(Duration::from_millis(30), client.read_u8()).await.is_err());
}

#[tokio::test]
async fn split_clients_exchange_encrypted_signals_after_split() {
        let (client, server) = pair().await;
        let (public, secret) = sign::gen_keypair();
        let worker = tokio::spawn(async move {
            let stream = accept(server, &secret).await.unwrap();
            let (mut send, mut receive) = stream.split();
            for serial in [17, 29] {
                let bytes = receive.next().await.unwrap().unwrap();
                let message = RendezvousMessage::parse_from_bytes(&bytes).unwrap();
                let Some(rendezvous_message::Union::TestNatRequest(nat)) = message.union else { panic!("not decrypted") };
                assert_eq!(nat.serial, serial);
                send.send(bytes.freeze()).await.unwrap();
            }
        });
        let mut client = client_exchange(client, public).await;
        for serial in [17,29] {
            let mut message = RendezvousMessage::new();
            message.set_test_nat_request(TestNatRequest{serial,..Default::default()});
            client.send(message.write_to_bytes().unwrap().into()).await.unwrap();
            let received = client.next().await.unwrap().unwrap();
            assert_eq!(received.as_ref(), message.write_to_bytes().unwrap().as_slice());
        }
        worker.await.unwrap();
}

#[tokio::test]
async fn rejects_invalid_key_exchange_and_oversized_handshake() {
    for bytes in [vec![162,1,0], vec![1;1025]] {
        let (client, server) = pair().await;
        let (_, secret) = sign::gen_keypair();
        let worker = tokio::spawn(async move { accept(server, &secret).await.is_err() });
        let mut client = Framed::new(client, BytesCodec::new());
        client.next().await.unwrap().unwrap();
        client.send(Bytes::from(bytes)).await.unwrap();
        assert!(worker.await.unwrap());
    }
}

#[test]
fn authenticated_codec_rejects_plaintext_and_tampering_and_large_frames() {
    for payload in [vec![0], vec![2;32]] {
        let mut bytes = BytesMut::new();
        BytesCodec::new().encode(payload.into(), &mut bytes).unwrap();
        assert!(SecureCodec::new(secretbox::gen_key(),secretbox::gen_key()).decode(&mut bytes).is_err());
    }
    let mut codec = SecureCodec::new(secretbox::gen_key(),secretbox::gen_key());
    let mut bytes = BytesMut::from(((MAX_SIGNAL_BYTES as u32 + 1) << 2 | 2).to_le_bytes().as_slice());
    assert!(codec.decode(&mut bytes).is_err());
}
