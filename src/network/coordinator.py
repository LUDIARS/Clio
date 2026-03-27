"""
Network Coordinator.

Orchestrates all network components (ContentStore, PeerStore, MeshNetwork,
TorrentManager, StreamManager) into a unified API for the Clio module.

The coordinator is the single entry point for all P2P operations:
    - Publishing local content to the network
    - Discovering and downloading remote content
    - Streaming content during download
    - Managing peer connections and swarm health
"""

from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable

from src.network.content_store import CID, ContentManifest, ContentStore, HashAlgorithm
from src.network.peer import PeerAddress, PeerID, PeerInfo, PeerState, PeerStore
from src.network.mesh import MeshMessage, MeshNetwork, MessageType
from src.network.torrent import (
    Bitfield,
    SelectionStrategy,
    TorrentManager,
    TorrentTransfer,
)
from src.network.stream import FileStream, StreamManager


@dataclass
class NetworkConfig:
    """Configuration for the network coordinator."""

    # Storage
    store_path: Path | None = None
    chunk_size: int = 256 * 1024
    hash_algorithm: HashAlgorithm = HashAlgorithm.SHA256

    # Peer management
    max_peers: int = 200
    max_gossip_peers: int = 8

    # Transfer
    default_strategy: SelectionStrategy = SelectionStrategy.RAREST_FIRST
    max_concurrent_requests: int = 10
    request_timeout: float = 30.0

    # Streaming
    stream_buffer_ahead: int = 8

    # Bootstrap peers
    bootstrap_peers: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> NetworkConfig:
        config = cls()
        if "store_path" in data:
            config.store_path = Path(data["store_path"])
        if "chunk_size" in data:
            config.chunk_size = data["chunk_size"]
        if "hash_algorithm" in data:
            config.hash_algorithm = HashAlgorithm(data["hash_algorithm"])
        if "max_peers" in data:
            config.max_peers = data["max_peers"]
        if "max_gossip_peers" in data:
            config.max_gossip_peers = data["max_gossip_peers"]
        if "default_strategy" in data:
            config.default_strategy = SelectionStrategy(data["default_strategy"])
        if "max_concurrent_requests" in data:
            config.max_concurrent_requests = data["max_concurrent_requests"]
        if "request_timeout" in data:
            config.request_timeout = data["request_timeout"]
        if "stream_buffer_ahead" in data:
            config.stream_buffer_ahead = data["stream_buffer_ahead"]
        if "bootstrap_peers" in data:
            config.bootstrap_peers = data["bootstrap_peers"]
        return config


