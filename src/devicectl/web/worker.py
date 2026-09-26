"""One thread owning the one connection, and everything queued behind it.

A device of this kind takes one conversation at a time -- a charger allows
a single session, a serial bus is a single wire -- so the web server cannot
let request threads talk to it directly.  Instead every request hands a
callable to the worker, which owns the link on its own thread and runs them
one after another.  A handler is then ordinary blocking code: it submits,
waits, and gets back what the callable returned or whatever it raised.

Three things fall out of that and are all here:

* **Jobs.**  Work measured in minutes rather than milliseconds cannot hold a
  request thread open, so it is queued as a :class:`Job` and followed on the
  event stream instead.
* **A published state.**  A page has to be able to say what the link is
  doing and who else is waiting, so every transition is broadcast.
* **Idleness.**  A device nobody is asking about should not be held.  The
  worker closes the link after a quiet spell, and a live refresh -- when the
  page has asked for one -- is just a task it queues for itself.

What a link *is*, how it opens and what a task is handed are the program's
own: :class:`Worker` is generic over the link and calls back for those.  Retry
deliberately is not here.  One program retries at the authentication layer
(a 401 means log in again) and another at the protocol layer (a framing
fault is not a Modbus exception), and an abstraction over the two would
describe neither.
"""

from __future__ import annotations

import itertools
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

from devicectl.errors import DeviceError, describe
from devicectl.web.events import Broadcaster

LinkT = TypeVar("LinkT")

# How often the live refresh reads, and the range a page may ask for.
DEFAULT_POLL_INTERVAL_S = 3.0
MIN_POLL_INTERVAL_S = 1.0
MAX_POLL_INTERVAL_S = 60.0

# How long the link is held after the last thing anybody asked for.  Long
# enough that clicking around a page does not reconnect between clicks,
# short enough that a forgotten tab gives the device back.
DEFAULT_IDLE_TIMEOUT_S = 45.0

# How long a request thread waits for its turn before giving up on it.
DEFAULT_TASK_TIMEOUT_S = 180.0

# How often the loop looks up when there is nothing queued.
LOOP_TICK_S = 0.25

# How many finished operations the activity ticker remembers.
ACTIVITY_HISTORY = 12

# How long a finished job stays answerable, for a page that was not looking.
JOB_RETENTION_S = 900.0

# The fastest a job's progress is published: more often than this is more
# often than a browser can usefully redraw.
JOB_NOTIFY_INTERVAL_S = 0.25

# How long `stop` waits for the thread to notice before leaving it to the
# interpreter.  It is a daemon thread with nothing to write down.
STOP_JOIN_S = 0.1

_job_ids = itertools.count(1)


@dataclass
class Job:
    """A long-running operation the UI follows rather than waits for."""

    id: int
    name: str
    state: str = "queued"  # queued | running | done | failed
    progress: float | None = None
    message: str = ""
    error: str = ""
    started: float = 0.0
    finished: float = 0.0
    result: dict[str, Any] = field(default_factory=dict)
    """What the job produced, for the page to read once it is done.

    An object rather than a bare value, because the page reads fields off
    it and a job usually has more than one thing to say.  A callable either
    returns it or fills it in as it goes.
    """
    notify: Callable[["Job"], None] | None = field(default=None, repr=False)
    _last_notify: float = field(default=0.0, repr=False)

    def report(
        self,
        progress: float | None = None,
        message: str | None = None,
        *,
        force: bool = False,
    ) -> None:
        """Update the job's progress and tell the browsers, at most 4x a second.

        ``force`` is for a new phase, which is worth an immediate event.  A
        changed message is not enough on its own: a transfer that counts what
        it has sent changes its message every block, and forcing on that
        would publish an event per block and defeat the rate limit entirely.
        A throttled message is not lost -- it goes out with the next event.
        """
        if progress is not None:
            self.progress = max(0.0, min(1.0, float(progress)))
        if message is not None:
            self.message = message
        now = time.monotonic()
        if not force and now - self._last_notify < JOB_NOTIFY_INTERVAL_S:
            return
        self._last_notify = now
        if self.notify is not None:
            self.notify(self)

    def as_dict(self) -> dict[str, Any]:
        """Render the job for the UI."""
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "elapsed": (self.finished or time.time()) - self.started
            if self.started
            else 0.0,
        } | ({"result": self.result} if self.result else {})


