"""
Content-Addressed Storage (IPFS-like).

Provides CID (Content Identifier) based storage where data is split into chunks,
each identified by its cryptographic hash. This enables deduplication, integrity
verification, and content-based routing across the mesh network.

Architecture:
    File → Chunker → [Chunk] → CID(hash) → DAG(Merkle Tree) → Root CID

    - Each chunk is hashed to produce a CID
    - Chunks form a Merkle DAG for hierarchical verification
    - Root CID uniquely identifies the entire file
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterator


class HashAlgorithm(Enum):
    SHA256 = "sha256"
    BLAKE2B = "blake2b"


# Default chunk size: 256KB (matches IPFS default)
DEFAULT_CHUNK_SIZE = 256 * 1024


@dataclass(frozen=True)
class CID:
    """Content Identifier - uniquely identifies a piece of content by its hash."""

    hash_hex: str
    algorithm: HashAlgorithm = HashAlgorithm.SHA256
    version: int = 1

    @classmethod
    def from_bytes(cls, data: bytes, algorithm: HashAlgorithm = HashAlgorithm.SHA256) -> CID:
        if algorithm == HashAlgorithm.SHA256:
            h = hashlib.sha256(data).hexdigest()
        elif algorithm == HashAlgorithm.BLAKE2B:
            h = hashlib.blake2b(data, digest_size=32).hexdigest()
        else:
            raise ValueError(f"Unsupported algorithm: {algorithm}")
        return cls(hash_hex=h, algorithm=algorithm)

    def __str__(self) -> str:
        prefix = "Qm" if self.algorithm == HashAlgorithm.SHA256 else "bk"
        return f"{prefix}{self.hash_hex[:16]}"

    def __hash__(self) -> int:
        return hash(self.hash_hex)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CID):
            return NotImplemented
        return self.hash_hex == other.hash_hex


@dataclass
class Chunk:
    """A single chunk of content with its CID and positional metadata."""

    cid: CID
    data: bytes
    index: int
    size: int

    @classmethod
    def create(cls, data: bytes, index: int, algorithm: HashAlgorithm = HashAlgorithm.SHA256) -> Chunk:
        cid = CID.from_bytes(data, algorithm)
        return cls(cid=cid, data=data, index=index, size=len(data))

    def verify(self) -> bool:
        """Verify chunk integrity by recomputing the CID."""
        expected = CID.from_bytes(self.data, self.cid.algorithm)
        return self.cid == expected


@dataclass
class DAGNode:
    """Merkle DAG node - represents a file as a tree of chunk CIDs."""

    cid: CID
    children: list[CID] = field(default_factory=list)
    size: int = 0
    name: str = ""
    is_leaf: bool = False


@dataclass
class ContentManifest:
    """Complete description of a content item for transfer coordination."""

    root_cid: CID
    name: str
    total_size: int
    chunk_size: int
    chunk_cids: list[CID]
    dag: DAGNode
    metadata: dict = field(default_factory=dict)

    @property
    def chunk_count(self) -> int:
        return len(self.chunk_cids)

    def to_dict(self) -> dict:
        return {
            "root_cid": str(self.root_cid),
            "name": self.name,
            "total_size": self.total_size,
            "chunk_size": self.chunk_size,
            "chunk_count": self.chunk_count,
            "chunk_cids": [str(c) for c in self.chunk_cids],
            "metadata": self.metadata,
        }


class Chunker:
    """Splits data into fixed-size chunks with content addressing."""

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    ):
        self.chunk_size = chunk_size
        self.algorithm = algorithm

    def chunk_bytes(self, data: bytes) -> list[Chunk]:
        """Split raw bytes into content-addressed chunks."""
        chunks = []
        for i in range(0, len(data), self.chunk_size):
            piece = data[i : i + self.chunk_size]
            chunk = Chunk.create(piece, index=len(chunks), algorithm=self.algorithm)
            chunks.append(chunk)
        return chunks

    def chunk_file(self, path: Path) -> Iterator[Chunk]:
        """Stream chunks from a file without loading it entirely into memory."""
        index = 0
        with open(path, "rb") as f:
            while True:
                piece = f.read(self.chunk_size)
                if not piece:
                    break
                yield Chunk.create(piece, index=index, algorithm=self.algorithm)
                index += 1


class ContentStore:
    """
    Local content-addressed store.

    Stores and retrieves chunks by CID. Provides the foundation for
    IPFS-like content distribution where any peer can serve any chunk
    they hold, identified solely by its content hash.
    """

    def __init__(
        self,
        store_path: Path | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    ):
        self.store_path = store_path or Path.home() / ".clio" / "blocks"
        self.store_path.mkdir(parents=True, exist_ok=True)
        self.chunker = Chunker(chunk_size=chunk_size, algorithm=algorithm)
        self.algorithm = algorithm
        self.chunk_size = chunk_size
        # In-memory index: CID -> file path on disk
        self._index: dict[CID, Path] = {}
        # Manifest registry: root CID -> manifest
        self._manifests: dict[CID, ContentManifest] = {}
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        """Rebuild the CID index from the block store directory."""
        if not self.store_path.exists():
            return
        for block_file in self.store_path.iterdir():
            if block_file.is_file() and block_file.suffix == ".block":
                cid = CID(hash_hex=block_file.stem, algorithm=self.algorithm)
                self._index[cid] = block_file

    def _block_path(self, cid: CID) -> Path:
        """Get the storage path for a given CID."""
        # Use first 4 chars as shard directory for filesystem efficiency
        shard = cid.hash_hex[:4]
        shard_dir = self.store_path / shard
        shard_dir.mkdir(parents=True, exist_ok=True)
        return shard_dir / f"{cid.hash_hex}.block"

    def has(self, cid: CID) -> bool:
        """Check if a chunk exists in the local store."""
        if cid in self._index:
            return True
        return self._block_path(cid).exists()

    def put_chunk(self, chunk: Chunk) -> CID:
        """Store a chunk and return its CID."""
        if not chunk.verify():
            raise ValueError(f"Chunk integrity check failed for index {chunk.index}")
        path = self._block_path(chunk.cid)
        if not path.exists():
            path.write_bytes(chunk.data)
        self._index[chunk.cid] = path
        return chunk.cid

    def get_chunk(self, cid: CID) -> Chunk | None:
        """Retrieve a chunk by its CID."""
        path = self._block_path(cid)
        if not path.exists():
            return None
        data = path.read_bytes()
        # We don't know the original index, use -1 as sentinel
        chunk = Chunk(cid=cid, data=data, index=-1, size=len(data))
        if not chunk.verify():
            path.unlink()
            self._index.pop(cid, None)
            return None
        return chunk

    def ingest_file(self, path: Path, name: str = "") -> ContentManifest:
        """
        Ingest a file into the content store.

        Splits the file into chunks, stores each chunk, builds a Merkle DAG,
        and returns a manifest describing the complete content.
        """
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        file_size = path.stat().st_size
        file_name = name or path.name
        chunk_cids: list[CID] = []

        for chunk in self.chunker.chunk_file(path):
            self.put_chunk(chunk)
            chunk_cids.append(chunk.cid)

        # Build Merkle DAG root
        root_cid = self._build_dag_root(chunk_cids)
        dag = DAGNode(
            cid=root_cid,
            children=chunk_cids,
            size=file_size,
            name=file_name,
        )

        manifest = ContentManifest(
            root_cid=root_cid,
            name=file_name,
            total_size=file_size,
            chunk_size=self.chunk_size,
            chunk_cids=chunk_cids,
            dag=dag,
        )
        self._manifests[root_cid] = manifest
        return manifest

    def ingest_bytes(self, data: bytes, name: str = "") -> ContentManifest:
        """Ingest raw bytes into the content store."""
        chunks = self.chunker.chunk_bytes(data)
        chunk_cids: list[CID] = []

        for chunk in chunks:
            self.put_chunk(chunk)
            chunk_cids.append(chunk.cid)

        root_cid = self._build_dag_root(chunk_cids)
        dag = DAGNode(
            cid=root_cid,
            children=chunk_cids,
            size=len(data),
            name=name,
        )

        manifest = ContentManifest(
            root_cid=root_cid,
            name=name,
            total_size=len(data),
            chunk_size=self.chunk_size,
            chunk_cids=chunk_cids,
            dag=dag,
        )
        self._manifests[root_cid] = manifest
        return manifest

    def reassemble(self, manifest: ContentManifest) -> bytes:
        """Reassemble a complete file from its manifest."""
        parts = []
        for cid in manifest.chunk_cids:
            chunk = self.get_chunk(cid)
            if chunk is None:
                raise ValueError(f"Missing chunk: {cid}")
            parts.append(chunk.data)
        return b"".join(parts)

    def reassemble_to_file(self, manifest: ContentManifest, output_path: Path) -> Path:
        """Reassemble content to a file on disk."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            for cid in manifest.chunk_cids:
                chunk = self.get_chunk(cid)
                if chunk is None:
                    raise ValueError(f"Missing chunk: {cid}")
                f.write(chunk.data)
        return output_path

    def get_manifest(self, root_cid: CID) -> ContentManifest | None:
        """Retrieve a stored manifest by its root CID."""
        return self._manifests.get(root_cid)

    def register_manifest(self, manifest: ContentManifest) -> None:
        """Register an externally received manifest."""
        self._manifests[manifest.root_cid] = manifest

    def get_available_cids(self, chunk_cids: list[CID]) -> list[CID]:
        """Return which of the requested CIDs are available locally."""
        return [cid for cid in chunk_cids if self.has(cid)]

    def get_missing_cids(self, chunk_cids: list[CID]) -> list[CID]:
        """Return which of the requested CIDs are NOT available locally."""
        return [cid for cid in chunk_cids if not self.has(cid)]

    def _build_dag_root(self, chunk_cids: list[CID]) -> CID:
        """Build a Merkle DAG root CID from child CIDs."""
        combined = b"".join(c.hash_hex.encode() for c in chunk_cids)
        return CID.from_bytes(combined, self.algorithm)

    @property
    def stored_block_count(self) -> int:
        count = 0
        for shard_dir in self.store_path.iterdir():
            if shard_dir.is_dir():
                count += sum(1 for f in shard_dir.iterdir() if f.suffix == ".block")
        return count
