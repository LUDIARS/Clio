"""
Torrent-like Chunked Transfer Protocol.

Implements BitTorrent-inspired piece-based file transfer with:
    - Piece selection strategies (rarest-first, sequential, random)
    - Swarm management (multiple peers contributing to a single download)
    - Bitfield tracking (which pieces each peer has)
    - Tit-for-tat incentive mechanism
    - Endgame mode for final pieces

Transfer flow:
    1. Receive manifest (equivalent to .torrent metainfo)
    2. Build bitfield from locally available chunks
    3. Connect to swarm peers
    4. Exchange bitfields, request missing pieces (rarest-first)
    5. Verify each piece via CID, update bitfield
    6. Seed completed content to other peers
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from src.network.content_store import CID, Chunk, ContentManifest, ContentStore
from src.network.peer import PeerID, PeerInfo


class PieceState(Enum):
    MISSING = "missing"
    REQUESTED = "requested"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    VERIFIED = "verified"


class SelectionStrategy(Enum):
    RAREST_FIRST = "rarest_first"
    SEQUENTIAL = "sequential"
    RANDOM = "random"
    ENDGAME = "endgame"


@dataclass
class Bitfield:
    """Tracks which pieces a peer has (1) or doesn't have (0)."""

    size: int
    _bits: bytearray = field(default=None, repr=False)

    def __post_init__(self):
        if self._bits is None:
            byte_count = (self.size + 7) // 8
            self._bits = bytearray(byte_count)

    def set(self, index: int) -> None:
        byte_idx, bit_idx = divmod(index, 8)
        self._bits[byte_idx] |= (1 << (7 - bit_idx))

    def clear(self, index: int) -> None:
        byte_idx, bit_idx = divmod(index, 8)
        self._bits[byte_idx] &= ~(1 << (7 - bit_idx))

    def has(self, index: int) -> bool:
        byte_idx, bit_idx = divmod(index, 8)
        return bool(self._bits[byte_idx] & (1 << (7 - bit_idx)))

    @property
    def completed_count(self) -> int:
        return sum(1 for i in range(self.size) if self.has(i))

    @property
    def is_complete(self) -> bool:
        return self.completed_count == self.size

    @property
    def progress(self) -> float:
        return self.completed_count / self.size if self.size > 0 else 0.0

    def missing_indices(self) -> list[int]:
        return [i for i in range(self.size) if not self.has(i)]

    def available_indices(self) -> list[int]:
        return [i for i in range(self.size) if self.has(i)]

    def to_bytes(self) -> bytes:
        return bytes(self._bits)

    @classmethod
    def from_bytes(cls, data: bytes, size: int) -> Bitfield:
        bf = cls(size=size)
        bf._bits = bytearray(data)
        return bf

    @classmethod
    def full(cls, size: int) -> Bitfield:
        bf = cls(size=size)
        for i in range(size):
            bf.set(i)
        return bf


@dataclass
class PieceRequest:
    """A request for a specific piece from a specific peer."""

    piece_index: int
    cid: CID
    peer_id: PeerID
    requested_at: float = field(default_factory=time.time)
    timeout: float = 30.0

    @property
    def is_expired(self) -> bool:
        return time.time() - self.requested_at > self.timeout


@dataclass
class SwarmPeer:
    """A peer participating in a content swarm."""

    peer_id: PeerID
    peer_info: PeerInfo
    bitfield: Bitfield | None = None
    am_choking: bool = True       # Are we choking this peer?
    am_interested: bool = False   # Are we interested in this peer?
    peer_choking: bool = True     # Is this peer choking us?
    peer_interested: bool = False # Is this peer interested in us?
    uploaded: int = 0
    downloaded: int = 0
    upload_rate: float = 0.0
    download_rate: float = 0.0
    last_unchoke: float = 0.0

    @property
    def has_useful_pieces(self) -> bool:
        return self.bitfield is not None and self.bitfield.completed_count > 0


