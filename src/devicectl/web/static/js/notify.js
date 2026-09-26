/* Being told the page was raised, when you are not looking at the page.
 *
 * Raising a tab from inside it is mostly wishful: `window.focus()` is a
 * silent no-op in Firefox outside a user gesture, and a tab whose title
 * changed is not highlighted, coloured or animated by anything -- the flash
 * is only readable when the strip is short enough to show more than
 * a truncated program name.  A notification is the one signal that leaves
 * the browser
 * altogether, and a click on it *is* a user gesture, so its handler is
 * allowed the `focus()` the event itself is not.
 *
 * It has to be asked for, from a click of your own.  Firefox takes
 * `Notification.requestPermission()` only from a user gesture, and a page
 * that asks the moment it loads is the reason for that rule; a refusal is
 * also remembered by the browser for good.  So nothing here happens until
 * the bell in the header is pressed, and the answer is kept per browser,
 * next to the theme.
 */

import { html, useState } from '/core/vendor/preact-htm.module.js';

/* What the program calls itself, in the four sentences this module writes
 * and in the two keys it keeps: the wish, in this browser's storage, and
 * the tag that makes a second run replace the first one's note rather than
 * stack under it.  Set once, from the program's own module, before the
 * bell is rendered. */
let program = 'this program';
let wantKey = 'devicectl-notify';
let noteTag = 'devicectl-focus';

export function configure({ name }) {
  program = name;
  wantKey = `${name}-notify`;
  noteTag = `${name}-focus`;
}

/* Long enough to find on another screen, short enough not to pile up. */
const NOTE_MS = 20000;

/* Notifications are for secure contexts: this page opened on the machine
 * serving it, or served over https.  Shared over plain http to someone
 * else's machine (a `--listen` of somebody else's), Firefox and Chrome take
 * the question away rather than answer it, and the bell has nothing to
 * offer. */
function supported() {
  return (
    typeof Notification !== 'undefined' &&
    typeof Notification.requestPermission === 'function' &&
    window.isSecureContext !== false
  );
}

/* Which of the two reasons it is, in a sentence.
 *
 * Worth telling apart, because one of them is somebody's own doing and can
 * be undone.  A program whose device is on the far end of a serial cable
 * gets run on the machine the cable is in and read from a laptop across the
 * room, and `http://192.168.1.x:8080` is not a secure context -- so the
 * same page that had a bell on the machine serving it has none over the
 * network, which is where it is actually used.  That is worth a sentence
 * rather than a control that quietly is not there.
 */
function whyNot() {
  return window.isSecureContext === false
    ? `This page is not a secure context, so the browser will not offer notifications. ` +
        `Open ${program} on the machine serving it, or put it behind https.`
    : 'This browser does not offer notifications.';
}

function permission() {
  return supported() ? Notification.permission : 'unsupported';
}

function wanted() {
  return localStorage.getItem(wantKey) === '1';
}

/* Show one, and let a click on it do the thing the page may not do itself. */
function show(title, body) {
  try {
    const note = new Notification(title, { body, tag: noteTag, renotify: true });
    note.onclick = () => {
      /* A click is a user gesture, so this focus() is honoured where the
       * one the focus event tries is quietly dropped. */
      window.focus();
      note.close();
    };
    setTimeout(() => note.close(), NOTE_MS);
    return true;
  } catch {
    /* Android's Chrome sends notifications only through a service worker,
     * which this page has none of.  Nothing to do but leave it to the
     * toast, which the tab shows either way. */
    return false;
  }
}

/* The bell's state and the two things it can do, plus the call the event
 * stream makes when the server says a second run of the program wanted this tab.
 *
 * `state` is one of `unsupported` (no notifications in this browser, or not
 * offered on this page), `off`, `on`, or `blocked` -- refused once, which
 * only the browser's own site permissions can undo.
 */
export function useTabAlerts() {
  const [state, setState] = useState(() => {
    const answer = permission();
    if (answer === 'unsupported') return 'unsupported';
    if (answer === 'denied') return 'blocked';
    return answer === 'granted' && wanted() ? 'on' : 'off';
  });

  /* Call this from a click and from nothing else; the browser will not take
   * the question any other way.  Returns what it answered. */
  const enable = async () => {
    let answer;
    try {
      answer = permission() === 'granted' ? 'granted' : await Notification.requestPermission();
    } catch {
      answer = 'unsupported'; /* offered, then refused to be asked */
    }
    if (answer === 'unsupported') {
      setState('unsupported');
      return answer;
    }
    if (answer !== 'granted') {
      setState(answer === 'denied' ? 'blocked' : 'off');
      return answer;
    }
    localStorage.setItem(wantKey, '1');
    setState('on');
    /* One right away: it proves the whole path works, at the moment the
     * question is still in mind, rather than a week later. */
    return show(`${program} ui`, 'Notifications are on. This is what one looks like.')
      ? 'granted'
      : 'unsupported';
  };

  const disable = () => {
    localStorage.removeItem(wantKey);
    setState('off');
  };

  /* Read the wish and the permission afresh rather than closing over
   * `state`: this is called from the event stream's handler, wired up on
   * the first render and remembering nothing since. */
  const notify = () => {
    if (!wanted() || permission() !== 'granted') return false;
    /* You are looking straight at the tab: the toast has already said it. */
    if (document.hasFocus()) return false;
    return show(
      `${program} ui was started again`,
      'This is the tab it meant -- click here to come back to it.'
    );
  };

  return { state, enable, disable, notify };
}

