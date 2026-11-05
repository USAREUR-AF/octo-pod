//! Serving consumers that dial the pod: a `tcp-server://` destination.
//!
//! Many consumers do not listen for data; they dial the sensor. A track correlator's radar-one input is a TCP
//! client, and so is a C2 system that connects to "the radar". For those, the egress arm
//! listens on the pod's own address and the consumer dials the pod as it would dial the
//! radar. The pod is the radar's stand-in.
//!
//! It behaves like a socket adapter's `tcp-server` sink:
//!
//! - only peers in [`Peers`] are served; anyone else is closed at once and audited;
//! - each consumer has its own bounded queue; one that falls behind loses frames (counted),
//!   never stalls the others;
//! - a consumer that connects late first gets the newest `replay` frames whose key ends in
//!   `/status`, so it learns the sensor's state before the next status message;
//! - anything a consumer sends is read and discarded: nothing a consumer writes ever
//!   reaches the pen or the fabric.
//!
//! One difference: at `max_clients`, a new allowed peer replaces the oldest connection
//! instead of being refused. A consumer that vanished without closing its socket would
//! otherwise hold the only slot until TCP timed it out, and a restarted correlator could not
//! reconnect for many minutes. Each replacement is audited.

use std::collections::VecDeque;
use std::io;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::Duration;

use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::mpsc;
use tokio::task::JoinHandle;
use tracing::{info, warn};

use crate::dest::Peers;

/// One sample's bytes, as written to every consumer; cheap to clone.
pub type Frame = Arc<[u8]>;

/// Frames a consumer may fall behind by before frames are dropped for it.
pub const QUEUE_FRAMES: usize = 4096;
/// Most consumers one destination may serve at once.
pub const MAX_CLIENTS: usize = 64;
/// Most status frames kept for replay.
pub const MAX_REPLAY: usize = 1024;
/// The key's last chunk that marks a status frame for replay.
pub const STATUS_CHUNK: &str = "status";

/// Where consumer events are recorded: the audit log in the arm, a list in tests.
pub trait Events: Send + Sync {
    /// Record one event (`connected`, `refused`, `evicted`, `disconnected`).
    fn event(&self, kind: &str, detail: Value);
}

/// Counters for one served destination.
#[derive(Debug, Default)]
pub struct ServeCounters {
    /// Consumers accepted.
    pub connected: AtomicU64,
    /// Connections from peers not on the list.
    pub refused: AtomicU64,
    /// Connections replaced by a newer one at `max_clients`.
    pub evicted: AtomicU64,
    /// Frames that arrived while no consumer was connected.
    pub no_client: AtomicU64,
    /// Frames dropped for a consumer whose queue was full.
    pub dropped_slow: AtomicU64,
    /// Bytes consumers sent, read and discarded.
    pub ignored_bytes: AtomicU64,
}

fn bump(c: &AtomicU64) {
    c.fetch_add(1, Ordering::Relaxed);
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(PoisonError::into_inner)
}

/// The newest status frames, replayed to a consumer that connects late.
#[derive(Debug, Default)]
struct StatusCache {
    keep: usize,
    frames: Mutex<VecDeque<Frame>>,
}

impl StatusCache {
    fn add(&self, frame: &Frame) {
        if self.keep == 0 {
            return;
        }
        let mut f = lock(&self.frames);
        if f.len() == self.keep {
            f.pop_front();
        }
        f.push_back(frame.clone());
    }

    fn snapshot(&self) -> Vec<Frame> {
        lock(&self.frames).iter().cloned().collect()
    }
}

struct Client {
    peer: SocketAddr,
    tx: mpsc::Sender<Frame>,
}

/// How a `tcp-server://` destination serves.
#[derive(Debug, Clone)]
pub struct ServeConfig {
    /// The pod's own address and port to listen on.
    pub bind: SocketAddr,
    /// The consumers allowed to connect.
    pub peers: Peers,
    /// Most consumers at once, 1 to [`MAX_CLIENTS`].
    pub max_clients: usize,
    /// Status frames replayed on connect, 0 to [`MAX_REPLAY`].
    pub replay: usize,
}

