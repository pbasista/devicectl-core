/* The shared pieces every panel is built from.
 *
 * One card shape, one row shape, one badge, one dialog, one toast -- so a
 * page of ten cards reads as one page rather than as ten opinions, and so
 * a new panel is a list of rows rather than a fresh set of decisions about
 * padding.  Two programs draw from this now, which is the point: a
 * primitive that suits only one of them is a primitive that was really a
 * page.
 *
 * Nothing here names a device, a program or a colour.  What a card is made
 * of lives in `core.css`, written against tokens alone; what those tokens
 * are is each program's own `app.css`.
 */

import { html, useEffect, useRef, useState } from '../vendor/preact-htm.module.js';

export const DASH = '—';

/* A value as text, with an em dash where there is none.
 *
 * A whole number stays whole: a limit of 16 A reads "16", not "16.0".  Use
 * `fixed` where a column of readings is watched over time -- there, a
 * value that changes width between polls is the worse fault. */
export function fmt(value, digits = 1) {
  if (value === null || value === undefined || value === '') return DASH;
  if (typeof value === 'number') {
    if (Number.isNaN(value)) return DASH;
    return Number.isInteger(value) ? String(value) : value.toFixed(digits);
  }
  return String(value);
}

/* A number at a fixed number of decimals, or an em dash where the device
 * did not answer.  A row that disappears when a register is unmapped
 * changes the card's height between polls; a row with a dash in it does
 * not, and neither does a reading that keeps its decimals. */
export function fixed(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  if (typeof value !== 'number') return String(value);
  return value.toFixed(digits);
}

/* A span of time in the largest unit that still says something useful.
 * It measures how long an operation took, so it runs from milliseconds --
 * a write that lands in 150 ms -- to hours. */
