"""
Peer Management and Discovery.

Manages peer identities, connections, and discovery within the Clio mesh network.
Each peer is identified by a unique PeerID derived from its public key, similar
to libp2p's peer identification scheme.

Peer lifecycle:
    Discovery → Handshake → Connected → Exchange → Disconnect
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from src.network.content_store import CID


class PeerState(Enum):
    DISCOVERED = "discovered"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    EXCHANGING = "exchanging"
    DISCONNECTED = "disconnected"
    BANNED = "banned"


@dataclass
class PeerID:
    """Unique identifier for a network peer."""

    id_hex: str

    @classmethod
    def generate(cls) -> PeerID:
        """Generate a new random PeerID."""
        raw = secrets.token_bytes(32)
        return cls(id_hex=hashlib.sha256(raw).hexdigest())

    @classmethod
    def from_public_key(cls, public_key: bytes) -> PeerID:
        """Derive PeerID from a public key."""
        return cls(id_hex=hashlib.sha256(public_key).hexdigest())

    def __str__(self) -> str:
        return f"peer:{self.id_hex[:12]}"

    def __hash__(self) -> int:
        return hash(self.id_hex)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PeerID):
            return NotImplemented
        return self.id_hex == other.id_hex

    def distance(self, other: PeerID) -> int:
        """XOR distance for Kademlia-style routing."""
        a = int(self.id_hex, 16)
        b = int(other.id_hex, 16)
        return a ^ b


@dataclass
class PeerAddress:
    """Network address of a peer."""

    host: str
    port: int
    protocol: str = "tcp"

    def __str__(self) -> str:
        return f"/{self.protocol}/{self.host}/{self.port}"


@dataclass
class PeerInfo:
    """Complete information about a known peer."""

    peer_id: PeerID
    addresses: list[PeerAddress] = field(default_factory=list)
    state: PeerState = PeerState.DISCOVERED
    # Content availability: which CIDs this peer has announced
    available_cids: set[CID] = field(default_factory=set)
    # Performance metrics
    latency_ms: float = 0.0
    bandwidth_bps: float = 0.0
    upload_total: int = 0
    download_total: int = 0
    # Reputation
    reputation_score: float = 1.0
    successful_transfers: int = 0
    failed_transfers: int = 0
    # Timestamps
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    last_connected: float = 0.0

    def update_seen(self) -> None:
        self.last_seen = time.time()

    def record_success(self, bytes_transferred: int) -> None:
        self.successful_transfers += 1
        self.download_total += bytes_transferred
        self._update_reputation()

    def record_failure(self) -> None:
        self.failed_transfers += 1
        self._update_reputation()

    def _update_reputation(self) -> None:
        total = self.successful_transfers + self.failed_transfers
        if total > 0:
            self.reputation_score = self.successful_transfers / total

    @property
    def is_available(self) -> bool:
        return self.state in (PeerState.CONNECTED, PeerState.EXCHANGING)


class PeerStore:
    """
    Manages the local peer registry.

    Tracks known peers, their states, capabilities, and reputation.
    Provides peer selection strategies for efficient content retrieval.
    """

    def __init__(self, local_peer_id: PeerID | None = None, max_peers: int = 200):
        self.local_id = local_peer_id or PeerID.generate()
        self.max_peers = max_peers
        self._peers: dict[PeerID, PeerInfo] = {}
        self._cid_providers: dict[CID, set[PeerID]] = {}
        self._on_peer_added: list[Callable[[PeerInfo], None]] = []
        self._on_peer_removed: list[Callable[[PeerID], None]] = []

    def add_peer(self, peer_info: PeerInfo) -> bool:
        """Add or update a peer in the store."""
        if peer_info.peer_id == self.local_id:
            return False

        is_new = peer_info.peer_id not in self._peers
        if is_new and len(self._peers) >= self.max_peers:
            self._evict_worst_peer()

        self._peers[peer_info.peer_id] = peer_info
        peer_info.update_seen()

        # Update CID provider index
        for cid in peer_info.available_cids:
            if cid not in self._cid_providers:
                self._cid_providers[cid] = set()
            self._cid_providers[cid].add(peer_info.peer_id)

        if is_new:
            for callback in self._on_peer_added:
                callback(peer_info)
        return True

    def remove_peer(self, peer_id: PeerID) -> None:
        """Remove a peer from the store."""
        peer = self._peers.pop(peer_id, None)
        if peer:
            for cid in peer.available_cids:
                providers = self._cid_providers.get(cid)
                if providers:
                    providers.discard(peer_id)
                    if not providers:
                        del self._cid_providers[cid]
            for callback in self._on_peer_removed:
                callback(peer_id)

    def get_peer(self, peer_id: PeerID) -> PeerInfo | None:
        return self._peers.get(peer_id)

    def get_connected_peers(self) -> list[PeerInfo]:
        return [p for p in self._peers.values() if p.is_available]

    def get_all_peers(self) -> list[PeerInfo]:
        return list(self._peers.values())

    def announce_cid(self, peer_id: PeerID, cid: CID) -> None:
        """Record that a peer has announced availability of a CID."""
        peer = self._peers.get(peer_id)
        if peer:
            peer.available_cids.add(cid)
            if cid not in self._cid_providers:
                self._cid_providers[cid] = set()
            self._cid_providers[cid].add(peer_id)

    def find_providers(self, cid: CID) -> list[PeerInfo]:
        """Find peers that have announced a given CID, sorted by reputation."""
        provider_ids = self._cid_providers.get(cid, set())
        providers = [self._peers[pid] for pid in provider_ids if pid in self._peers]
        return sorted(providers, key=lambda p: p.reputation_score, reverse=True)

    def find_closest_peers(self, target_id: PeerID, count: int = 20) -> list[PeerInfo]:
        """Find peers closest to a target ID (Kademlia-style)."""
        peers = list(self._peers.values())
        peers.sort(key=lambda p: p.peer_id.distance(target_id))
        return peers[:count]

    def select_best_peers(self, cids: list[CID], count: int = 5) -> list[PeerInfo]:
        """
        Select the best peers for downloading a set of CIDs.

        Considers: content availability, reputation, latency, and bandwidth.
        """
        peer_scores: dict[PeerID, float] = {}

        for cid in cids:
            for peer in self.find_providers(cid):
                pid = peer.peer_id
                if pid not in peer_scores:
                    peer_scores[pid] = 0.0
                # Score: availability coverage * reputation * bandwidth factor
                bw_factor = min(peer.bandwidth_bps / 1_000_000, 10.0) if peer.bandwidth_bps > 0 else 1.0
                peer_scores[pid] += peer.reputation_score * bw_factor

        ranked = sorted(peer_scores.items(), key=lambda x: x[1], reverse=True)
        return [self._peers[pid] for pid, _ in ranked[:count] if pid in self._peers]

    def update_peer_state(self, peer_id: PeerID, state: PeerState) -> None:
        peer = self._peers.get(peer_id)
        if peer:
            peer.state = state
            if state == PeerState.CONNECTED:
                peer.last_connected = time.time()

    def on_peer_added(self, callback: Callable[[PeerInfo], None]) -> None:
        self._on_peer_added.append(callback)

    def on_peer_removed(self, callback: Callable[[PeerID], None]) -> None:
        self._on_peer_removed.append(callback)

    def _evict_worst_peer(self) -> None:
        """Evict the peer with lowest reputation that is disconnected."""
        candidates = [
            p for p in self._peers.values()
            if p.state == PeerState.DISCONNECTED
        ]
        if not candidates:
            candidates = list(self._peers.values())
        worst = min(candidates, key=lambda p: p.reputation_score)
        self.remove_peer(worst.peer_id)

    @property
    def peer_count(self) -> int:
        return len(self._peers)

    @property
    def connected_count(self) -> int:
        return len(self.get_connected_peers())
