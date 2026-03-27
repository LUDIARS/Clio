"""Tests for Clio Network Module - IPFS/Torrent P2P resource sharing."""

import tempfile
from pathlib import Path

import pytest

from src.network.content_store import (
    CID,
    Chunk,
    Chunker,
    ContentStore,
    HashAlgorithm,
)
from src.network.peer import PeerAddress, PeerID, PeerInfo, PeerState, PeerStore
from src.network.mesh import (
    KBucket,
    MeshMessage,
    MeshNetwork,
    MessageType,
    RoutingTable,
)
from src.network.torrent import (
    Bitfield,
    PieceSelector,
    SelectionStrategy,
    SwarmPeer,
    TorrentManager,
    TorrentTransfer,
)
from src.network.stream import FileStream, StreamBuffer, StreamManager
from src.network.coordinator import NetworkConfig, NetworkCoordinator


# ──────────────────────────────────────────────────
# Content Store Tests
# ──────────────────────────────────────────────────


class TestCID:
    def test_from_bytes(self):
        cid = CID.from_bytes(b"hello world")
        assert cid.hash_hex
        assert cid.algorithm == HashAlgorithm.SHA256

    def test_deterministic(self):
        cid1 = CID.from_bytes(b"test data")
        cid2 = CID.from_bytes(b"test data")
        assert cid1 == cid2

    def test_different_data(self):
        cid1 = CID.from_bytes(b"data a")
        cid2 = CID.from_bytes(b"data b")
        assert cid1 != cid2

    def test_blake2b(self):
        cid = CID.from_bytes(b"hello", algorithm=HashAlgorithm.BLAKE2B)
        assert cid.algorithm == HashAlgorithm.BLAKE2B

    def test_str(self):
        cid = CID.from_bytes(b"test")
        s = str(cid)
        assert s.startswith("Qm")


class TestChunker:
    def test_chunk_bytes(self):
        data = b"x" * 1000
        chunker = Chunker(chunk_size=256)
        chunks = chunker.chunk_bytes(data)
        assert len(chunks) == 4  # 256 + 256 + 256 + 232
        assert sum(c.size for c in chunks) == 1000

    def test_chunk_indices(self):
        data = b"a" * 600
        chunker = Chunker(chunk_size=256)
        chunks = chunker.chunk_bytes(data)
        for i, chunk in enumerate(chunks):
            assert chunk.index == i

    def test_chunk_verify(self):
        data = b"verify me"
        chunker = Chunker(chunk_size=256)
        chunks = chunker.chunk_bytes(data)
        for chunk in chunks:
            assert chunk.verify()

    def test_chunk_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"file content " * 100)
            path = Path(f.name)

        chunker = Chunker(chunk_size=256)
        chunks = list(chunker.chunk_file(path))
        assert len(chunks) > 0
        total = sum(c.size for c in chunks)
        assert total == path.stat().st_size
        path.unlink()


class TestContentStore:
    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        self.store = ContentStore(
            store_path=Path(self._tmpdir) / "blocks",
            chunk_size=256,
        )

    def test_put_get_chunk(self):
        chunk = Chunk.create(b"hello world", index=0)
        self.store.put_chunk(chunk)
        assert self.store.has(chunk.cid)
        retrieved = self.store.get_chunk(chunk.cid)
        assert retrieved is not None
        assert retrieved.data == b"hello world"

    def test_ingest_bytes(self):
        data = b"test content " * 50
        manifest = self.store.ingest_bytes(data, name="test.bin")
        assert manifest.name == "test.bin"
        assert manifest.total_size == len(data)
        assert manifest.chunk_count > 0

    def test_reassemble(self):
        data = b"reassemble me " * 100
        manifest = self.store.ingest_bytes(data, name="test.bin")
        reassembled = self.store.reassemble(manifest)
        assert reassembled == data

    def test_ingest_file(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".dat") as f:
            content = b"file data " * 200
            f.write(content)
            path = Path(f.name)

        manifest = self.store.ingest_file(path, name="resource.dat")
        assert manifest.total_size == len(content)

        reassembled = self.store.reassemble(manifest)
        assert reassembled == content
        path.unlink()

    def test_reassemble_to_file(self):
        data = b"output test " * 50
        manifest = self.store.ingest_bytes(data)

        output_path = Path(self._tmpdir) / "output" / "result.bin"
        self.store.reassemble_to_file(manifest, output_path)
        assert output_path.read_bytes() == data

    def test_missing_cids(self):
        data = b"partial"
        manifest = self.store.ingest_bytes(data)
        fake_cid = CID.from_bytes(b"nonexistent")
        cids = manifest.chunk_cids + [fake_cid]
        missing = self.store.get_missing_cids(cids)
        assert fake_cid in missing
        for real_cid in manifest.chunk_cids:
            assert real_cid not in missing