class PieceSelector:
    """Selects which pieces to request next based on strategy."""

    def __init__(self, strategy: SelectionStrategy = SelectionStrategy.RAREST_FIRST):
        self.strategy = strategy

    def select(
        self,
        local_bitfield: Bitfield,
        swarm_peers: list[SwarmPeer],
        active_requests: dict[int, PieceRequest],
        count: int = 5,
    ) -> list[tuple[int, PeerID]]:
        """Select pieces to request and which peer to request from."""
        missing = set(local_bitfield.missing_indices())
        in_flight = set(active_requests.keys())
        requestable = missing - in_flight

        if not requestable:
            # Endgame: re-request from alternate peers
            if missing and self.strategy != SelectionStrategy.ENDGAME:
                return self._endgame_select(missing, swarm_peers, active_requests, count)
            return []

        if self.strategy == SelectionStrategy.RAREST_FIRST:
            return self._rarest_first(requestable, swarm_peers, count)
        elif self.strategy == SelectionStrategy.SEQUENTIAL:
            return self._sequential(requestable, swarm_peers, count)
        elif self.strategy == SelectionStrategy.RANDOM:
            return self._random(requestable, swarm_peers, count)
        else:
            return self._rarest_first(requestable, swarm_peers, count)

    def _rarest_first(
        self,
        requestable: set[int],
        peers: list[SwarmPeer],
        count: int,
    ) -> list[tuple[int, PeerID]]:
        """Select the rarest pieces first - maximizes swarm health."""
        # Count availability of each piece across the swarm
        piece_availability: dict[int, list[SwarmPeer]] = {}
        for idx in requestable:
            available_peers = [
                p for p in peers
                if p.bitfield and p.bitfield.has(idx) and not p.peer_choking
            ]
            if available_peers:
                piece_availability[idx] = available_peers

        # Sort by rarity (fewest providers first)
        sorted_pieces = sorted(piece_availability.items(), key=lambda x: len(x[1]))

        result = []
        for piece_idx, available_peers in sorted_pieces[:count]:
            # Pick the peer with best reputation
            best = max(available_peers, key=lambda p: p.peer_info.reputation_score)
            result.append((piece_idx, best.peer_id))
        return result

    def _sequential(
        self,
        requestable: set[int],
        peers: list[SwarmPeer],
        count: int,
    ) -> list[tuple[int, PeerID]]:
        """Select pieces in order - useful for streaming."""
        sorted_indices = sorted(requestable)
        result = []
        for idx in sorted_indices[:count]:
            available = [
                p for p in peers
                if p.bitfield and p.bitfield.has(idx) and not p.peer_choking
            ]
            if available:
                best = max(available, key=lambda p: p.peer_info.reputation_score)
                result.append((idx, best.peer_id))
        return result

    def _random(
        self,
        requestable: set[int],
        peers: list[SwarmPeer],
        count: int,
    ) -> list[tuple[int, PeerID]]:
        """Random selection - used during initial download phase."""
        indices = list(requestable)
        random.shuffle(indices)
        result = []
        for idx in indices[:count]:
            available = [
                p for p in peers
                if p.bitfield and p.bitfield.has(idx) and not p.peer_choking
            ]
            if available:
                peer = random.choice(available)
                result.append((idx, peer.peer_id))
        return result

    def _endgame_select(
        self,
        missing: set[int],
        peers: list[SwarmPeer],
        active_requests: dict[int, PieceRequest],
        count: int,
    ) -> list[tuple[int, PeerID]]:
        """Endgame mode: request missing pieces from alternate peers."""
        result = []
        for idx in list(missing)[:count]:
            current_req = active_requests.get(idx)
            available = [
                p for p in peers
                if p.bitfield and p.bitfield.has(idx) and not p.peer_choking
                and (current_req is None or p.peer_id != current_req.peer_id)
            ]
            if available:
                peer = random.choice(available)
                result.append((idx, peer.peer_id))
        return result


