"""Single-worker job queue with cancellation and a per-job event bus."""

import json
import logging
import queue
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

JobRunner = Callable[[str], None]


@dataclass
class JobHandle:
    """Cancellation state for one queued or running job."""

    job_id: str
    cancel: threading.Event = field(default_factory=threading.Event)


class EventBus:
    """Thread-safe publish/subscribe bus for SSE consumers.

    Every published frame is prefixed with a monotonic ``id:`` line so browsers
    can resume with ``Last-Event-ID``. Recent frames are kept in a small
    per-job replay buffer; subscribing with ``after=<id>`` replays only the
    frames the client has not seen.
    """

    def __init__(self, max_queue: int = 256, replay: int = 64) -> None:
        self._subs: dict[str, list[queue.Queue[str]]] = {}
        self._lock = threading.Lock()
        self._max = max_queue
        self._replay = replay
        self._next_id = 0
        self._history: dict[str, deque[tuple[int, str]]] = {}

    def subscribe(self, job_id: str, after: int = 0) -> queue.Queue[str]:
        """Register a subscriber; ``after`` replays later frames when known."""
        q: queue.Queue[str] = queue.Queue(maxsize=self._max)
        with self._lock:
            if after > 0:
                for frame_id, frame in self._history.get(job_id, ()):
                    if frame_id > after:
                        try:
                            q.put_nowait(frame)
                        except queue.Full:
                            break
            self._subs.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q: queue.Queue[str]) -> None:
        """Remove a subscriber queue."""
        with self._lock:
            subs = self._subs.get(job_id, [])
            if q in subs:
                subs.remove(q)
            if not subs:
                self._subs.pop(job_id, None)

    def publish(self, job_id: str, event: str, data: dict) -> None:
        """Push an SSE frame ``id: <n>\\nevent: <name>\\ndata: <json>\\n\\n``."""
        with self._lock:
            self._next_id += 1
            frame_id = self._next_id
            frame = f"id: {frame_id}\nevent: {event}\ndata: {json.dumps(data)}\n\n"
            history = self._history.setdefault(job_id, deque(maxlen=self._replay))
            history.append((frame_id, frame))
            subs = list(self._subs.get(job_id, []))
            for q in subs:
                try:
                    q.put_nowait(frame)
                except queue.Full:
                    try:
                        q.get_nowait()
                        q.put_nowait(frame)
                    except queue.Empty:
                        pass


class JobQueue:
    """FIFO queue executed by one daemon worker thread."""

    def __init__(self, runner: JobRunner) -> None:
        self._runner = runner
        self._pending: deque[str] = deque()
        self._handles: dict[str, JobHandle] = {}
        self._lock = threading.Lock()
        self.bus = EventBus()
        self._thread = threading.Thread(target=self._work, name="openclip-worker", daemon=True)
        self._thread.start()

    def submit(self, job_id: str) -> JobHandle:
        """Enqueue a job; returns its handle for cancellation."""
        handle = JobHandle(job_id=job_id)
        with self._lock:
            self._handles[job_id] = handle
            self._pending.append(job_id)
        return handle

    def cancel(self, job_id: str) -> bool:
        """Request cancellation; True when the job was known."""
        with self._lock:
            handle = self._handles.get(job_id)
        if handle is None:
            return False
        handle.cancel.set()
        return True

    def handle(self, job_id: str) -> JobHandle | None:
        """Return the live handle for a job, if any."""
        with self._lock:
            return self._handles.get(job_id)

    def is_active(self, job_id: str) -> bool:
        """True while the job is queued or running."""
        with self._lock:
            return job_id in self._handles

    def _work(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    job_id = None
                else:
                    job_id = self._pending.popleft()
            if job_id is None:
                threading.Event().wait(0.2)
                continue
            handle = self.handle(job_id)
            if handle is None:
                continue
            try:
                self._runner(job_id)
            except Exception:
                log.exception("job %s runner crashed", job_id)
            finally:
                with self._lock:
                    self._handles.pop(job_id, None)