const BELL_TITLES = {
  off: 'notify me when this program raises this tab',
  on: 'notifications are on -- click to turn them off',
  blocked: 'this browser has blocked notifications for this page',
  unsupported: 'notifications are not available on this page -- click to find out why',
};

/* The bell, drawn rather than typed.
 *
 * It was the characters U+1F514 and U+1F515, which every platform draws in
 * its own colour at its own size: a fat orange bell next to a 15px line
 * drawing of a theme and a 15px line drawing of a pulse.  Same box, same
 * stroke, same cap as those two, so the header's icons are one set -- and
 * the same shape in both programs, which a font cannot promise.
 *
 * On is the bell; off is the bell with the stroke through it that every
 * other muted thing wears.  The state is in the shape, not only in the
 * colour, so it survives a screen with no colour to read.
 */
const BELL_SVG = {
  viewBox: '0 0 16 16',
  width: 15,
  height: 15,
  fill: 'none',
  stroke: 'currentColor',
  'stroke-width': 1.5,
  'stroke-linecap': 'round',
  'stroke-linejoin': 'round',
  'aria-hidden': 'true',
  focusable: 'false',
};

/* The body, in one stroke: the crown, the shoulder falling away to a skirt
 * that flares, and the flat lip across the bottom.
 *
 * It had been an arch on a rule -- two verticals, a half-round on top and a
 * line under it -- which is the silhouette of a doorway, a tombstone or a
 * tunnel, and at fifteen pixels beside a sun and a pulse it read as none of
 * them in particular.  A bell is recognised by its skirt: the sides do not
 * come down straight, they swing out at the bottom.  That flare is the
 * whole of the difference, and it costs two curves. */
const BELL_BODY =
  'M8 1.6a4.4 4.4 0 0 0-4.4 4.4c0 2.6-.5 4-1.2 4.9a.6.6 0 0 0 .5 1h10.2a.6.6 0 0 0 .5-1' +
  'c-.7-.9-1.2-2.3-1.2-4.9A4.4 4.4 0 0 0 8 1.6z';

/* The clapper, swinging under the lip. */
const BELL_CLAPPER = 'M6.4 13.3a1.7 1.7 0 0 0 3.2 0';

const BELL_ICON = {
  on: html`<svg ...${BELL_SVG}>
    <path d=${BELL_BODY} />
    <path d=${BELL_CLAPPER} />
  </svg>`,
  off: html`<svg ...${BELL_SVG}>
    <path d=${BELL_BODY} />
    <path d=${BELL_CLAPPER} />
    <path d="M2.6 13.6 13.4 2.4" />
  </svg>`,
};

/* The one control, in the header beside the theme: on, off, blocked and not
 * ours to unblock, or not on offer here at all.
 *
 * Always drawn, including that last case.  It used to return nothing at
 * all when the browser would not take the question, which is how a program
 * read over a plain-http `--listen` came to have no bell in its header
 * while the one next to it, opened on the machine serving it, had one --
 * the same header, the same module, a control present in one and absent in
 * the other, with nothing anywhere saying why.  An absent control cannot be
 * asked what became of it.  This one can: it is there, struck through like
 * every other muted thing, and clicking it says what would have to change.
 */
export function Bell({ alerts, toast }) {
  const state = alerts.state;
  const click = async () => {
    if (state === 'unsupported') {
      toast.info(whyNot());
      return;
    }
    if (state === 'on') {
      alerts.disable();
      toast.info('Notifications off.');
      return;
    }
    if (state === 'blocked') {
      toast.error(
        'This browser has blocked notifications for this page. Allow them in its site permissions to turn them on.'
      );
      return;
    }
    const answer = await alerts.enable();
    if (answer === 'granted') {
      toast.ok(`Notifications on: ${program} will say when this is the tab it meant.`);
    } else if (answer === 'denied') {
      toast.error(
        'Notifications blocked. Only this browser can undo that, in its site permissions.'
      );
    } else if (answer === 'unsupported') {
      toast.error('This browser offered notifications and then would not send one.');
    } else {
      toast.info('The browser was not answered, so nothing changed.');
    }
  };
  return html`<button
    class="btn small ghost icon"
    title=${BELL_TITLES[state]}
    aria-label=${BELL_TITLES[state]}
    aria-pressed=${state === 'on'}
    onClick=${click}
  >
    ${BELL_ICON[state === 'on' ? 'on' : 'off']}
  </button>`;
}