# ──────────────────────────────────────────────────
# Peer Tests
# ──────────────────────────────────────────────────


class TestPeerID:
    def test_generate(self):
        pid = PeerID.generate()
        assert len(pid.id_hex) == 64

    def test_distance(self):
        p1 = PeerID.generate()
        p2 = PeerID.generate()
        d = p1.distance(p2)
        assert d > 0
        assert p1.distance(p1) == 0


class TestPeerStore:
    def test_add_peer(self):
        store = PeerStore()
        peer = PeerInfo(peer_id=PeerID.generate())
        assert store.add_peer(peer)
        assert store.peer_count == 1

    def test_reject_self(self):
        store = PeerStore()
        self_peer = PeerInfo(peer_id=store.local_id)
        assert not store.add_peer(self_peer)

    def test_find_providers(self):
        store = PeerStore()
        peer = PeerInfo(peer_id=PeerID.generate(), state=PeerState.CONNECTED)
        cid = CID.from_bytes(b"content")
        peer.available_cids.add(cid)
        store.add_peer(peer)
        providers = store.find_providers(cid)
        assert len(providers) == 1
        assert providers[0].peer_id == peer.peer_id

    def test_eviction(self):
        store = PeerStore(max_peers=3)
        for _ in range(5):
            store.add_peer(PeerInfo(peer_id=PeerID.generate()))
        assert store.peer_count <= 3

    def test_reputation(self):
        peer = PeerInfo(peer_id=PeerID.generate())
        peer.record_success(1000)
        peer.record_success(1000)
        peer.record_failure()
        assert 0.6 < peer.reputation_score < 0.7


# ──────────────────────────────────────────────────
# Mesh Network Tests
# ──────────────────────────────────────────────────


class TestRoutingTable:
    def test_add_and_find(self):
        local = PeerID.generate()
        rt = RoutingTable(local)
        peers = [PeerID.generate() for _ in range(10)]
        for p in peers:
            rt.add_peer(p)
        assert rt.total_peers == 10
        closest = rt.find_closest(peers[0], count=3)
        assert len(closest) == 3

    def test_reject_self(self):
        local = PeerID.generate()
        rt = RoutingTable(local)
        assert not rt.add_peer(local)


class TestMeshNetwork:
    def test_announce_content(self):
        store = PeerStore()
        mesh = MeshNetwork(store)
        cid = CID.from_bytes(b"announced content")
        mesh.announce_content(cid)
        # Local node should be a provider
        providers = mesh._content_providers.get(cid, set())
        assert mesh.local_id in providers

    def test_handle_ping(self):
        store = PeerStore()
        mesh = MeshNetwork(store)
        sender = PeerID.generate()
        msg = MeshMessage(msg_type=MessageType.PING, sender=sender)
        mesh.handle_message(msg)
        outbox = mesh.drain_outbox()
        assert any(m.msg_type == MessageType.PONG for _, m in outbox)

    def test_deduplication(self):
        store = PeerStore()
        mesh = MeshNetwork(store)
        msg = MeshMessage(msg_type=MessageType.PING, sender=PeerID.generate())
        mesh.handle_message(msg)
        mesh.handle_message(msg)  # Duplicate
        outbox = mesh.drain_outbox()
        # Only one PONG should be generated
        pongs = [m for _, m in outbox if m.msg_type == MessageType.PONG]
        assert len(pongs) == 1