/// A running listener: offer it each frame, and it reaches every connected consumer.
pub struct Server {
    clients: Arc<Mutex<Vec<Client>>>,
    cache: Arc<StatusCache>,
    counters: Arc<ServeCounters>,
    local: SocketAddr,
    accept: JoinHandle<()>,
}

impl Server {
    /// Bind and start accepting.
    ///
    /// # Errors
    ///
    /// Fails when the address cannot be bound, or the configuration is out of range.
    pub async fn start(
        cfg: ServeConfig,
        events: Arc<dyn Events>,
        counters: Arc<ServeCounters>,
    ) -> io::Result<Self> {
        if cfg.peers.is_empty() {
            return Err(io::Error::other("no peers allowed: refusing to listen"));
        }
        if !(1..=MAX_CLIENTS).contains(&cfg.max_clients) || cfg.replay > MAX_REPLAY {
            return Err(io::Error::other(format!(
                "max_clients must be 1-{MAX_CLIENTS} and replay 0-{MAX_REPLAY}"
            )));
        }
        let listener = TcpListener::bind(cfg.bind).await?;
        let local = listener.local_addr()?;
        info!(listen = %local, peers = %cfg.peers.describe(), max_clients = cfg.max_clients,
              replay = cfg.replay, "listening for consumers to dial");
        let clients: Arc<Mutex<Vec<Client>>> = Arc::default();
        let cache = Arc::new(StatusCache {
            keep: cfg.replay,
            frames: Mutex::default(),
        });
        let accept = tokio::spawn(accept(
            listener,
            cfg,
            clients.clone(),
            cache.clone(),
            events,
            counters.clone(),
        ));
        Ok(Self {
            clients,
            cache,
            counters,
            local,
            accept,
        })
    }

    /// The address actually bound (the declared one in production; tests bind port 0).
    #[must_use]
    pub fn local_addr(&self) -> SocketAddr {
        self.local
    }

    /// Queue one sample for every connected consumer, and remember it for replay when its
    /// key is a status key. Returns how many consumers it was queued for.
    #[must_use]
    pub fn offer(&self, key: &str, frame: &Frame) -> usize {
        if key.rsplit('/').next() == Some(STATUS_CHUNK) {
            self.cache.add(frame);
        }
        let mut list = lock(&self.clients);
        list.retain(|c| !c.tx.is_closed());
        if list.is_empty() {
            bump(&self.counters.no_client);
            return 0;
        }
        let mut queued = 0;
        for c in list.iter() {
            if c.tx.try_send(frame.clone()).is_ok() {
                queued += 1;
            } else {
                bump(&self.counters.dropped_slow);
            }
        }
        queued
    }

    /// Consumers connected now.
    #[must_use]
    pub fn connected(&self) -> usize {
        let mut list = lock(&self.clients);
        list.retain(|c| !c.tx.is_closed());
        list.len()
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.accept.abort();
    }
}

async fn accept(
    listener: TcpListener,
    cfg: ServeConfig,
    clients: Arc<Mutex<Vec<Client>>>,
    cache: Arc<StatusCache>,
    events: Arc<dyn Events>,
    counters: Arc<ServeCounters>,
) {
    loop {
        let (stream, peer) = match listener.accept().await {
            Ok(c) => c,
            Err(e) => {
                // Usually out of file descriptors: back off instead of spinning.
                warn!(error = %e, "accept failed");
                tokio::time::sleep(Duration::from_millis(200)).await;
                continue;
            }
        };
        if !cfg.peers.permits(peer.ip()) {
            bump(&counters.refused);
            warn!(%peer, "refusing a consumer that is not an allowed peer");
            events.event("refused", json!({ "peer": peer.to_string() }));
            continue; // dropping the stream closes it
        }
        let _ = stream.set_nodelay(true);
        let (tx, rx) = mpsc::channel::<Frame>(QUEUE_FRAMES);
        for frame in cache.snapshot() {
            let _ = tx.try_send(frame);
        }
        {
            let mut list = lock(&clients);
            list.retain(|c| !c.tx.is_closed());
            while list.len() >= cfg.max_clients {
                // Dropping its sender ends its writer task, which closes the socket.
                let old = list.remove(0);
                bump(&counters.evicted);
                info!(old = %old.peer, new = %peer, "replacing the oldest consumer at max_clients");
                events.event(
                    "evicted",
                    json!({ "peer": old.peer.to_string(), "by": peer.to_string() }),
                );
            }
            list.push(Client { peer, tx });
        }
        bump(&counters.connected);
        info!(%peer, "consumer connected");
        events.event("connected", json!({ "peer": peer.to_string() }));
        tokio::spawn(serve_one(
            stream,
            rx,
            peer,
            events.clone(),
            counters.clone(),
        ));
    }
}