class TorrentTransfer:
    """
    Manages a single torrent-like content transfer.

    Coordinates downloading a content item from multiple peers simultaneously,
    using piece selection strategy, bitfield exchange, and piece verification.
    """

    def __init__(
        self,
        manifest: ContentManifest,
        content_store: ContentStore,
        strategy: SelectionStrategy = SelectionStrategy.RAREST_FIRST,
        max_concurrent_requests: int = 10,
        request_timeout: float = 30.0,
    ):
        self.manifest = manifest
        self.content_store = content_store
        self.selector = PieceSelector(strategy)
        self.max_concurrent = max_concurrent_requests
        self.request_timeout = request_timeout

        # Build local bitfield from content store
        self.local_bitfield = Bitfield(size=manifest.chunk_count)
        for i, cid in enumerate(manifest.chunk_cids):
            if content_store.has(cid):
                self.local_bitfield.set(i)

        # Swarm management
        self._swarm: dict[PeerID, SwarmPeer] = {}
        self._active_requests: dict[int, PieceRequest] = {}

        # Stats
        self.started_at: float = time.time()
        self.completed_at: float | None = None
        self.total_downloaded: int = 0
        self.total_uploaded: int = 0

        # Callbacks
        self._on_piece_complete: list[Callable[[int, CID], None]] = []
        self._on_transfer_complete: list[Callable[[ContentManifest], None]] = []

    @property
    def is_complete(self) -> bool:
        return self.local_bitfield.is_complete

    @property
    def progress(self) -> float:
        return self.local_bitfield.progress

    @property
    def is_seeding(self) -> bool:
        return self.is_complete

    def add_peer(self, peer_info: PeerInfo, bitfield: Bitfield | None = None) -> None:
        """Add a peer to the swarm."""
        swarm_peer = SwarmPeer(
            peer_id=peer_info.peer_id,
            peer_info=peer_info,
            bitfield=bitfield,
        )
        self._swarm[peer_info.peer_id] = swarm_peer

    def remove_peer(self, peer_id: PeerID) -> None:
        """Remove a peer from the swarm."""
        self._swarm.pop(peer_id, None)
        # Cancel requests to this peer
        expired = [
            idx for idx, req in self._active_requests.items()
            if req.peer_id == peer_id
        ]
        for idx in expired:
            del self._active_requests[idx]

    def update_peer_bitfield(self, peer_id: PeerID, bitfield: Bitfield) -> None:
        """Update a swarm peer's bitfield."""
        peer = self._swarm.get(peer_id)
        if peer:
            peer.bitfield = bitfield

    def peer_has_piece(self, peer_id: PeerID, piece_index: int) -> None:
        """Record that a peer has a specific piece (HAVE message)."""
        peer = self._swarm.get(peer_id)
        if peer:
            if peer.bitfield is None:
                peer.bitfield = Bitfield(size=self.manifest.chunk_count)
            peer.bitfield.set(piece_index)

    def unchoke_peer(self, peer_id: PeerID) -> None:
        peer = self._swarm.get(peer_id)
        if peer:
            peer.peer_choking = False

    def choke_peer(self, peer_id: PeerID) -> None:
        peer = self._swarm.get(peer_id)
        if peer:
            peer.peer_choking = True

    def get_next_requests(self) -> list[PieceRequest]:
        """Get the next batch of piece requests to send."""
        self._cleanup_expired_requests()

        available_slots = self.max_concurrent - len(self._active_requests)
        if available_slots <= 0:
            return []

        selections = self.selector.select(
            self.local_bitfield,
            list(self._swarm.values()),
            self._active_requests,
            count=available_slots,
        )

        requests = []
        for piece_idx, peer_id in selections:
            cid = self.manifest.chunk_cids[piece_idx]
            req = PieceRequest(
                piece_index=piece_idx,
                cid=cid,
                peer_id=peer_id,
                timeout=self.request_timeout,
            )
            self._active_requests[piece_idx] = req
            requests.append(req)
        return requests

    def receive_piece(self, piece_index: int, chunk: Chunk) -> bool:
        """
        Process a received piece.

        Verifies the chunk's CID matches the expected CID from the manifest,
        stores it, and updates the bitfield.
        """
        if piece_index >= self.manifest.chunk_count:
            return False

        expected_cid = self.manifest.chunk_cids[piece_index]
        if chunk.cid != expected_cid:
            # Piece verification failed - data corruption or malicious peer
            req = self._active_requests.get(piece_index)
            if req:
                peer = self._swarm.get(req.peer_id)
                if peer:
                    peer.peer_info.record_failure()
            return False

        # Store the verified chunk
        self.content_store.put_chunk(chunk)
        self.local_bitfield.set(piece_index)
        self.total_downloaded += chunk.size

        # Update peer stats
        req = self._active_requests.pop(piece_index, None)
        if req:
            peer = self._swarm.get(req.peer_id)
            if peer:
                peer.downloaded += chunk.size
                peer.peer_info.record_success(chunk.size)

        # Notify callbacks
        for callback in self._on_piece_complete:
            callback(piece_index, expected_cid)

        # Check completion
        if self.is_complete:
            self.completed_at = time.time()
            for callback in self._on_transfer_complete:
                callback(self.manifest)

        return True

    def get_piece_for_peer(self, peer_id: PeerID, piece_index: int) -> Chunk | None:
        """Serve a piece to a requesting peer (upload/seeding)."""
        if not self.local_bitfield.has(piece_index):
            return None

        cid = self.manifest.chunk_cids[piece_index]
        chunk = self.content_store.get_chunk(cid)
        if chunk:
            self.total_uploaded += chunk.size
            peer = self._swarm.get(peer_id)
            if peer:
                peer.uploaded += chunk.size
        return chunk

    def on_piece_complete(self, callback: Callable[[int, CID], None]) -> None:
        self._on_piece_complete.append(callback)

    def on_transfer_complete(self, callback: Callable[[ContentManifest], None]) -> None:
        self._on_transfer_complete.append(callback)

    def _cleanup_expired_requests(self) -> None:
        """Remove expired piece requests."""
        expired = [
            idx for idx, req in self._active_requests.items()
            if req.is_expired
        ]
        for idx in expired:
            req = self._active_requests.pop(idx)
            peer = self._swarm.get(req.peer_id)
            if peer:
                peer.peer_info.record_failure()

    def get_stats(self) -> dict:
        elapsed = time.time() - self.started_at
        return {
            "root_cid": str(self.manifest.root_cid),
            "name": self.manifest.name,
            "progress": f"{self.progress:.1%}",
            "pieces": f"{self.local_bitfield.completed_count}/{self.manifest.chunk_count}",
            "downloaded": self.total_downloaded,
            "uploaded": self.total_uploaded,
            "swarm_size": len(self._swarm),
            "active_requests": len(self._active_requests),
            "elapsed_seconds": round(elapsed, 1),
            "is_seeding": self.is_seeding,
        }