# ──────────────────────────────────────────────────
# Torrent Transfer Tests
# ──────────────────────────────────────────────────


class TestBitfield:
    def test_set_and_has(self):
        bf = Bitfield(size=16)
        assert not bf.has(5)
        bf.set(5)
        assert bf.has(5)

    def test_completed_count(self):
        bf = Bitfield(size=10)
        bf.set(0)
        bf.set(3)
        bf.set(7)
        assert bf.completed_count == 3

    def test_progress(self):
        bf = Bitfield(size=4)
        bf.set(0)
        bf.set(1)
        assert bf.progress == 0.5

    def test_full(self):
        bf = Bitfield.full(8)
        assert bf.is_complete
        assert bf.progress == 1.0

    def test_serialization(self):
        bf = Bitfield(size=16)
        bf.set(0)
        bf.set(8)
        raw = bf.to_bytes()
        restored = Bitfield.from_bytes(raw, size=16)
        assert restored.has(0)
        assert restored.has(8)
        assert not restored.has(1)


class TestTorrentTransfer:
    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        self.store = ContentStore(
            store_path=Path(self._tmpdir) / "blocks",
            chunk_size=64,
        )

    def test_create_seeding_transfer(self):
        data = b"seed this content " * 20
        manifest = self.store.ingest_bytes(data, name="seed.bin")
        transfer = TorrentTransfer(manifest=manifest, content_store=self.store)
        assert transfer.is_complete
        assert transfer.is_seeding
        assert transfer.progress == 1.0

    def test_download_transfer(self):
        # Create manifest without storing chunks locally
        data = b"download this " * 20
        chunker = self.store.chunker
        chunks = chunker.chunk_bytes(data)
        chunk_cids = [c.cid for c in chunks]

        root_cid = CID.from_bytes(
            b"".join(c.hash_hex.encode() for c in chunk_cids)
        )
        from src.network.content_store import ContentManifest, DAGNode
        manifest = ContentManifest(
            root_cid=root_cid,
            name="download.bin",
            total_size=len(data),
            chunk_size=64,
            chunk_cids=chunk_cids,
            dag=DAGNode(cid=root_cid, children=chunk_cids, size=len(data)),
        )

        transfer = TorrentTransfer(manifest=manifest, content_store=self.store)
        assert not transfer.is_complete
        assert transfer.progress == 0.0

        # Simulate receiving pieces
        for chunk in chunks:
            result = transfer.receive_piece(chunk.index, chunk)
            assert result is True

        assert transfer.is_complete
        assert transfer.progress == 1.0

    def test_piece_verification_failure(self):
        data = b"verify test " * 10
        manifest = self.store.ingest_bytes(data, name="verify.bin")

        # Clear store to simulate fresh download
        store2 = ContentStore(
            store_path=Path(self._tmpdir) / "blocks2",
            chunk_size=64,
        )
        transfer = TorrentTransfer(manifest=manifest, content_store=store2)

        # Try to receive a chunk with wrong data
        bad_chunk = Chunk.create(b"CORRUPTED DATA", index=0)
        result = transfer.receive_piece(0, bad_chunk)
        assert result is False


class TestTorrentManager:
    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        self.store = ContentStore(
            store_path=Path(self._tmpdir) / "blocks",
            chunk_size=64,
        )

    def test_manage_transfers(self):
        data = b"managed content " * 10
        manifest = self.store.ingest_bytes(data, name="managed.bin")
        manager = TorrentManager(self.store)
        transfer = manager.create_transfer(manifest)
        assert transfer.is_seeding
        assert len(manager.get_seeding_transfers()) == 1
        assert len(manager.get_active_transfers()) == 0