class NetworkCoordinator:
    """
    High-level coordinator for all Clio P2P network operations.

    Usage:
        coordinator = NetworkCoordinator(config)

        # Publish a local file
        manifest = coordinator.publish_file(Path("data/resource.bin"))

        # Download content from the network
        transfer = coordinator.download(manifest)

        # Stream content while downloading
        stream = coordinator.stream(manifest)
        for chunk_data in stream.iter_pieces():
            process(chunk_data)
    """

    def __init__(self, config: NetworkConfig | None = None):
        self.config = config or NetworkConfig()

        # Initialize components
        self.content_store = ContentStore(
            store_path=self.config.store_path,
            chunk_size=self.config.chunk_size,
            algorithm=self.config.hash_algorithm,
        )
        self.peer_store = PeerStore(max_peers=self.config.max_peers)
        self.mesh = MeshNetwork(
            peer_store=self.peer_store,
            max_gossip_peers=self.config.max_gossip_peers,
        )
        self.torrent_manager = TorrentManager(self.content_store)
        self.stream_manager = StreamManager(self.content_store)

        # Wire up mesh message handlers for data transfer
        self._setup_mesh_handlers()

        # Bootstrap
        self._bootstrap()

    @property
    def local_peer_id(self) -> PeerID:
        return self.peer_store.local_id

    # ──────────────────────────────────────────────
    # Publishing (Seeding)
    # ──────────────────────────────────────────────

    def publish_file(self, path: Path, name: str = "") -> ContentManifest:
        """
        Ingest a local file and announce it to the network.

        The file is split into chunks, stored in the content store,
        and announced to the mesh network for other peers to discover.
        """
        manifest = self.content_store.ingest_file(path, name=name)

        # Create a seeding transfer
        transfer = self.torrent_manager.create_transfer(manifest)

        # Announce all chunk CIDs to the mesh
        for cid in manifest.chunk_cids:
            self.mesh.announce_content(cid)
        self.mesh.announce_content(manifest.root_cid)

        return manifest

    def publish_bytes(self, data: bytes, name: str = "") -> ContentManifest:
        """Ingest raw bytes and announce to the network."""
        manifest = self.content_store.ingest_bytes(data, name=name)
        transfer = self.torrent_manager.create_transfer(manifest)

        for cid in manifest.chunk_cids:
            self.mesh.announce_content(cid)
        self.mesh.announce_content(manifest.root_cid)

        return manifest

    # ──────────────────────────────────────────────
    # Downloading
    # ──────────────────────────────────────────────

    def download(
        self,
        manifest: ContentManifest,
        strategy: SelectionStrategy | None = None,
    ) -> TorrentTransfer:
        """
        Start downloading content described by a manifest.

        Initiates peer discovery for the content's chunks and begins
        torrent-style multi-peer download.
        """
        strategy = strategy or self.config.default_strategy
        transfer = self.torrent_manager.create_transfer(manifest, strategy=strategy)

        # Find providers for missing chunks
        missing_cids = self.content_store.get_missing_cids(manifest.chunk_cids)
        for cid in missing_cids:
            self.mesh.find_content(cid)

        return transfer

    def download_from_cid(self, root_cid: CID) -> TorrentTransfer | None:
        """
        Download content by root CID.

        First requests the manifest from the network, then starts download.
        """
        # Check if we already have the manifest
        manifest = self.content_store.get_manifest(root_cid)
        if manifest:
            return self.download(manifest)

        # Request manifest from network
        self.mesh.find_content(root_cid)
        return None  # Caller must wait for manifest discovery

    # ──────────────────────────────────────────────
    # Streaming
    # ──────────────────────────────────────────────

    def stream(
        self,
        manifest: ContentManifest,
        buffer_ahead: int | None = None,
    ) -> FileStream:
        """
        Start streaming content from the network.

        Uses sequential piece selection to prioritize pieces needed
        for continuous playback.
        """
        buffer_ahead = buffer_ahead or self.config.stream_buffer_ahead

        # Ensure there's a download transfer
        transfer = self.torrent_manager.get_transfer(manifest.root_cid)
        if not transfer:
            transfer = self.download(manifest, strategy=SelectionStrategy.SEQUENTIAL)

        stream = self.stream_manager.create_stream(
            manifest=manifest,
            transfer=transfer,
            buffer_ahead=buffer_ahead,
        )
        stream.start()
        return stream

    # ──────────────────────────────────────────────
    # Peer Management
    # ──────────────────────────────────────────────

    def add_peer(
        self,
        host: str,
        port: int,
        peer_id: PeerID | None = None,
    ) -> PeerInfo:
        """Manually add a peer to the network."""
        pid = peer_id or PeerID.generate()
        peer_info = PeerInfo(
            peer_id=pid,
            addresses=[PeerAddress(host=host, port=port)],
            state=PeerState.CONNECTED,
        )
        self.peer_store.add_peer(peer_info)
        self.mesh.routing_table.add_peer(pid)
        return peer_info

    def discover_peers(self) -> None:
        """Trigger peer discovery on the mesh network."""
        self.mesh.discover_peers()

    # ──────────────────────────────────────────────
    # Content Queries
    # ──────────────────────────────────────────────

    def has_content(self, root_cid: CID) -> bool:
        """Check if content is fully available locally."""
        manifest = self.content_store.get_manifest(root_cid)
        if not manifest:
            return False
        missing = self.content_store.get_missing_cids(manifest.chunk_cids)
        return len(missing) == 0

    def get_content(self, root_cid: CID) -> bytes | None:
        """Retrieve fully downloaded content as bytes."""
        manifest = self.content_store.get_manifest(root_cid)
        if not manifest:
            return None
        try:
            return self.content_store.reassemble(manifest)
        except ValueError:
            return None

    def save_content(self, root_cid: CID, output_path: Path) -> Path | None:
        """Save fully downloaded content to a file."""
        manifest = self.content_store.get_manifest(root_cid)
        if not manifest:
            return None
        try:
            return self.content_store.reassemble_to_file(manifest, output_path)
        except ValueError:
            return None

    def find_providers(self, cid: CID) -> list[PeerInfo]:
        """Find peers that provide a specific CID."""
        return self.mesh.get_content_providers(cid)

    # ──────────────────────────────────────────────
    # Transfer Management
    # ──────────────────────────────────────────────

    def get_transfer_stats(self) -> list[dict]:
        """Get stats for all active transfers."""
        return self.torrent_manager.get_all_stats()

    def get_stream_stats(self) -> list[dict]:
        """Get stats for all active streams."""
        return self.stream_manager.get_all_stats()

    def get_network_stats(self) -> dict:
        """Get overall network statistics."""
        mesh_stats = self.mesh.get_network_stats()
        return {
            **mesh_stats,
            "stored_blocks": self.content_store.stored_block_count,
            "active_transfers": len(self.torrent_manager.get_active_transfers()),
            "seeding_transfers": len(self.torrent_manager.get_seeding_transfers()),
            "active_streams": len(self.stream_manager._streams),
        }

    # ──────────────────────────────────────────────
    # Internal Setup
    # ──────────────────────────────────────────────

    def _setup_mesh_handlers(self) -> None:
        """Wire mesh message handlers for torrent protocol integration."""

        def on_want_block(msg: MeshMessage) -> None:
            cid_hex = msg.payload.get("cid_hash", "")
            root_hex = msg.payload.get("root_cid", "")
            piece_idx = msg.payload.get("piece_index", -1)

            if root_hex:
                root_cid = CID(hash_hex=root_hex)
                transfer = self.torrent_manager.get_transfer(root_cid)
                if transfer and piece_idx >= 0:
                    chunk = transfer.get_piece_for_peer(msg.sender, piece_idx)
                    if chunk:
                        self.mesh._send(msg.sender, MeshMessage(
                            msg_type=MessageType.BLOCK_DATA,
                            sender=self.local_peer_id,
                            payload={
                                "root_cid": root_hex,
                                "piece_index": piece_idx,
                                "cid_hash": chunk.cid.hash_hex,
                                "data_size": chunk.size,
                            },
                        ))

        def on_have_block(msg: MeshMessage) -> None:
            root_hex = msg.payload.get("root_cid", "")
            piece_idx = msg.payload.get("piece_index", -1)
            if root_hex and piece_idx >= 0:
                root_cid = CID(hash_hex=root_hex)
                transfer = self.torrent_manager.get_transfer(root_cid)
                if transfer:
                    transfer.peer_has_piece(msg.sender, piece_idx)

        def on_manifest_request(msg: MeshMessage) -> None:
            root_hex = msg.payload.get("root_cid", "")
            if root_hex:
                root_cid = CID(hash_hex=root_hex)
                manifest = self.content_store.get_manifest(root_cid)
                if manifest:
                    self.mesh._send(msg.sender, MeshMessage(
                        msg_type=MessageType.MANIFEST_RESPONSE,
                        sender=self.local_peer_id,
                        payload=manifest.to_dict(),
                    ))

        self.mesh.on_message(MessageType.WANT_BLOCK, on_want_block)
        self.mesh.on_message(MessageType.HAVE_BLOCK, on_have_block)
        self.mesh.on_message(MessageType.MANIFEST_REQUEST, on_manifest_request)

    def _bootstrap(self) -> None:
        """Connect to bootstrap peers from config."""
        for bp in self.config.bootstrap_peers:
            host = bp.get("host", "")
            port = bp.get("port", 0)
            if host and port:
                self.add_peer(host, port)
