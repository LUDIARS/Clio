"""
File Streaming over P2P Network.

Provides sequential streaming of content from the mesh network, enabling
playback or processing of large files before the entire download completes.

Streaming is built on top of the torrent transfer layer, using sequential
piece selection to prioritize pieces needed for continuous playback.

Architecture:
    StreamRequest → TorrentTransfer(SEQUENTIAL) → Buffer → StreamReader

    - Pieces are fetched sequentially from the current read position
    - A read-ahead buffer maintains pieces ahead of the read cursor
    - Pieces behind the cursor are available for seeding to other peers
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator

from src.network.content_store import CID, ContentManifest, ContentStore
from src.network.torrent import (
    Bitfield,
    SelectionStrategy,
    TorrentTransfer,
)


class StreamState(Enum):
    IDLE = "idle"
    BUFFERING = "buffering"
    STREAMING = "streaming"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class StreamBuffer:
    """
    Manages a sliding window buffer for streaming.

    Tracks which pieces are buffered and ready for sequential reading.
    """

    total_pieces: int
    buffer_ahead: int = 8  # Number of pieces to buffer ahead of read position

    read_cursor: int = 0
    _buffered: set[int] = field(default_factory=set)

    def mark_buffered(self, piece_index: int) -> None:
        self._buffered.add(piece_index)

    def is_buffered(self, piece_index: int) -> bool:
        return piece_index in self._buffered

    def can_read(self) -> bool:
        """Check if the current read position is buffered."""
        if self.read_cursor >= self.total_pieces:
            return False
        return self.read_cursor in self._buffered

    def advance(self) -> int | None:
        """Advance the read cursor if possible. Returns the piece index read."""
        if not self.can_read():
            return None
        idx = self.read_cursor
        self.read_cursor += 1
        return idx

    def needed_pieces(self) -> list[int]:
        """Get the list of pieces needed for the buffer window."""
        needed = []
        start = self.read_cursor
        end = min(start + self.buffer_ahead, self.total_pieces)
        for i in range(start, end):
            if i not in self._buffered:
                needed.append(i)
        return needed

    @property
    def buffer_health(self) -> float:
        """Ratio of buffered pieces in the read-ahead window."""
        start = self.read_cursor
        end = min(start + self.buffer_ahead, self.total_pieces)
        window_size = end - start
        if window_size == 0:
            return 1.0
        buffered_count = sum(1 for i in range(start, end) if i in self._buffered)
        return buffered_count / window_size

    @property
    def is_complete(self) -> bool:
        return self.read_cursor >= self.total_pieces

    @property
    def progress(self) -> float:
        return self.read_cursor / self.total_pieces if self.total_pieces > 0 else 0.0


class FileStream:
    """
    Streams a file from the P2P network.

    Provides a file-like interface for reading content that is being
    downloaded via the torrent transfer protocol. Automatically manages
    buffering and piece prioritization for smooth sequential access.
    """

    def __init__(
        self,
        manifest: ContentManifest,
        transfer: TorrentTransfer,
        content_store: ContentStore,
        buffer_ahead: int = 8,
    ):
        self.manifest = manifest
        self.transfer = transfer
        self.content_store = content_store
        self.state = StreamState.IDLE
        self.buffer = StreamBuffer(
            total_pieces=manifest.chunk_count,
            buffer_ahead=buffer_ahead,
        )

        # Register callback to update buffer on piece completion
        self.transfer.on_piece_complete(self._on_piece_received)

        # Sync existing pieces into buffer
        for i in range(manifest.chunk_count):
            if transfer.local_bitfield.has(i):
                self.buffer.mark_buffered(i)

    def _on_piece_received(self, piece_index: int, cid: CID) -> None:
        """Called when a piece finishes downloading."""
        self.buffer.mark_buffered(piece_index)
        if self.state == StreamState.BUFFERING and self.buffer.can_read():
            self.state = StreamState.STREAMING

    def start(self) -> None:
        """Start the stream."""
        if self.buffer.can_read():
            self.state = StreamState.STREAMING
        else:
            self.state = StreamState.BUFFERING

    def pause(self) -> None:
        self.state = StreamState.PAUSED

    def resume(self) -> None:
        if self.buffer.can_read():
            self.state = StreamState.STREAMING
        else:
            self.state = StreamState.BUFFERING

    def seek(self, piece_index: int) -> None:
        """Seek to a specific piece position."""
        if 0 <= piece_index < self.manifest.chunk_count:
            self.buffer.read_cursor = piece_index
            if self.buffer.can_read():
                self.state = StreamState.STREAMING
            else:
                self.state = StreamState.BUFFERING

    def read_piece(self) -> bytes | None:
        """
        Read the next piece from the stream.

        Returns None if data is not yet available (buffering).
        """
        if self.state in (StreamState.PAUSED, StreamState.ERROR):
            return None

        if self.buffer.is_complete:
            self.state = StreamState.COMPLETED
            return None

        idx = self.buffer.advance()
        if idx is None:
            self.state = StreamState.BUFFERING
            return None

        cid = self.manifest.chunk_cids[idx]
        chunk = self.content_store.get_chunk(cid)
        if chunk is None:
            self.state = StreamState.ERROR
            return None

        if self.buffer.is_complete:
            self.state = StreamState.COMPLETED

        return chunk.data

    def read_all_available(self) -> bytes:
        """Read all consecutively available pieces from the current position."""
        parts = []
        while True:
            data = self.read_piece()
            if data is None:
                break
            parts.append(data)
        return b"".join(parts)

    def iter_pieces(self, poll_interval: float = 0.1, timeout: float = 300.0) -> Iterator[bytes]:
        """
        Iterate over pieces as they become available.

        Yields piece data in order, waiting for buffering when necessary.
        """
        start_time = time.time()
        self.start()

        while not self.buffer.is_complete:
            if time.time() - start_time > timeout:
                self.state = StreamState.ERROR
                break

            data = self.read_piece()
            if data is not None:
                yield data
            else:
                time.sleep(poll_interval)

    def get_priority_pieces(self) -> list[int]:
        """Get pieces that should be prioritized for streaming."""
        return self.buffer.needed_pieces()

    def get_stats(self) -> dict:
        return {
            "state": self.state.value,
            "read_position": self.buffer.read_cursor,
            "total_pieces": self.manifest.chunk_count,
            "buffer_health": f"{self.buffer.buffer_health:.1%}",
            "stream_progress": f"{self.buffer.progress:.1%}",
            "download_progress": f"{self.transfer.progress:.1%}",
        }


class StreamManager:
    """
    Manages multiple concurrent file streams.

    Coordinates piece prioritization across active streams to ensure
    smooth playback for all streaming sessions.
    """

    def __init__(self, content_store: ContentStore):
        self.content_store = content_store
        self._streams: dict[CID, FileStream] = {}

    def create_stream(
        self,
        manifest: ContentManifest,
        transfer: TorrentTransfer,
        buffer_ahead: int = 8,
    ) -> FileStream:
        """Create a new file stream."""
        stream = FileStream(
            manifest=manifest,
            transfer=transfer,
            content_store=self.content_store,
            buffer_ahead=buffer_ahead,
        )
        self._streams[manifest.root_cid] = stream
        return stream

    def get_stream(self, root_cid: CID) -> FileStream | None:
        return self._streams.get(root_cid)

    def remove_stream(self, root_cid: CID) -> None:
        stream = self._streams.pop(root_cid, None)
        if stream:
            stream.state = StreamState.IDLE

    def get_all_priority_pieces(self) -> dict[CID, list[int]]:
        """Get priority pieces across all active streams."""
        priorities = {}
        for root_cid, stream in self._streams.items():
            if stream.state in (StreamState.STREAMING, StreamState.BUFFERING):
                pieces = stream.get_priority_pieces()
                if pieces:
                    priorities[root_cid] = pieces
        return priorities

    def get_all_stats(self) -> list[dict]:
        return [s.get_stats() for s in self._streams.values()]
