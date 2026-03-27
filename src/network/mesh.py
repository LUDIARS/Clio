"""
Mesh Network Topology and Routing.

Implements a gossip-based mesh network for peer discovery, content announcement,
and message propagation. Combines structured (Kademlia DHT) and unstructured
(gossip) approaches for resilient content routing.

Topology:
    - Each node maintains connections to K nearest peers (structured overlay)
    - Content announcements propagate via gossip protocol
    - DHT provides deterministic content routing as fallback
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from src.network.content_store import CID
from src.network.peer import PeerID, PeerInfo, PeerState, PeerStore


class MessageType(Enum):
    # Peer discovery
    PING = "ping"
    PONG = "pong"
    FIND_PEER = "find_peer"
    PEER_LIST = "peer_list"
    # Content routing
    ANNOUNCE = "announce"
    FIND_CONTENT = "find_content"
    CONTENT_FOUND = "content_found"
    # Data transfer coordination
    WANT_BLOCK = "want_block"
    HAVE_BLOCK = "have_block"
    BLOCK_DATA = "block_data"
    # Manifest exchange
    MANIFEST_REQUEST = "manifest_request"
    MANIFEST_RESPONSE = "manifest_response"


@dataclass
class MeshMessage:
    """Message passed between peers in the mesh network."""

    msg_type: MessageType
    sender: PeerID
    payload: dict = field(default_factory=dict)
    msg_id: str = ""
    ttl: int = 7
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        if not self.msg_id:
            raw = f"{self.sender.id_hex}{self.msg_type.value}{self.timestamp}".encode()
            self.msg_id = hashlib.sha256(raw).hexdigest()[:16]

    def should_forward(self) -> bool:
        return self.ttl > 0

    def forwarded(self) -> MeshMessage:
        """Create a forwarded copy with decremented TTL."""
        return MeshMessage(
            msg_type=self.msg_type,
            sender=self.sender,
            payload=self.payload,
            msg_id=self.msg_id,
            ttl=self.ttl - 1,
            timestamp=self.timestamp,
        )


# Kademlia DHT parameters
K_BUCKET_SIZE = 20
ALPHA_CONCURRENCY = 3
ID_BITS = 256


@dataclass
class KBucket:
    """A k-bucket in the Kademlia routing table."""

    peers: list[PeerID] = field(default_factory=list)
    max_size: int = K_BUCKET_SIZE
    last_updated: float = field(default_factory=time.time)

    def add(self, peer_id: PeerID) -> bool:
        if peer_id in self.peers:
            self.peers.remove(peer_id)
            self.peers.append(peer_id)
            self.last_updated = time.time()
            return True
        if len(self.peers) < self.max_size:
            self.peers.append(peer_id)
            self.last_updated = time.time()
            return True
        return False

    def remove(self, peer_id: PeerID) -> None:
        if peer_id in self.peers:
            self.peers.remove(peer_id)

    @property
    def is_full(self) -> bool:
        return len(self.peers) >= self.max_size


class RoutingTable:
    """
    Kademlia-style routing table.

    Organizes peers into k-buckets based on XOR distance from local node.
    Provides O(log n) lookups for any content or peer in the network.
    """

    def __init__(self, local_id: PeerID):
        self.local_id = local_id
        self.buckets: list[KBucket] = [KBucket() for _ in range(ID_BITS)]

    def _bucket_index(self, peer_id: PeerID) -> int:
        """Determine which k-bucket a peer belongs to."""
        distance = self.local_id.distance(peer_id)
        if distance == 0:
            return 0
        return distance.bit_length() - 1

    def add_peer(self, peer_id: PeerID) -> bool:
        if peer_id == self.local_id:
            return False
        idx = self._bucket_index(peer_id)
        idx = min(idx, len(self.buckets) - 1)
        return self.buckets[idx].add(peer_id)

    def remove_peer(self, peer_id: PeerID) -> None:
        idx = self._bucket_index(peer_id)
        idx = min(idx, len(self.buckets) - 1)
        self.buckets[idx].remove(peer_id)

    def find_closest(self, target: PeerID, count: int = K_BUCKET_SIZE) -> list[PeerID]:
        """Find the closest peers to a target ID."""
        all_peers = []
        for bucket in self.buckets:
            all_peers.extend(bucket.peers)
        all_peers.sort(key=lambda p: p.distance(target))
        return all_peers[:count]

    @property
    def total_peers(self) -> int:
        return sum(len(b.peers) for b in self.buckets)


class MeshNetwork:
    """
    Mesh network overlay combining gossip and DHT for content routing.

    Responsibilities:
        - Peer discovery via gossip and DHT lookups
        - Content announcement propagation
        - Message routing between peers
        - Network health monitoring
    """

    def __init__(self, peer_store: PeerStore, max_gossip_peers: int = 8):
        self.peer_store = peer_store
        self.local_id = peer_store.local_id
        self.routing_table = RoutingTable(self.local_id)
        self.max_gossip_peers = max_gossip_peers

        # Message deduplication
        self._seen_messages: dict[str, float] = {}
        self._message_ttl = 300.0  # 5 minutes

        # Content routing table: CID -> set of provider PeerIDs
        self._content_providers: dict[CID, set[PeerID]] = {}

        # Message handlers
        self._handlers: dict[MessageType, list[Callable[[MeshMessage], None]]] = {}

        # Outbound message queue (consumed by transport layer)
        self._outbox: list[tuple[PeerID, MeshMessage]] = []

    def on_message(self, msg_type: MessageType, handler: Callable[[MeshMessage], None]) -> None:
        """Register a handler for a message type."""
        if msg_type not in self._handlers:
            self._handlers[msg_type] = []
        self._handlers[msg_type].append(handler)

    def handle_message(self, message: MeshMessage) -> None:
        """Process an incoming mesh message."""
        # Dedup
        if message.msg_id in self._seen_messages:
            return
        self._seen_messages[message.msg_id] = time.time()

        # Update routing table
        self.routing_table.add_peer(message.sender)

        # Dispatch to handlers
        handlers = self._handlers.get(message.msg_type, [])
        for handler in handlers:
            handler(message)

        # Handle built-in message types
        self._handle_builtin(message)

        # Gossip forward if TTL allows
        if message.should_forward() and message.msg_type in (
            MessageType.ANNOUNCE,
            MessageType.FIND_CONTENT,
            MessageType.FIND_PEER,
        ):
            self._gossip_forward(message)

    def _handle_builtin(self, message: MeshMessage) -> None:
        """Handle built-in protocol messages."""
        if message.msg_type == MessageType.PING:
            self._send(message.sender, MeshMessage(
                msg_type=MessageType.PONG,
                sender=self.local_id,
            ))

        elif message.msg_type == MessageType.FIND_PEER:
            target_hex = message.payload.get("target_id", "")
            target = PeerID(id_hex=target_hex)
            closest = self.routing_table.find_closest(target)
            peers_data = [
                {"id": p.id_hex}
                for p in closest
            ]
            self._send(message.sender, MeshMessage(
                msg_type=MessageType.PEER_LIST,
                sender=self.local_id,
                payload={"peers": peers_data},
            ))

        elif message.msg_type == MessageType.ANNOUNCE:
            cid_hex = message.payload.get("cid_hash", "")
            cid = CID(hash_hex=cid_hex)
            if cid not in self._content_providers:
                self._content_providers[cid] = set()
            self._content_providers[cid].add(message.sender)
            self.peer_store.announce_cid(message.sender, cid)

        elif message.msg_type == MessageType.FIND_CONTENT:
            cid_hex = message.payload.get("cid_hash", "")
            cid = CID(hash_hex=cid_hex)
            providers = self._content_providers.get(cid, set())
            if providers:
                self._send(message.sender, MeshMessage(
                    msg_type=MessageType.CONTENT_FOUND,
                    sender=self.local_id,
                    payload={
                        "cid_hash": cid_hex,
                        "providers": [p.id_hex for p in providers],
                    },
                ))

    def announce_content(self, cid: CID) -> None:
        """Announce to the network that we have content for a given CID."""
        if cid not in self._content_providers:
            self._content_providers[cid] = set()
        self._content_providers[cid].add(self.local_id)

        msg = MeshMessage(
            msg_type=MessageType.ANNOUNCE,
            sender=self.local_id,
            payload={"cid_hash": cid.hash_hex},
        )
        self._gossip_broadcast(msg)

    def find_content(self, cid: CID) -> None:
        """Request the network to locate providers for a CID."""
        msg = MeshMessage(
            msg_type=MessageType.FIND_CONTENT,
            sender=self.local_id,
            payload={"cid_hash": cid.hash_hex},
        )
        # Send to closest peers in DHT
        target_peer_id = PeerID(id_hex=cid.hash_hex)
        closest = self.routing_table.find_closest(target_peer_id)
        for peer_id in closest[:ALPHA_CONCURRENCY]:
            self._send(peer_id, msg)
        # Also gossip broadcast
        self._gossip_broadcast(msg)

    def discover_peers(self, target: PeerID | None = None) -> None:
        """Initiate peer discovery for a target or random ID."""
        target = target or PeerID.generate()
        msg = MeshMessage(
            msg_type=MessageType.FIND_PEER,
            sender=self.local_id,
            payload={"target_id": target.id_hex},
        )
        closest = self.routing_table.find_closest(target)
        for peer_id in closest[:ALPHA_CONCURRENCY]:
            self._send(peer_id, msg)

    def get_content_providers(self, cid: CID) -> list[PeerInfo]:
        """Get known providers for a CID."""
        provider_ids = self._content_providers.get(cid, set())
        providers = []
        for pid in provider_ids:
            peer = self.peer_store.get_peer(pid)
            if peer and peer.is_available:
                providers.append(peer)
        return sorted(providers, key=lambda p: p.reputation_score, reverse=True)

    def _gossip_broadcast(self, message: MeshMessage) -> None:
        """Broadcast a message to gossip peers."""
        connected = self.peer_store.get_connected_peers()
        targets = sorted(connected, key=lambda p: p.reputation_score, reverse=True)
        for peer in targets[: self.max_gossip_peers]:
            self._send(peer.peer_id, message)

    def _gossip_forward(self, message: MeshMessage) -> None:
        """Forward a gossip message to a subset of connected peers."""
        forwarded = message.forwarded()
        connected = self.peer_store.get_connected_peers()
        # Forward to a random subset, excluding sender
        targets = [p for p in connected if p.peer_id != message.sender]
        for peer in targets[: self.max_gossip_peers // 2]:
            self._send(peer.peer_id, forwarded)

    def _send(self, target: PeerID, message: MeshMessage) -> None:
        """Queue a message for sending."""
        self._outbox.append((target, message))

    def drain_outbox(self) -> list[tuple[PeerID, MeshMessage]]:
        """Drain and return all queued outbound messages."""
        messages = list(self._outbox)
        self._outbox.clear()
        return messages

    def cleanup_seen_messages(self) -> int:
        """Remove expired message IDs from dedup cache."""
        now = time.time()
        expired = [
            mid for mid, ts in self._seen_messages.items()
            if now - ts > self._message_ttl
        ]
        for mid in expired:
            del self._seen_messages[mid]
        return len(expired)

    def get_network_stats(self) -> dict:
        return {
            "local_id": str(self.local_id),
            "routing_table_size": self.routing_table.total_peers,
            "connected_peers": self.peer_store.connected_count,
            "total_peers": self.peer_store.peer_count,
            "tracked_content": len(self._content_providers),
            "pending_messages": len(self._outbox),
            "seen_messages": len(self._seen_messages),
        }
