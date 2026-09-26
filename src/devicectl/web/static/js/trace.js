/* A failure that the device caused, turned into a recording of the next one.
 *
 * When a write to a device fails, the one thing that would tell anybody why
 * -- the frames that went over the wire and what came back -- is only there
 * if somebody had thought to start recording beforehand, which nobody ever
 * has.  So the first such failure starts the recording itself and says so:
 * the page asks for the same thing again, and this time it is on tape.  A
 * failure while a recording is already running offers the recording there
 * and then, from the notice that reported it.
 *
 * Which failures count is the server's to say, not the page's: an error
 * reply carries `traceable` when it came from the device or the link to it
 * -- no answer, a refusal, a checksum -- or from a bug in the program while
 * it was talking to one.  A value out of range, a port nobody has chosen, a
 * request the server was too busy for: none of those is in a trace, and
 * none of them starts one.
 *
 * Whether it does this at all is a choice kept per browser, beside the
 * theme and the bell, and on until somebody turns it off -- from the notice
 * itself, or from the program's Tools tab.  Turning it off also stops a
 * recording it started (the server marks those `automatic`): a switch
 * labelled "record on failure" that is off beside a card still saying
 * "recording" leaves nobody sure which of the two to believe.  A recording
 * somebody started by hand is theirs, and runs on.  Nothing here knows
 * what the recording is of; the program hands in the three calls that
 * start, stop and download it, and `start` is asked for an automatic one.
 */

import { html, useRef, useState } from '/core/vendor/preact-htm.module.js';

let program = 'devicectl';

export function configure({ name }) {
  program = name;
}

const key = () => `${program}-auto-trace`;

function stored() {
  try {
    return localStorage.getItem(key()) !== 'off';
  } catch {
    return true;
  }
}

function store(on) {
  try {
    if (on) localStorage.removeItem(key());
    else localStorage.setItem(key(), 'off');
  } catch {
    /* A browser that keeps nothing keeps the default, which is on. */
  }
}

/* The behaviour, and the preference behind it.
 *
 * `recording` is whether the server says a recording is running, and
 * `automatic` whether this started it; both are read through a ref when a
 * failure lands rather than from the render that wired the handler up.
 * `start`, `stop` and `download` are the program's own calls.
 * `failed(what, err)` is what the program's error path calls in place of a
 * plain error toast; it returns true when it has said something itself.
 */
export function useAutoTrace({ toast, recording, automatic, start, stop, download }) {
  const [enabled, setEnabled] = useState(stored);
  const live = useRef({ recording, automatic });
  live.current = { recording, automatic };

  const set = async (on) => {
    store(on);
    setEnabled(on);
    if (on || !live.current.recording || !live.current.automatic) return;
    await stop();
    toast.info('Stopped the recording a failure started. It is kept, to download or throw away.');
  };

  const actions = () => [
    { label: 'Download trace', onClick: () => download(), dismiss: false },
    { label: 'Stop recording', onClick: () => stop() },
    {
      label: 'Stop recording automatically',
      onClick: async () => {
        await set(false);
        toast.info('Failures will no longer start a recording. The Tools tab turns it back on.');
      },
    },
  ];

  const failed = (what, err) => {
    if (!err?.traceable || !stored()) return false;
    const said = what ? `${what}: ${err.message}` : err.message;
    if (live.current.recording) {
      toast.error(
        `${said}. The serial trace was recording while it happened, so what went over ` +
          'the wire is in it.',
        { actions: actions() }
      );
      return true;
    }
    Promise.resolve()
      .then(start)
      .then(
        () =>
          toast.error(
            `${said}. That came from the device, so a serial trace is recording now: ` +
              'try it again, and the trace will have every frame of it.',
            { actions: actions() }
          ),
        () => toast.error(said)
      );
    return true;
  };

  return { enabled, set, failed };
}

/* The preference, as a row for a program's Tools tab. */
export function AutoTraceToggle({ auto }) {
  return html`<div class="row" title="when the device fails a request, start recording the trace and offer it">
    <div class="k">Record on failure</div>
    <div class="v">
      <label class="toggle">
        <span class="muted">${auto.enabled ? 'on' : 'off'}</span>
        <input
          type="checkbox"
          checked=${auto.enabled}
          onChange=${(e) => auto.set(e.target.checked)}
        />
      </label>
    </div>
  </div>`;
}