/// Write queued frames to one consumer; read and discard anything it sends; stop when it
/// leaves, its queue is closed (replaced), or a write fails.
async fn serve_one(
    stream: TcpStream,
    mut rx: mpsc::Receiver<Frame>,
    peer: SocketAddr,
    events: Arc<dyn Events>,
    counters: Arc<ServeCounters>,
) {
    let (mut rd, mut wr) = stream.into_split();
    let mut buf = [0u8; 4096];
    loop {
        tokio::select! {
            frame = rx.recv() => match frame {
                Some(f) => if wr.write_all(&f).await.is_err() { break },
                None => break,
            },
            n = rd.read(&mut buf) => match n {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    counters.ignored_bytes.fetch_add(n as u64, Ordering::Relaxed);
                }
            },
        }
    }
    let _ = wr.shutdown().await;
    info!(%peer, "consumer disconnected");
    events.event("disconnected", json!({ "peer": peer.to_string() }));
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::AsyncReadExt;

    #[derive(Default)]
    struct Recorded(Mutex<Vec<(String, Value)>>);

    impl Events for Recorded {
        fn event(&self, kind: &str, detail: Value) {
            lock(&self.0).push((kind.to_string(), detail));
        }
    }

    impl Recorded {
        fn kinds(&self) -> Vec<String> {
            lock(&self.0).iter().map(|(k, _)| k.clone()).collect()
        }
    }

    async fn server(
        peers: &str,
        max: usize,
        replay: usize,
    ) -> (Server, Arc<Recorded>, Arc<ServeCounters>) {
        let events = Arc::new(Recorded::default());
        let counters = Arc::new(ServeCounters::default());
        let cfg = ServeConfig {
            bind: "127.0.0.1:0".parse().unwrap(),
            peers: Peers::from_csv(peers).unwrap(),
            max_clients: max,
            replay,
        };
        let s = Server::start(cfg, events.clone(), counters.clone())
            .await
            .unwrap();
        (s, events, counters)
    }

    /// Wait until the server holds `n` consumers (the accept runs on another task).
    async fn until_connected(s: &Server, n: usize) {
        for _ in 0..200 {
            if s.connected() == n {
                return;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        panic!("expected {n} consumers, have {}", s.connected());
    }

    async fn read_exact_n(stream: &mut TcpStream, n: usize) -> Vec<u8> {
        let mut out = vec![0u8; n];
        tokio::time::timeout(Duration::from_secs(5), stream.read_exact(&mut out))
            .await
            .expect("read timed out")
            .unwrap();
        out
    }

    fn frame(b: &[u8]) -> Frame {
        Arc::from(b)
    }

    #[tokio::test]
    async fn an_allowed_consumer_dials_in_and_gets_the_stream_in_order() {
        let (s, events, _) = server("127.0.0.1", 1, 0).await;
        let mut c = TcpStream::connect(s.local_addr()).await.unwrap();
        until_connected(&s, 1).await;
        assert_eq!(
            s.offer("a/sensors/radar1/radar-one/data", &frame(b"one|")),
            1
        );
        assert_eq!(
            s.offer("a/sensors/radar1/radar-one/data", &frame(b"two|")),
            1
        );
        assert_eq!(read_exact_n(&mut c, 8).await, b"one|two|");
        assert!(events.kinds().contains(&"connected".to_string()));
    }

    #[tokio::test]
    async fn a_peer_not_on_the_list_is_closed_and_audited() {
        let (s, events, counters) = server("10.9.9.9", 1, 0).await;
        let mut c = TcpStream::connect(s.local_addr()).await.unwrap();
        let mut buf = [0u8; 1];
        let n = tokio::time::timeout(Duration::from_secs(5), c.read(&mut buf))
            .await
            .unwrap()
            .unwrap_or(0);
        assert_eq!(n, 0, "the refused connection is closed without data");
        assert_eq!(counters.refused.load(Ordering::Relaxed), 1);
        assert_eq!(events.kinds(), vec!["refused".to_string()]);
        assert_eq!(s.connected(), 0);
    }

    #[tokio::test]
    async fn a_late_consumer_first_gets_the_newest_status_frames_only() {
        let (s, _, counters) = server("127.0.0.1", 1, 2).await;
        for f in [&b"s1|"[..], b"s2|", b"s3|"] {
            let _ = s.offer("a/sensors/radar1/radar-one/status", &frame(f));
        }
        let _ = s.offer("a/sensors/radar1/radar-one/data", &frame(b"d1|"));
        assert_eq!(counters.no_client.load(Ordering::Relaxed), 4);
        let mut c = TcpStream::connect(s.local_addr()).await.unwrap();
        until_connected(&s, 1).await;
        let _ = s.offer("a/sensors/radar1/radar-one/data", &frame(b"d2|"));
        assert_eq!(
            read_exact_n(&mut c, 9).await,
            b"s2|s3|d2|",
            "replay, then live"
        );
    }

    #[tokio::test]
    async fn what_a_consumer_sends_is_discarded_and_the_stream_continues() {
        let (s, _, counters) = server("127.0.0.1", 1, 0).await;
        let mut c = TcpStream::connect(s.local_addr()).await.unwrap();
        until_connected(&s, 1).await;
        c.write_all(b"REGISTER ME\n").await.unwrap();
        for _ in 0..200 {
            if counters.ignored_bytes.load(Ordering::Relaxed) == 12 {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        assert_eq!(counters.ignored_bytes.load(Ordering::Relaxed), 12);
        assert_eq!(s.offer("a/x", &frame(b"live")), 1);
        assert_eq!(read_exact_n(&mut c, 4).await, b"live");
    }

    #[tokio::test]
    async fn at_max_clients_a_new_consumer_replaces_the_oldest() {
        let (s, events, counters) = server("127.0.0.1", 1, 0).await;
        let mut old = TcpStream::connect(s.local_addr()).await.unwrap();
        until_connected(&s, 1).await;
        let mut new = TcpStream::connect(s.local_addr()).await.unwrap();
        for _ in 0..200 {
            if counters.evicted.load(Ordering::Relaxed) == 1 {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        assert_eq!(counters.evicted.load(Ordering::Relaxed), 1);
        let mut buf = [0u8; 1];
        let n = tokio::time::timeout(Duration::from_secs(5), old.read(&mut buf))
            .await
            .unwrap()
            .unwrap_or(0);
        assert_eq!(n, 0, "the replaced consumer is closed");
        until_connected(&s, 1).await;
        assert_eq!(s.offer("a/x", &frame(b"to-new")), 1);
        assert_eq!(read_exact_n(&mut new, 6).await, b"to-new");
        assert!(events.kinds().contains(&"evicted".to_string()));
    }

    #[tokio::test]
    async fn it_refuses_to_listen_without_peers_or_out_of_range() {
        let events: Arc<dyn Events> = Arc::new(Recorded::default());
        let bind: SocketAddr = "127.0.0.1:0".parse().unwrap();
        for (peers, max, replay) in [
            ("", 1, 0),
            ("127.0.0.1", 0, 0),
            ("127.0.0.1", 65, 0),
            ("127.0.0.1", 1, 1025),
        ] {
            let cfg = ServeConfig {
                bind,
                peers: Peers::from_csv(peers).unwrap(),
                max_clients: max,
                replay,
            };
            assert!(
                Server::start(cfg, events.clone(), Arc::default())
                    .await
                    .is_err(),
                "peers={peers:?} max={max} replay={replay} must be refused"
            );
        }
    }
}