export function span(seconds) {
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

/* How long ago something happened, said in whole units.  A reading is
 * either current or it is not, and "3m ago" is the whole of what a
 * timestamp on a dashboard has to say. */
export function ago(seconds) {
  if (seconds === null || seconds === undefined) return DASH;
  const n = Math.max(0, Math.round(seconds));
  if (n < 60) return `${n}s ago`;
  if (n < 3600) return `${Math.round(n / 60)}m ago`;
  if (n < 86400) return `${Math.round(n / 3600)}h ago`;
  return `${Math.round(n / 86400)}d ago`;
}

/* A long span -- an uptime, a time to full -- in the two largest units it
 * has.  `span` is for something that just happened; this is for something
 * that has been going on. */
export function duration(seconds) {
  if (seconds === null || seconds === undefined) return DASH;
  const n = Math.max(0, Math.round(seconds));
  const days = Math.floor(n / 86400);
  const hours = Math.floor((n % 86400) / 3600);
  const mins = Math.floor((n % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${mins}m`;
  return `${mins}m ${n % 60}s`;
}

/* A moment, the one way every one of these pages writes one: ISO order,
 * with a space where the `T` goes.  `2026-09-26 08:03:46` is unambiguous on
 * both sides of the Atlantic, which "26/09/2026, 08:03:46" and
 * "9/26/2026, 8:03:46 AM" -- what a browser's own locale makes of the same
 * instant -- are not; and a reader comparing it with a terminal's output is
 * reading the same shape.  Takes what a server sends, an ISO string with or
 * without a zone, and keeps the wall clock in it as it was sent: the zone it
 * is in is the device's to say, not the browser's. */
export function stamp(iso) {
  if (!iso) return null;
  return String(iso).replace('T', ' ').slice(0, 19);
}

/* How long the link has to stay busy before the page treats it as busy.
 *
 * The live refresh holds the connection for as long as a device takes to
 * answer, several times a minute.  Reacting to that on the beat meant
 * every button on the page went grey and came back every few seconds,
 * which reads as a fault rather than as a refresh.  Work nobody asked for
 * is filtered out by `link.quiet` before it gets here; this is for the
 * rest -- a write that lands in 150 ms should not flicker the page on its
 * way past either.
 */
const BUSY_SETTLE_MS = 400;

/* True once `busy` has held for BUSY_SETTLE_MS, false the moment it drops.
 *
 * Slow one way and immediate the other, deliberately: a control should
 * come back the instant it can be used, and go away only when there is
 * really something to wait for.  Nothing is lost by letting a click
 * through in the meantime -- the server queues every request behind the
 * one connection anyway, and answers it when its turn comes.
 */
export function useSteadyBusy(busy, delay = BUSY_SETTLE_MS) {
  const [steady, setSteady] = useState(false);
  useEffect(() => {
    if (!busy) {
      setSteady(false);
      return undefined;
    }
    const timer = setTimeout(() => setSteady(true), delay);
    return () => clearTimeout(timer);
  }, [busy, delay]);
  return steady;
}

/* One field of a card: its name on the left, its value on the right.
 *
 * `data` marks a value that is read character by character -- an id, a
 * licence key, a timestamp.  Those get fixed-width digits, and the whole
 * value on hover, since a long one wraps rather than being cut off.
 * `tone` colours the value (`good`, `warn`, `bad`); `token` sets it in the
 * monospace face without claiming it is data.  `pending` is a field holding
 * an edit nobody has applied: its name goes the colour every unsent edit on
 * the page wears -- see `.row.pending` and `Card`'s `draft`.
 *
 * Two tooltips, for two questions.  `hint` is on the name and says what
 * the field *is* -- an acronym spelled out, the register behind it;
 * `title` is on the value and says what it may *be* -- its range, its
 * default.  They were one tooltip on the whole row, so the meaning of a
 * setting and its bounds came up together wherever the pointer was.
 */
export function Row({ k, v, title, hint, data, tone, token, pending }) {
  const plain = typeof v === 'string' || typeof v === 'number' ? String(v) : '';
  const cls = ['v', data ? 'data' : '', tone || '', token ? 'token' : '']
    .filter(Boolean)
    .join(' ');
  return html`<div class=${pending ? 'row pending' : 'row'}>
    <div class=${hint ? 'k hinted' : 'k'} title=${hint}>${k}</div>
    <div class=${cls} title=${title || plain}>
      ${v === null || v === undefined || v === ''
        ? html`<span class="muted">${DASH}</span>`
        : v}
    </div>
  </div>`;
}

/* A word about a value, in the colour of what it says. */
export function Badge({ tone, children, title }) {
  return html`<span class=${tone ? `badge ${tone}` : 'badge'} title=${title}
    >${children}</span
  >`;
}

/* Where a list would be, if there were anything in it. */
export function Empty({ children }) {
  return html`<div class="empty">${children}</div>`;
}

/* One labelled reading, of a row of them: what it is above, the figure
 * below.  Pack voltage, current, power; average cell, spread, balance
 * current.  Three or six of these under a card's title is how a page says
 * "here are the figures" before anything has to be read.
 *
 * Not a `Row`: a row is a field of a card, as wide as the card, with its
 * value against the far edge.  These are read across, not down.
 */
export function Stat({ k, tone, title, children }) {
  return html`<div class=${tone ? `stat ${tone}` : 'stat'} title=${title}>
    <span class="k">${k}</span>
    <span class="v">${children}</span>
  </div>`;
}

/* A row of them. */
export function Stats({ children }) {
  return html`<div class="stats">${children}</div>`;
}

/* The chevron, wherever one says a thing opens.
 *
 * It was the character `▸` -- and `▾` where it pointed down -- set at the
 * smallest size on the page.  Unicode's "small" triangles are drawn small
 * *within* their em as well, so a 12px one came out a few pixels across:
 * at a glance a full stop that had drifted up the line, and on a hidpi
 * screen a dot that the reader is meant to recognise as a direction.  A
 * drawn chevron is the same shape at any size and carries a stroke weight,
 * so it can sit at the size of the letters beside it and still read as one
 * mark rather than a smudge.
 *
 * `down` is a chevron that has been turned a quarter turn, which is what a
 * menu trigger wants: the shape is one shape, turned, rather than two
 * characters that happen to be neighbours in a font.  A disclosure that
 * turns as it opens does it from CSS, on `details[open]`.
 */
export function Chev({ down = false }) {
  return html`<svg
    class=${down ? 'chev down' : 'chev'}
    viewBox="0 0 16 16"
    fill="none"
    stroke="currentColor"
    stroke-width="2"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <path d="M6 3l5 5-5 5" />
  </svg>`;
}

/* One line about a card, with the rest of what there is to say folded
 * behind it.
 *
 * Half the cards on a page opened with three or four sentences of
 * explanation, which is a paragraph of prose above two controls: it pushes
 * the controls down, it wraps to a different number of lines in every
 * card, and the cards in a row then end at four different heights.  The
 * summary is the one line worth reading every time; the paragraph is worth
 * reading once, so it opens.
 *
 * The chevron is what says it opens.  It read "why?" -- a word small
 * enough and faint enough to be taken for part of the sentence, which then
 * went away once the line was open, leaving nothing to press to close it
 * again.
 */
export function Help({ summary, children }) {
  if (!children) return html`<p class="note">${summary}</p>`;
  return html`<details class="help">
    <summary>
      <${Chev} />
      <span class="short">${summary}</span>
    </summary>
    <p>${children}</p>
  </details>`;
}

/* A setting that is legal but will not do what it looks like -- a socket
 * limit above the station's, a cell count the pack does not have.  The
 * card has one line for it and the explanation takes three, so the short
 * form is what shows: the whole of it is the hover title, and clicking
 * opens it in place for anyone without a mouse to hover with. */
export function Caveats({ items }) {
  if (!items?.length) return null;
  return html`<div class="caveats">
    ${items.map(
      (item) => html`<details class="why" key=${item.short || item}>
        <summary title=${item.detail || item}>
          <span class="mark">!</span>
          <span class="short">${item.short || item}</span>
          <${Chev} />
        </summary>
        <p>${item.detail || item}</p>
      </details>`
    )}
  </div>`;
}

/* A panel.
 *
 * `width` is one of three, not a spans-everything boolean: the default is
 * one column of the grid, `wide` is two, and `full` is the row.  The
 * boolean it replaced was on eleven cards, which is what made a page of
 * them read as a stack of banners rather than a grid -- so `full` is now
 * for the things that genuinely are a row wide (a table, the log, a plot)
 * and `wide` for a form of two columns.  Everything else is one column,
 * and roughly square.  `tools/frontlint.py` (C005) holds cards to it.
 *
 * `badge` and `actions` both hang on the right of the title: a badge says
 * what the card is, and actions are what can be done to it.  `foot` is
 * ruled off below the body, for what belongs under a card's content.
 *
 * `draft` is a view from `useDraft` (drafts.js) -- or `pending`, a count,
 * where the edits are counted some other way.  A card holding any wears
 * them: its edge goes the colour of an unsent edit and its title says how
 * many are waiting.  A long page of settings scrolls out of sight, and a
 * changed field three cards down is one amber outline among forty grey
 * ones; the card is the unit somebody scrolling past actually sees.  When
 * the draft's scope can be sent, the card's own Apply and Discard join the
 * count in its title (`CardApply`) -- the same edits the header's Apply
 * counts among everything else that is waiting.
 *
 * `immediate` is a card whose controls write the moment they are used,
 * with no draft and no Apply: it says so in its title, so nobody goes
 * looking for the Apply that sends it.
 */
export function Card({ title, badge, actions, width, help, foot, children, draft, pending, immediate }) {
  const held = pending ?? draft?.count ?? 0;
  const cls = ['card', width || '', held ? 'pending' : ''].filter(Boolean).join(' ');
  return html`<section class=${cls}>
    ${title
      ? html`<h2>
          <span class="grow">${title}</span>
          ${held ? html`<${Unsent} count=${held} />` : null}
          ${held && draft?.writable
            ? html`<${CardApply}
                count=${held}
                busy=${draft.busy}
                disabled=${draft.disabled}
                onApply=${draft.apply}
                onDiscard=${draft.clear}
              />`
            : null}
          ${immediate ? html`<${Immediate} />` : null}
          ${badge}${actions}
        </h2>`
      : null}
    ${help ? html`<${Help} summary=${help.summary}>${help.body}<//>` : null}
    <div class="card-body">${children}</div>
    ${foot ? html`<div class="card-foot">${foot}</div>` : null}
  </section>`;
}

/* The badge on a card that has no Apply because it needs none. */
export function Immediate() {
  return html`<span
    class="badge"
    title="What is changed in this card is written to the device straight away; there is nothing to apply."
  >
    applied immediately
  </span>`;
}

/* The words on a card holding edits: how many, and that they have not gone
 * anywhere yet -- the same "not sent yet" the header's Apply says. */
export function Unsent({ count }) {
  return html`<span class="badge unsent" title="changed here and not written to the device yet">
    ${count} not sent
  </span>`;
}

/* Apply and Discard for one card's edits, in its title beside the count.
 *
 * They were a bar in the card's foot, which appeared with the first edit
 * and so made the card taller -- and a grid row is as tall as its tallest
 * card, so one changed field moved every card below it down the page.  In
 * the title they take no height: two buttons the size of the badge beside
 * them, a tick to send and a cross to throw away, each with its words in
 * its tooltip and its accessible name.  `Card` draws them from its draft:
 * the tick is the draft's `apply`, which shows a plan first where the
 * scope has one, and the cross its `clear`. */
const TICK_SVG = {
  viewBox: '0 0 16 16',
  width: 12,
  height: 12,
  fill: 'none',
  stroke: 'currentColor',
  'stroke-width': 2.2,
  'stroke-linecap': 'round',
  'stroke-linejoin': 'round',
  'aria-hidden': 'true',
  focusable: 'false',
};

export function CardApply({ count, busy, disabled, onApply, onDiscard }) {
  const what = `${count} change${count === 1 ? '' : 's'}`;
  const send = `Apply ${what}`;
  const drop = `Discard ${what}`;
  return html`<span class="card-apply" role="group" aria-label=${`${what} not sent`}>
    <button
      type="button"
      class="btn tick primary"
      title=${send}
      aria-label=${send}
      disabled=${busy || disabled}
      onClick=${onApply}
    >
      <svg ...${TICK_SVG}><path d="M3 8.5l3.2 3.2L13 4.8" /></svg>
    </button>
    <button
      type="button"
      class="btn tick"
      title=${drop}
      aria-label=${drop}
      disabled=${busy}
      onClick=${onDiscard}
    >
      <svg ...${TICK_SVG}><path d="M4 4l8 8M12 4l-8 8" /></svg>
    </button>
  </span>`;
}

/* A popover that belongs to the pointer and the keyboard both: a click
 * anywhere else, or Escape, puts it away.  Returns the open flag, a
 * toggle, the ref to hang on whatever counts as "inside", and the ref for
 * the button that opens it.
 *
 * Closing gives the keyboard back.  Escape used to leave focus on a button
 * that no longer existed, which drops it at the top of the document: the
 * menu is dismissed and the next Tab starts the page over.  So the trigger
 * is remembered and refocused, and only when the popover itself had the
 * focus -- closing because a click landed somewhere else must not pull the
 * keyboard away from wherever that click went. */
export function usePopover() {
  const [open, setOpen] = useState(false);
  const box = useRef(null);
  const trigger = useRef(null);

  const close = () => {
    if (box.current?.contains(document.activeElement)) trigger.current?.focus();
    setOpen(false);
  };

  useEffect(() => {
    if (!open) return undefined;
    /* A dialog opened from inside the popover -- the list behind a count
     * in a menu -- sits on top of it rather than somewhere else: a click in
     * it, or the Escape that closes it, is about the dialog.  The popover
     * goes on the next click outside both, not on the way back from it. */
    const elsewhere = (event) => {
      if (event.target.closest?.('.backdrop')) return;
      if (!box.current?.contains(event.target)) setOpen(false);
    };
    const key = (event) => {
      if (event.key !== 'Escape') return;
      if (document.querySelector('[aria-modal="true"]')) return;
      if (box.current?.contains(document.activeElement)) trigger.current?.focus();
      setOpen(false);
    };
    document.addEventListener('mousedown', elsewhere);
    window.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('mousedown', elsewhere);
      window.removeEventListener('keydown', key);
    };
  }, [open]);

  return {
    open,
    box,
    trigger,
    toggle: () => (open ? close() : setOpen(true)),
    close,
  };
}

/* What a browser will let the keyboard reach. */
const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

/* Everything a modal owes the keyboard, as one ref to hang on its box.
 *
 * `aria-modal` is a promise to a screen reader that the rest of the page
 * is not there; without a trap it is a promise the page does not keep, and
 * Tab walks straight out of the dialog into the controls behind it -- with
 * the reader still saying it is inside.  So: focus moves in when the
 * dialog opens (to whatever it holds that can take it, or the box itself),
 * Tab and Shift+Tab wrap at the ends, Escape asks it to close, and the
 * element that opened it gets the keyboard back when it goes.
 */
export function useModal(onClose) {
  const box = useRef(null);

  useEffect(() => {
    const from = document.activeElement;
    const first = box.current?.querySelector(FOCUSABLE);
    (first || box.current)?.focus();

    const key = (event) => {
      if (event.key === 'Escape') {
        onClose?.();
        return;
      }
      if (event.key !== 'Tab' || !box.current) return;
      const inside = [...box.current.querySelectorAll(FOCUSABLE)];
      if (!inside.length) {
        event.preventDefault();
        return;
      }
      const edge = event.shiftKey ? inside[0] : inside[inside.length - 1];
      if (document.activeElement !== edge) return;
      event.preventDefault();
      (event.shiftKey ? inside[inside.length - 1] : inside[0]).focus();
    };
    window.addEventListener('keydown', key);
    return () => {
      window.removeEventListener('keydown', key);
      /* Only if the dialog still had it: a click that lands somewhere
       * else has already chosen where the keyboard should be. */
      if (from && (!box.current || box.current.contains(document.activeElement))) {
        from.focus?.();
      }
    };
  }, [onClose]);

  return box;
}

/* A dialog: a title, some content, and a way out.
 *
 * The title is the accessible name, through `aria-labelledby` rather than
 * a second copy of it in an `aria-label` that can drift from the heading a
 * reader can see. */
let dialogSeq = 0;

export function Dialog({ title, children, onClose, width, actions }) {
  const [id] = useState(() => {
    dialogSeq += 1;
    return `dialog-title-${dialogSeq}`;
  });
  const box = useModal(onClose);
  return html`<div
    class="backdrop"
    onClick=${(e) => e.target === e.currentTarget && onClose?.()}
  >
    <div
      class="modal"
      ref=${box}
      tabindex="-1"
      style=${width ? `width:min(${width}px,100%)` : ''}
      role="dialog"
      aria-modal="true"
      aria-labelledby=${id}
    >
      <h3 id=${id}>${title}</h3>
      ${children}
      <div class="buttons">
        ${actions || html`<button class="btn" onClick=${onClose}>Close</button>`}
      </div>
    </div>
  </div>`;
}

/* The two lines a device is named by: the one string no other device on the
 * bench shares, and what the thing is.
 *
 * Wherever a device is named it is named in this shape -- in the header, and
 * in the dialog that changes which one the page is about -- because those
 * two places answer one question between them: am I on the right device,
 * and if not, which of these is it?  They had not been.  The header carried
 * a serial number over a model and two version strings; the dialog beside
 * it listed the same boards as "BMS 1" with the model after it, so a bank
 * of matching units read as four identical rows and the line the header had
 * just shown was nowhere on the page.
 */
export function Lines({ primary, secondary }) {
  return html`<span class="lines">
    <span class="primary">${primary}</span>
    <span class="secondary">${secondary}</span>
  </span>`;
}

/* Which of the devices on the other end this page is about.
 *
 * One button in the header, one dialog of choices.  Both programs had a way
 * to switch: one a button opening a list, the other a bare `<select>` in the
 * header -- a dropdown that named boards in twenty characters, could not say
 * which of them was in trouble, and looked like nothing else on either page.
 * A list is what this is: a name, a line about it, and the one you are on
 * marked.
 *
 * `entries` is `{ key, label, detail, aside, tone, selected }` each: the two
 * lines a device is named by, and then whatever this program files a device
 * under that is neither of them -- an address on a bus, where a name was
 * found.  `children` is whatever the program needs under the list -- typing
 * an address in by hand, a rescan -- because what "a device you have not got
 * yet" means differs between a bus you can sweep and a network you can only
 * ask.
 */
export function Picker({ title, entries, onPick, onClose, looking, empty, width, children }) {
  const list = entries || [];
  return html`<${Dialog}
    title=${title}
    onClose=${onClose}
    width=${width || 560}
    actions=${html`<button class="btn ghost" onClick=${onClose}>Cancel</button>`}
  >
    ${looking
      ? html`<p class="note flush">Looking...</p>`
      : list.length === 0
        ? html`<p class="note flush">${empty || 'Nothing to choose from yet.'}</p>`
        : html`<div class="scroller stack">
            ${list.map(
              (entry) => html`<button
                class=${`btn pick${entry.tone ? ` ${entry.tone}` : ''}`}
                key=${entry.key}
                aria-current=${entry.selected ? 'true' : undefined}
                onClick=${() => onPick(entry)}
              >
                <${Lines} primary=${entry.label} secondary=${entry.detail} />
                ${entry.aside ? html`<span class="aside">${entry.aside}</span>` : null}
                ${entry.selected ? html`<span class="badge good">this one</span>` : null}
              </button>`
            )}
          </div>`}
    ${children}
  <//>`;
}

/* Anything irreversible asks first, with the reason in the dialog rather
 * than in a tooltip somebody has to go looking for.
 *
 * `typed` is for the few that are worse than irreversible -- erasing a
 * transaction database, writing firmware: the button stays dead until the
 * word is typed, so the dialog cannot be dismissed by reflex. */
export function Confirm({
  title,
  body,
  confirmLabel,
  danger,
  typed,
  onConfirm,
  onCancel,
}) {
  const [text, setText] = useState('');
  const ready = !typed || text.trim().toLowerCase() === typed;
  return html`<${Dialog}
    title=${title}
    onClose=${onCancel}
    actions=${html`
      <button class="btn ghost" onClick=${onCancel}>Cancel</button>
      <button
        class=${danger ? 'btn danger' : 'btn primary'}
        disabled=${!ready}
        onClick=${onConfirm}
      >
        ${confirmLabel || 'Confirm'}
      </button>
    `}
  >
    <p>${body}</p>
    ${typed
      ? html`<p class="muted">
          Type <b>${typed}</b> to confirm:
          <input
            type="text"
            class="typed"
            value=${text}
            onInput=${(e) => setText(e.target.value)}
          />
        </p>`
      : null}
  <//>`;
}

/* Asking before doing, as one line in the panel that does it.
 *
 * Nine panels had written this out for themselves: a `useState(null)`, a
 * `<${Confirm}>` at the bottom of the render, and the same ordering to get
 * right every time -- run the action *before* clearing the dialog, or the
 * closure that holds what to do is gone by the time it is called.  Here it
 * is once.
 *
 * `ask` takes the dialog whole -- its words and its `run`, which is what
 * to do if the answer is yes.  `node` is what the panel renders, and is
 * null until something has been asked.
 */
export function useConfirm() {
  const [pending, setPending] = useState(null);

  const ask = (question) => setPending(question);

  const node = pending
    ? html`<${Confirm}
        ...${pending}
        onConfirm=${() => {
          const { run } = pending;
          setPending(null);
          run?.();
        }}
        onCancel=${() => setPending(null)}
      />`
    : null;

  return { ask, node, open: Boolean(pending) };
}

/* Every notice the page raises, and the one place a screen reader is told
 * about them.  `polite` rather than `assertive`: these report what just
 * happened, and none of them is worth cutting somebody off mid-sentence.
 *
 * A notice can carry buttons -- what to do about it, right there -- which
 * is what a failure that started a recording needs: the way to fetch the
 * recording, and the way to stop it, beside the sentence that said so. */
export function Toasts({ toasts, dismiss }) {
  return html`<div class="toasts" role="status" aria-live="polite">
    ${toasts.map(
      (toast) => html`<div class=${`toast ${toast.kind}`} key=${toast.id}>
        <div class="said">
          <span>${toast.text}</span>
          ${toast.actions?.length
            ? html`<div class="toast-actions">
                ${toast.actions.map(
                  (action) => html`<button
                    class="btn small"
                    key=${action.label}
                    disabled=${action.disabled}
                    onClick=${() => {
                      action.onClick();
                      if (action.dismiss !== false) dismiss(toast.id);
                    }}
                  >
                    ${action.label}
                  </button>`
                )}
              </div>`
            : null}
        </div>
        <button
          class="x"
          title="dismiss"
          aria-label="dismiss"
          onClick=${() => dismiss(toast.id)}
        >
          ×
        </button>
      </div>`
    )}
  </div>`;
}

/* Toast list plus the helpers views use to add to it.
 *
 * A failure stays until it is dismissed.  It used to go after twelve
 * seconds, which is long enough to see that something is red and not long
 * enough to read what, let alone copy it into a message -- and the one
 * notice on a page that says a write did not happen is the last one that
 * should take itself away while somebody is still looking for it.  Good
 * news and information still go on their own. */
export function useToasts() {
  const [toasts, setToasts] = useState([]);
  const seq = useRef(0);
  const dismiss = (id) => setToasts((list) => list.filter((t) => t.id !== id));
  const push = (kind, text, linger, actions) => {
    seq.current += 1;
    const id = seq.current;
    setToasts((list) => [...list, { id, kind, text, actions }]);
    if (linger) setTimeout(() => dismiss(id), linger);
    return id;
  };
  return {
    toasts,
    dismiss,
    ok: (text) => push('ok', String(text), 6000),
    info: (text, { actions } = {}) => push('info', text, actions?.length ? 0 : 6000, actions),
    error: (text, { actions } = {}) => push('error', text, 0, actions),
  };
}

/* Doing something to the device, and saying how it went, in one call.
 *
 * Nineteen handlers had written `.then(toast.ok).catch(toast.error)` out
 * by hand, each with its own idea of what to say and its own chance of
 * forgetting the catch -- which is an unhandled rejection and a page that
 * says nothing at all.  This is that line, once.  It returns the promise,
 * already handled, so a caller that wants to do something after can, and a
 * caller that does not is still safe -- unless `raise` asks for the failure
 * back, which is what a draft's write needs to know it did not go.
 */
export function caller(toast) {
  return (work, said, { raise = false } = {}) =>
    Promise.resolve()
      .then(work)
      .then((answer) => {
        if (said) toast.ok(typeof said === 'function' ? said(answer) : said);
        return answer;
      })
      .catch((err) => {
        toast.error(err.message || String(err));
        /* A draft's write has to fail to keep its edits: see drafts.js. */
        if (raise) throw err;
        return undefined;
      });
}

/* A select that does not re-render when nothing about it changed.
 *
 * The page re-renders on every link beat -- once a second while a device
 * is connected -- and each one rebuilt the option vnodes, so the differ
 * rewrote every option's value on the way past.  That is a mutation of the
 * very list a browser's open dropdown is showing, so the popup rebuilt
 * under the pointer and the highlight moved with it: a category dropdown
 * jumped to a different option from time to time, and only while a device
 * was connected, which is the only time the page re-renders on a beat.
 * Handing the differ back the same vnode it already rendered
 * short-circuits the diff before it reaches the DOM, and the open popup
 * keeps its hover.
 *
 * `entries` is compared by value rather than identity on purpose: callers
 * build it inline, and a stable identity is exactly what a plain array
 * built on render does not have.  Each entry is `{value, title}` or the
 * `[value, title]` pair the same table is often already in.
 *
 * `onChange` is handed the chosen value, and the event after it: what a
 * caller wants is the value in all but a handful of cases, and reading it
 * off the event was one more chance to write `e.target.checked`.
 */
export function Select({ value, onChange, entries, disabled, pending, onKeyDown }) {
  const options = (entries || []).map((entry) =>
    Array.isArray(entry) ? { value: entry[0], title: entry[1] } : entry
  );
  const prev = useRef(null);
  /* The handlers live in a ref that the memoised vnode reads through, so a
   * cached `<select>` still calls the current ones.  `onChange` would get
   * away without it -- most callers close over a state setter, which is
   * the same function for the life of the page -- but `onKeyDown` cannot:
   * an Enter-to-save closes over the value being edited, and a copy cached
   * with the vnode would save the value it held when the dropdown was
   * first drawn. */
  const live = useRef(null);
  live.current = { onChange, onKeyDown };
  const shown = value === null || value === undefined ? '' : String(value);
  if (
    !prev.current ||
    shown !== prev.current.value ||
    disabled !== prev.current.disabled ||
    pending !== prev.current.pending ||
    !sameEntries(prev.current.entries, options)
  ) {
    prev.current = {
      value: shown,
      disabled,
      pending,
      entries: options,
      /* The whole of what is selected, on hover.  A `<select>` is capped
       * at the width of the column it sits in, so the longest options are
       * cut with an ellipsis, and the pointer is how the rest is read. */
      vnode: html`<select
        class=${pending ? 'pending' : ''}
        value=${shown}
        onChange=${(event) => live.current.onChange?.(event.target.value, event)}
        onKeyDown=${(event) => live.current.onKeyDown?.(event)}
        disabled=${disabled}
        title=${options.find((e) => String(e.value) === shown)?.title || ''}
      >
        ${options.map(
          (e) => html`<option value=${e.value} key=${e.value}>${e.title}</option>`
        )}
      </select>`,
    };
  }
  return prev.current.vnode;
}

function sameEntries(a, b) {
  return (
    a.length === b.length &&
    a.every((entry, i) => entry.value === b[i].value && entry.title === b[i].title)
  );
}

/* A bar with a fraction in it, and a word about what the fraction is. */
export function Bar({ fraction, label }) {
  const known = fraction !== null && fraction !== undefined;
  const width = Math.max(0, Math.min(1, fraction ?? 0)) * 100;
  return html`<div>
    <div class=${known ? 'bar' : 'bar unknown'}>
      <span style=${known ? `width:${width}%` : ''}></span>
    </div>
    ${label ? html`<div class="bar-note muted">${label}</div>` : null}
  </div>`;
}

/* A small ring that turns, for a wait that fits inside a control.
 *
 * `Bar` is the other answer to "this is taking a while", and it is for a
 * job with a start, an end and something to say about the distance
 * between.  This is for a wait that has none of those: a single register
 * going out over a serial bus, which is done when it is done.
 */
export function Spinner({ label }) {
  return html`<span class="spinner" role="img" aria-label=${label || 'working'}></span>`;
}

/* Something is being read from the device, and it is taking a while.
 *
 * A read that pages the device -- an event log, a transaction database,
 * every property for a backup -- holds the one connection for seconds at a
 * time, and the page had nothing to show for it but the pill's "busy",
 * which is also what a 150 ms write looks like.  So the panel that asked
 * draws a bar while its own read is running, with whatever the worker can
 * say about how far it has got.
 *
 * `what` is the beginning of the operation's name, as the worker publishes
 * it: a panel shows the bar for its own read and not for someone else's
 * write going past.  A panel whose read has more than one name passes an
 * array, so that no panel has to widen its prefix until it matches
 * everybody else's reads too.  `tools/frontlint.py` (C004) checks every
 * name here against the operations the program's `web/api.py` publishes.
 */
export function Progress({ link, what }) {
  const names = what === undefined ? [] : Array.isArray(what) ? what : [what];
  const op = link?.op || '';
  const running =
    link &&
    (link.state === 'busy' || link.state === 'opening') &&
    !link.quiet &&
    (names.length === 0 || names.some((name) => op.startsWith(name)));
  if (!running) return null;
  const known = link.progress !== null && link.progress !== undefined;
  return html`<div class="reading" role="status">
    <div class="what">
      <span>${link.op || 'Reading the device'}</span>
      <span class="muted"
        >${link.note || (known ? `${Math.round(link.progress * 100)}%` : '')}</span
      >
    </div>
    <div class=${known ? 'bar' : 'bar unknown'}>
      <span style=${known ? `width:${Math.round(link.progress * 100)}%` : ''}></span>
    </div>
  </div>`;
}

/* Live updates, as one switch in the header.
 *
 * This lived in the link pill's menu, with Connect, Release and the
 * watcher count, on the reasoning that those are controls nobody touches
 * twice an hour.  That is true of the other three and was never true of
 * this one: it decides whether every number on the page is a reading or a
 * memory, and it is the thing people reach for the moment a device starts
 * doing something.  It costs one click here and cost two there.
 *
 * A mode rather than an action, so `aria-pressed` rather than a label that
 * changes.  The state is in the shape as well as the colour -- the line is
 * flat when the page is not reading and beats when it is -- so it survives
 * a screen that has no colour to read.
 *
 * It was three arcs rising from a dot, which is the signal-strength mark
 * every phone and laptop draws for a radio: on a page about a device on a
 * network, beside a Wi-Fi tab, a switch wearing that icon reads as "the
 * link is good", which is a different claim entirely and one this button
 * does not make.  A pulse says what this actually is -- something being
 * read over and over -- and belongs to no other meaning here.
 */
export function LiveToggle({ link, offline, onLive, program }) {
  const live = Boolean(link?.live);
  const every = link?.pollInterval || 3;
  const title = offline
    ? `Live updates need the ${program} server.`
    : live
      ? `Live updates on -- reading the device every ${every}s. Click to pause.`
      : 'Live updates paused. Click to resume.';
  return html`<button
    type="button"
    class=${`btn small ghost icon live${live ? ' on' : ''}`}
    title=${title}
    aria-label=${title}
    aria-pressed=${live}
    disabled=${offline}
    onClick=${() => onLive(!live)}
  >
    <svg
      viewBox="0 0 16 16"
      width="15"
      height="15"
      fill="none"
      stroke="currentColor"
      stroke-width="1.6"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path class="flat" d="M1.6 8h12.8" />
      <path class="beat" d="M1.6 8h2.6l1.7-4.2L8.9 12.4l1.4-4.4h2.7" />
    </svg>
  </button>`;
}

/* The server has gone, and every control on the page is about to lie about
 * a device it can no longer reach.
 *
 * The page cannot fix that and does not pretend to.  It says which of the
 * two things went -- the program on this machine, not the device on the
 * other end of the wire -- keeps trying, and offers the one thing a person
 * can do about it.  Nothing here is dismissible: a banner you can wave
 * away is a banner that stops being true quietly.
 *
 * It sits *in* the header rather than under it.  As its own bar it was a
 * block that appeared and disappeared with the server, and every blip
 * pushed the tabs and the whole page down and back -- movement caused by
 * the one message on the page that is not about the device.  The header is
 * a flex row with a spacer in it, which on any laptop is hundreds of
 * pixels of slack, and its height is set by the two-line device name, so
 * one line of small text costs nothing.  The pill beside it already reads
 * "server unreachable"; this is the sentence that says which server and
 * what to do.
 *
 * The sentence yields space rather than taking it, and below the narrow
 * breakpoint it goes entirely -- so the announcement is carried by a
 * visually-hidden copy that is always there in full, and the visible text
 * is plain.  Eliding text for the eye must not elide it for a reader.
 */
export function OfflineNotice({ state, onRetry, program }) {
  if (state !== 'offline' && state !== 'reconnecting') return null;
  const gone = state === 'offline';
  const said = gone
    ? `The ${program} server is not answering. Nothing on this page is live.`
    : `Lost the ${program} server -- trying to get it back...`;
  return html`<div class=${gone ? 'offline-note gone' : 'offline-note'}>
    <span class="sr-only" role="alert">${said}</span>
    <span class="dot" aria-hidden="true"></span>
    <span class="what" aria-hidden="true">${said}</span>
    <button class="btn small" onClick=${onRetry}>Try now</button>
  </div>`;
}

/* --- a device's clock, said the same way on both dashboards ---------------
 *
 * Both programs put the device's clock on the card that names the device,
 * and each had said it its own way: "Unit clock" in whatever format the
 * browser's locale picked, beside "Off this host by 03h 30m" with no
 * direction; and "Local time" in ISO beside "3.5 hours ahead of this
 * computer".  Neither a charger nor a battery board keeps a zone -- the
 * charger keeps UTC and an offset it was given, the board a count from local
 * midnight -- so what either can show is its local time, and it is named
 * that.  The difference is the server's phrase, `devicectl.clock`, which
 * leaves out whose computer it is: the page is often read on another one.
 *
 * `clock` is `{ local, drift, driftS }`: an ISO wall-clock time, the phrase,
 * and the signed seconds it was made from.  `zone` is what the device says
 * about where its local time is, as the local row's hover; `outAfter` is
 * how far out, in seconds, is worth a warning.
 */
export function ClockRows({ clock, zone, outAfter = 60 }) {
  const drift = clock?.driftS;
  const out = typeof drift === 'number' && Math.abs(drift) > outAfter;
  return html`
    <${Row} k="Local time" v=${stamp(clock?.local)} data=${true} title=${zone} />
    <${Row}
      k="Clock difference"
      v=${clock?.drift ? html`<span class=${out ? 'badge warn' : ''}>${clock.drift}</span>` : null}
      title="how far the device's clock is from the clock of the computer running this program"
    />
  `;
}

/* Setting the device's clock from the server's, as the one action on the
 * card that shows the clock -- so it goes in the card's title, where it
 * costs no height, and says the same two words in both programs. */
export function SyncClock({ busy, onSync }) {
  return html`<button
    class="btn small"
    disabled=${busy}
    onClick=${onSync}
    title="set the device's clock from the clock of the computer running this program"
  >
    Sync clock
  </button>`;
}

/* The health check's one button, in its card's title.  "Run check" until it
 * has run, "Check again" after -- both programs, the same words. */
export function RunCheck({ ran, running, busy, onRun }) {
  return html`<button class="btn small" disabled=${busy || running} onClick=${onRun}>
    ${running ? 'Checking…' : ran ? 'Check again' : 'Run check'}
  </button>`;
}

/* --- a health check -------------------------------------------------------
 *
 * Both programs have a doctor, and both put it in a card on the dashboard
 * -- which each had written for itself, so one said "nothing wrong" where
 * the other said "nothing to report", one listed findings with the command
 * that fixes them and the other put them in rows without it.  "Nothing to
 * report" is the one to keep: a check can only say what it looked at, and
 * a pack or a charger it found nothing on is not thereby a pack or a
 * charger with nothing wrong.
 *
 * `report` is the shape devicectl.doctor gives every program: `findings`,
 * each `{ severity, area, detail, fix }`, and `unavailable`, what could not
 * be looked at.  `onRun` asks for a new one and says itself if that fails. */
const SEVERITY_TONE = { error: 'bad', warning: 'warn' };
const SEVERITY_ORDER = ['error', 'warning', 'note'];

function plural(n, word) {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

/* What a report comes to, on the badge beside the card's title. */
export function HealthBadge({ report }) {
  if (!report) return null;
  const findings = report.findings || [];
  if (!findings.length) return html`<${Badge} tone="good">nothing to report<//>`;
  const counts = SEVERITY_ORDER.map((severity) => [
    severity,
    findings.filter((f) => f.severity === severity).length,
  ]).filter(([, n]) => n);
  const worst = counts[0]?.[0];
  return html`<${Badge} tone=${SEVERITY_TONE[worst] || ''}>
    ${counts.map(([severity, n]) => plural(n, severity)).join(', ')}
  <//>`;
}

export function HealthCard({ report, busy, onRun, help, idle }) {
  const [running, setRunning] = useState(false);
  const run = async () => {
    setRunning(true);
    try {
      await onRun();
    } catch {
      /* Said already, by whoever ran it. */
    } finally {
      setRunning(false);
    }
  };
  const findings = report?.findings || [];
  return html`<${Card}
    title="Health"
    badge=${html`<${HealthBadge} report=${report} />`}
    actions=${html`<${RunCheck} ran=${!!report} running=${running} busy=${busy} onRun=${run} />`}
    help=${help}
  >
    ${!report
      ? html`<${Empty}>${idle || 'Not run yet.'}<//>`
      : findings.length
        ? html`<ul class="list">
            ${findings.map(
              (f, i) => html`<li key=${i}>
                <${Badge} tone=${SEVERITY_TONE[f.severity] || ''}>${f.severity}<//>
                <span>
                  ${f.area ? html`<b>${f.area}</b>: ` : null}${f.detail}
                  ${f.fix ? html`<br /><span class="muted mono">${f.fix}</span>` : null}
                </span>
              </li>`
            )}
          </ul>`
        : html`<${Empty}>Nothing to report.<//>`}
    ${report?.unavailable?.length
      ? html`<div class="caveat">
          <span>!</span><span>Could not check: ${report.unavailable.join('; ')}</span>
        </div>`
      : null}
  <//>`;
}