class TorrentManager:
    """
    Manages multiple concurrent torrent transfers.

    Provides the high-level API for creating, monitoring, and controlling
    torrent-style downloads and uploads.
    """

    def __init__(self, content_store: ContentStore):
        self.content_store = content_store
        self._transfers: dict[CID, TorrentTransfer] = {}

    def create_transfer(
        self,
        manifest: ContentManifest,
        strategy: SelectionStrategy = SelectionStrategy.RAREST_FIRST,
    ) -> TorrentTransfer:
        """Create a new transfer for a content manifest."""
        transfer = TorrentTransfer(
            manifest=manifest,
            content_store=self.content_store,
            strategy=strategy,
        )
        self._transfers[manifest.root_cid] = transfer
        self.content_store.register_manifest(manifest)
        return transfer

    def get_transfer(self, root_cid: CID) -> TorrentTransfer | None:
        return self._transfers.get(root_cid)

    def remove_transfer(self, root_cid: CID) -> None:
        self._transfers.pop(root_cid, None)

    def get_active_transfers(self) -> list[TorrentTransfer]:
        return [t for t in self._transfers.values() if not t.is_complete]

    def get_seeding_transfers(self) -> list[TorrentTransfer]:
        return [t for t in self._transfers.values() if t.is_seeding]

    def get_all_stats(self) -> list[dict]:
        return [t.get_stats() for t in self._transfers.values()]