class Task:
    """One unit of work, queued for the worker thread."""

    def __init__(
        self,
        name: str,
        fn: Callable[..., Any],
        *,
        job: Job | None = None,
        quiet: bool = False,
        exclusive: bool = False,
    ) -> None:
        """Prepare a task; ``quiet`` keeps polls out of the activity ticker."""
        self.name = name
        self.fn = fn
        self.job = job
        self.quiet = quiet
        self.exclusive = exclusive
        self.done = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None


class Worker(Generic[LinkT]):
    """Owns the one link to the device and runs every request against it.

    Subclass it and answer four questions: how a link is opened
    (:meth:`_open_link`), how it is closed (:meth:`_close_link`), whether
    there is anything to open one to (:meth:`_check_ready`), and what a task
    is handed when it runs (:meth:`_invoke`, which by default is the link
    itself).  :meth:`_details` adds whatever else the page needs to draw the
    header.
    """

    # The published names for the states the base moves through.  They are
    # class attributes because a program's page and stylesheet are written
    # against its own words for them.
    RELEASED = "released"
    OPENING = "opening"
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"

    thread_name = "device-worker"
    task_timeout_s = DEFAULT_TASK_TIMEOUT_S

    # What the page calls the live refresh in the link pill.  A program's own
    # word for it: the frontend checks these strings against the panels that
    # wait on them, so it is not free to change.
    poll_task_name = "Refreshing"

    def __init__(
        self,
        events: Broadcaster,
        *,
        poll_interval: float = DEFAULT_POLL_INTERVAL_S,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT_S,
    ) -> None:
        """Set up the worker; it opens nothing until it has work."""
        self.events = events
        self.poll_interval = poll_interval
        self.idle_timeout = idle_timeout
        self._link: LinkT | None = None
        self._queue: queue.Queue[Task | None] = queue.Queue()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._live = False
        self._exclusive = False
        self._release_requested = False
        self._last_used = time.monotonic()
        self._last_poll = 0.0
        self._last_tick = 0
        self._state = self.RELEASED
        self._op = ""
        self._op_quiet = False
        self._op_progress: float | None = None
        self._op_note = ""
        self._last_progress = 0.0
        self._error = ""
        self._since = time.time()
        self._activity: deque[dict[str, Any]] = deque(maxlen=ACTIVITY_HISTORY)
        self._jobs: dict[int, Job] = {}
        self._poll_fn: Callable[..., Any] | None = None

    # --- what a program answers ---------------------------------------------------

    def _open_link(self) -> LinkT:
        """Open the link to the device and return it."""
        raise NotImplementedError

    def _close_link(self, link: LinkT) -> None:
        """Hand the link back.  Must not raise."""
        raise NotImplementedError

    def _check_ready(self) -> None:
        """Raise the program's own error if there is nothing to talk to yet."""

    def _invoke(self, task: Task, link: LinkT) -> Any:
        """Run one task's callable.  By default it is handed the link."""
        if task.job is not None:
            return task.fn(link, task.job)
        return task.fn(link)

    def _details(self) -> dict[str, Any]:
        """Whatever else the page needs, on top of the shared link state."""
        return {}

    def _busy_state(self, task: Task) -> str:
        """Return the state to publish while this task runs.

        Almost always :attr:`BUSY`.  A program that lets a task own the link
        outright can say so instead, which is a different thing for a page to
        draw: nothing else will get a turn until it finishes.
        """
        return self.BUSY

    def _after_failure(self, exc: BaseException) -> None:
        """React to a task that raised, before the link state is republished."""

    # --- lifecycle ----------------------------------------------------------------

    def start(self) -> None:
        """Start the worker thread."""
        self._thread = threading.Thread(
            target=self._loop, name=self.thread_name, daemon=True
        )
        self._thread.start()
        self._publish_link()

    def stop(self, timeout: float | None = None) -> None:
        """Stop the worker and drop the link.

        Returning quickly matters more than unwinding tidily: this is what
        Ctrl+C waits for, and the worker can be in the middle of a round
        trip to a device on the far end of a slow link.  Closing the link
        from here does not interrupt a blocked read -- a socket closed under
        a thread parked in ``recv`` on Linux stays parked -- so the thread is
        asked to stop, given ``timeout`` to notice (:data:`STOP_JOIN_S` when
        none is named), and then left to the interpreter, which is entitled
        to do that because it is a daemon thread with nothing to write down.

        ``timeout=0`` does not wait at all, which is what a teardown running
        off a signal handler wants: blocking there is how a process hangs on
        Ctrl+C instead of stopping on it.
        """
        wait = STOP_JOIN_S if timeout is None else timeout
        self._stopping.set()
        self._queue.put(None)
        thread = self._thread
        if thread is not None and wait > 0:
            thread.join(timeout=wait)

    @property
    def stopping(self) -> bool:
        """Whether the worker has been told to stand down."""
        return self._stopping.is_set()

    @property
    def live(self) -> bool:
        """Whether the live refresh is running."""
        return self._live

    def set_poll(
        self, *, live: bool | None = None, interval: float | None = None
    ) -> None:
        """Turn the live refresh on or off, and set its interval."""
        if interval is not None:
            self.poll_interval = max(
                MIN_POLL_INTERVAL_S, min(MAX_POLL_INTERVAL_S, float(interval))
            )
        if live is not None:
            self._live = bool(live)
            if live:
                self._last_poll = 0.0  # refresh at once rather than on the beat
        self._publish_link()

    def set_poll_fn(self, fn: Callable[..., Any] | None) -> None:
        """Install what a live refresh actually reads (set by the API layer).

        It is run as an ordinary task, so it is handed whatever
        :meth:`_invoke` hands one.
        """
        self._poll_fn = fn

    def release(self) -> None:
        """Ask the worker to hand the link back as soon as it is free."""
        self._release_requested = True

    # --- submitting work ----------------------------------------------------------

    def run(
        self,
        name: str,
        fn: Callable[..., Any],
        *,
        timeout: float | None = None,
        quiet: bool = False,
    ) -> Any:
        """Run ``fn`` against the device and return its result.

        Blocks the calling (request) thread until the worker gets to it.
        Raises whatever ``fn`` raised, or :class:`~devicectl.errors.DeviceError`
        via :meth:`_busy_error` if the queue did not reach it in time.
        """
        self._check_ready()
        wait = self.task_timeout_s if timeout is None else timeout
        task = Task(name, fn, quiet=quiet)
        self._queue.put(task)
        if not task.done.wait(wait):
            raise self._busy_error(name, wait)
        if task.error is not None:
            raise task.error
        return task.result

    def run_soon(self, fn: Callable[..., Any], *, name: str = "") -> None:
        """Queue work and do not wait for it.

        Unnamed by default, so it stays out of the activity ticker: this is
        for housekeeping the user did not ask for by name.
        """
        self._queue.put(Task(name, fn, quiet=True))

    def start_job(
        self, name: str, fn: Callable[..., Any], *, exclusive: bool = False
    ) -> Job:
        """Queue a long operation and return its :class:`Job` immediately.

        ``exclusive`` is for work that owns the link outright -- a firmware
        transfer, after which the device is not speaking its usual protocol
        any more, so a live refresh in the gap would put the wrong bytes on
        the wire.  The poll timer and the idle timer both stand down for it.
        """
        self._check_ready()
        job = Job(id=next(_job_ids), name=name, notify=self._on_job_progress)
        with self._lock:
            self._jobs[job.id] = job
            self._forget_old_jobs()
        self._publish_job(job)
        if exclusive:
            with self._lock:
                self._exclusive = True
        self._queue.put(Task(name, fn, job=job, exclusive=exclusive))
        return job

    def discard_pending(self, why: str) -> int:
        """Drop everything queued but not started, and tell whoever waits on it.

        Pointing the worker at a different device -- or giving up on the one
        it was pointed at -- leaves a queue full of work meant for the old
        one.  Running it against the new one would be wrong, and leaving it
        to time out makes the first two minutes on the new device two
        minutes of failures arriving from the last.  Dropped work fails at
        once, with ``why`` as its reason.

        The task already running is not touched: it holds the link, and the
        worker has no way to interrupt a thread parked on a read.  Returns
        how many were dropped.
        """
        dropped = 0
        while True:
            try:
                task = self._queue.get_nowait()
            except queue.Empty:
                break
            if task is None:  # the stop sentinel; leave it for the loop
                self._queue.put(None)
                break
            task.error = DeviceError(f"{task.name or 'the request'}: {why}")
            if task.exclusive:
                with self._lock:
                    self._exclusive = False
            self._finish_job(task.job, failure=task.error)
            task.done.set()
            dropped += 1
        if dropped:
            self._publish_link()
        return dropped

    def _busy_error(self, name: str, timeout: float) -> BaseException:
        """Return the error a caller gets when it never got its turn."""
        return TimeoutError(
            f"the device is busy ({self._op or 'another operation'}); "
            f"'{name}' did not get its turn within {timeout:.0f}s"
        )

    def job(self, job_id: int) -> Job | None:
        """Look up one job by id."""
        with self._lock:
            return self._jobs.get(job_id)

    def jobs(self) -> list[dict[str, Any]]:
        """Every job the worker still remembers, oldest first."""
        with self._lock:
            return [job.as_dict() for job in sorted(self._jobs.values(), key=_job_key)]

    def _forget_old_jobs(self) -> None:
        """Drop finished jobs nobody is going to ask about any more."""
        cutoff = time.time() - JOB_RETENTION_S
        for job_id, job in list(self._jobs.items()):
            if job.finished and job.finished < cutoff:
                del self._jobs[job_id]

    # --- state reporting ----------------------------------------------------------

    def link_state(self) -> dict[str, Any]:
        """Build the link snapshot published to every browser."""
        with self._lock:
            countdown = None
            if self._link is not None and not self._live:
                left = self.idle_timeout - (time.monotonic() - self._last_used)
                countdown = max(0.0, round(left, 1))
            state = {
                "state": self._state,
                "op": self._op,
                # Whether the link is busy with something nobody asked for.
                # The live refresh runs every few seconds and holds it for as
                # long as it takes; a page that treated that the same as a
                # firmware upload would blink every control on it on the beat.
                "quiet": self._op_quiet,
                "progress": self._op_progress,
                # What the task running right now has got through, in its own
                # words -- "412 records, page 5".  Some long reads have no
                # honest denominator to make a percentage out of, and a made-up
                # one is worse than a count that is true.
                "note": self._op_note,
                "queued": max(0, self._queue.qsize()),
                "since": self._since,
                "error": self._error,
                "live": self._live,
                "pollInterval": self.poll_interval,
                "releaseIn": countdown,
                "idleTimeout": self.idle_timeout,
                "activity": list(self._activity),
                "clients": self.events.subscriber_count,
            }
        return state | self._details()

    def _publish_link(self) -> None:
        """Tell every browser what the link is doing."""
        self.events.publish("link", self.link_state(), sticky=True)

    def _on_job_progress(self, job: Job) -> None:
        """Mirror a running job's progress onto the link, and publish both."""
        with self._lock:
            self._op_progress = job.progress
        self._publish_job(job)
        self._publish_link()

    def _publish_job(self, job: Job) -> None:
        """Tell every browser about one job's progress."""
        self.events.publish("job", job.as_dict())

    def _set_state(
        self,
        state: str,
        op: str = "",
        *,
        error: str = "",
        progress: float | None = None,
        quiet: bool = False,
    ) -> None:
        """Move the link to a new state and publish it."""
        with self._lock:
            self._state = state
            self._op = op
            self._op_quiet = quiet
            self._op_progress = progress
            self._op_note = ""
            self._error = error
            self._since = time.time()
        self._publish_link()

    def progress(self, fraction: float | None = None, note: str = "") -> None:
        """Say how the task running right now is getting on.

        The counterpart to :meth:`Job.report` for work that is *not* a job:
        a read the browser is waiting on, which holds the link for seconds
        while it pages a device.  Without this the page has only the pill's
        "busy", which is the same thing it says for a write that lands in
        150 ms -- so a long walk is indistinguishable from a hang.

        ``fraction`` is ``None`` where there is no honest denominator, and
        ``note`` then carries what there *is* to say.  Called from the worker
        thread, from inside the task it describes, and throttled.
        """
        now = time.monotonic()
        if now - self._last_progress < JOB_NOTIFY_INTERVAL_S:
            return
        self._last_progress = now
        with self._lock:
            self._op_progress = fraction
            self._op_note = note
        self._publish_link()

    def _note_activity(self, name: str, seconds: float, ok: bool, detail: str) -> None:
        """Add one finished operation to the ticker."""
        with self._lock:
            self._activity.appendleft(
                {
                    "op": name,
                    "seconds": round(seconds, 3),
                    "ok": ok,
                    "at": time.time(),
                    "detail": detail,
                }
            )

    # --- the worker thread --------------------------------------------------------

    def _loop(self) -> None:
        """Run tasks one at a time, polling and releasing in the gaps."""
        while not self._stopping.is_set():
            try:
                task = self._queue.get(timeout=LOOP_TICK_S)
            except queue.Empty:
                task = None
            if task is None:
                if self._stopping.is_set():
                    break
                self._idle_work()
                continue
            self._run_task(task)
        self._close("shutting down")

    def _idle_work(self) -> None:
        """Between tasks: refresh the live view, or hand the link back."""
        if self._exclusive:
            return  # something owns the link outright; do not interrupt it
        if self._release_requested:
            self._release_requested = False
            self._close("released")
            return
        now = time.monotonic()
        # Polling with nothing selected would fail on every beat and turn the
        # link red for something the user has not asked for yet.
        if self._live and self._poll_fn is not None and self._can_poll():
            if now - self._last_poll >= self.poll_interval:
                self._last_poll = now
                self._run_task(Task(self.poll_task_name, self._poll_fn, quiet=True))
            return
        if self._link is not None and now - self._last_used >= self.idle_timeout:
            self._close("idle")
        elif self._link is not None and int(now) != self._last_tick:
            self._last_tick = int(now)
            self._publish_link()  # keep the release countdown moving
        with self._lock:
            self._forget_old_jobs()

    def _can_poll(self) -> bool:
        """Whether a live refresh has anything to read from."""
        try:
            self._check_ready()
        except Exception:  # noqa: BLE001 - "not yet" is an answer, not a fault
            return False
        return True

    def _run_task(self, task: Task) -> None:
        """Run one task, reporting the link state around it."""
        if self._release_requested and not task.exclusive:
            self._release_requested = False
            self._close("released")
        started = time.monotonic()
        job = task.job
        if job is not None:
            job.state = "running"
            job.started = time.time()
            self._publish_job(job)
        try:
            link = self._require_link(quiet=task.quiet)
            self._set_state(self._busy_state(task), task.name, quiet=task.quiet)
            task.result = self._invoke(task, link)
        except BaseException as exc:  # noqa: BLE001 - reported, never swallowed
            task.error = exc
            self._after_failure(exc)
            self._finish_job(job, failure=exc)
            if not task.quiet:
                self._note_activity(
                    task.name, time.monotonic() - started, False, describe(exc)
                )
        else:
            self._finish_job(job, result=task.result)
            if not task.quiet:
                self._note_activity(task.name, time.monotonic() - started, True, "")
        finally:
            task.done.set()
            self._last_used = time.monotonic()
            with self._lock:
                self._exclusive = False
            if self._link is not None:
                self._set_state(self.IDLE)

    def _finish_job(
        self,
        job: Job | None,
        *,
        result: Any = None,
        failure: BaseException | None = None,
    ) -> None:
        """Close a job's record and tell the page, if this task had one."""
        if job is None:
            return
        job.finished = time.time()
        if failure is not None:
            job.state = "failed"
            job.error = describe(failure)
        else:
            # A callable either returns its result or has been filling it in
            # as it went; anything else means the latter.
            if isinstance(result, dict):
                job.result = result
            job.state = "done"
            job.progress = 1.0
        self._publish_job(job)

    def _require_link(self, *, quiet: bool = False) -> LinkT:
        """Return the live link, opening it if it is not held."""
        if self._link is not None:
            return self._link
        self._check_ready()
        self._set_state(self.OPENING, self._opening_note(), quiet=quiet)
        self._link = self._open_link()
        return self._link

    def _opening_note(self) -> str:
        """Return what the page says while the link is being opened."""
        return "Connecting"

    def _close(self, why: str, *, quiet: bool = False) -> None:
        """Hand the link back, if one is held."""
        link = self._link
        self._link = None
        if link is not None:
            self._close_link(link)
        if not quiet:
            self._set_state(self.RELEASED, why if link is not None else "")


def _job_key(job: Job) -> tuple[float, int]:
    """Sort jobs by start time, then id (queued ones have no start time)."""
    return (job.started or float("inf"), job.id)


__all__ = [
    "ACTIVITY_HISTORY",
    "DEFAULT_IDLE_TIMEOUT_S",
    "DEFAULT_POLL_INTERVAL_S",
    "DEFAULT_TASK_TIMEOUT_S",
    "JOB_NOTIFY_INTERVAL_S",
    "JOB_RETENTION_S",
    "LOOP_TICK_S",
    "MAX_POLL_INTERVAL_S",
    "MIN_POLL_INTERVAL_S",
    "STOP_JOIN_S",
    "Job",
    "Task",
    "Worker",
]