# ──────────────────────────────────────────────────
# Stream Tests
# ──────────────────────────────────────────────────


class TestStreamBuffer:
    def test_buffer_operations(self):
        buf = StreamBuffer(total_pieces=10, buffer_ahead=3)
        assert not buf.can_read()

        buf.mark_buffered(0)
        assert buf.can_read()

        idx = buf.advance()
        assert idx == 0
        assert buf.read_cursor == 1

    def test_needed_pieces(self):
        buf = StreamBuffer(total_pieces=10, buffer_ahead=3)
        needed = buf.needed_pieces()
        assert needed == [0, 1, 2]

        buf.mark_buffered(0)
        buf.mark_buffered(1)
        needed = buf.needed_pieces()
        assert needed == [2]

    def test_complete(self):
        buf = StreamBuffer(total_pieces=3, buffer_ahead=3)
        for i in range(3):
            buf.mark_buffered(i)
            buf.advance()
        assert buf.is_complete


class TestFileStream:
    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        self.store = ContentStore(
            store_path=Path(self._tmpdir) / "blocks",
            chunk_size=64,
        )

    def test_stream_complete_content(self):
        data = b"streamable content " * 20
        manifest = self.store.ingest_bytes(data, name="stream.bin")
        transfer = TorrentTransfer(manifest=manifest, content_store=self.store)

        stream = FileStream(
            manifest=manifest,
            transfer=transfer,
            content_store=self.store,
            buffer_ahead=4,
        )
        stream.start()

        result = stream.read_all_available()
        assert result == data

    def test_stream_seek(self):
        data = b"x" * 256  # 4 chunks of 64 bytes
        manifest = self.store.ingest_bytes(data, name="seek.bin")
        transfer = TorrentTransfer(manifest=manifest, content_store=self.store)

        stream = FileStream(
            manifest=manifest,
            transfer=transfer,
            content_store=self.store,
        )
        stream.start()
        stream.seek(2)
        assert stream.buffer.read_cursor == 2


# ──────────────────────────────────────────────────
# Coordinator Tests
# ──────────────────────────────────────────────────


class TestNetworkCoordinator:
    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        config = NetworkConfig(
            store_path=Path(self._tmpdir) / "blocks",
            chunk_size=64,
        )
        self.coordinator = NetworkCoordinator(config)

    def test_publish_and_retrieve(self):
        data = b"published resource " * 30
        manifest = self.coordinator.publish_bytes(data, name="resource.bin")
        assert manifest.name == "resource.bin"
        assert self.coordinator.has_content(manifest.root_cid)

        retrieved = self.coordinator.get_content(manifest.root_cid)
        assert retrieved == data

    def test_publish_file(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".dat") as f:
            content = b"file publish test " * 50
            f.write(content)
            path = Path(f.name)

        manifest = self.coordinator.publish_file(path, name="test.dat")
        assert self.coordinator.has_content(manifest.root_cid)

        output = Path(self._tmpdir) / "retrieved.dat"
        self.coordinator.save_content(manifest.root_cid, output)
        assert output.read_bytes() == content
        path.unlink()

    def test_stream_published_content(self):
        data = b"stream via coordinator " * 20
        manifest = self.coordinator.publish_bytes(data, name="stream.bin")
        stream = self.coordinator.stream(manifest)
        result = stream.read_all_available()
        assert result == data

    def test_add_peer(self):
        peer = self.coordinator.add_peer("192.168.1.10", 4001)
        assert peer.state == PeerState.CONNECTED
        stats = self.coordinator.get_network_stats()
        assert stats["total_peers"] >= 1

    def test_network_stats(self):
        stats = self.coordinator.get_network_stats()
        assert "local_id" in stats
        assert "stored_blocks" in stats
        assert "active_transfers" in stats
