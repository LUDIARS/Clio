"""
Clio Network Module - IPFS-like content-addressed storage with torrent-style P2P transfer.

Components:
    - content_store: Content-addressed storage with CID-based chunk management
    - peer: Peer identity, discovery, and connection management
    - mesh: Mesh network topology and routing
    - torrent: Torrent-like chunked transfer protocol with piece verification
    - stream: File streaming over the P2P network
    - coordinator: Orchestration layer integrating all network components
"""

from src.network.coordinator import NetworkCoordinator

__all__ = ["NetworkCoordinator"]
