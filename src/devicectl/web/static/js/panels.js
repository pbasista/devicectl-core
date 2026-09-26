/* Shared plumbing for the panel tabs: fetch-once state, edit-and-apply
 * cards, and the small pieces every panel reuses.
 *
 * A panel tab is not a dashboard: it is opened to *do* something, so what
 * it reads is kept here -- once per device, across tab switches -- rather
 * than following the dashboard's reload-on-connect flow.  What a panel
 * has changed and not sent is in drafts.js.
 */

import { Card, DASH, Help, Row, Select } from '/core/js/ui.js';
import { html, useEffect, useState } from '/core/vendor/preact-htm.module.js';

/* What every panel has read, keyed by the endpoint rather than by the
 * component that asked for it.
 *
 * Two things wanted this out of the components' own `useState`.  A panel
 * that unmounts -- which is what switching tabs used to do -- took its
 * document with it, so coming back re-read a device that had not changed.
 * And two cards can want the same document: two halves of one settings
 * page each used to fetch the same endpoint for itself, which is two round
 * trips over the one connection for one answer, and two copies that could
 * disagree.  One entry per key fixes both: the second caller finds the
 * first one's document, or waits on the fetch already in flight.
 *
 * It is module state on purpose.  It belongs to the device, not to the
 * tree, and the program empties it when the device changes.
 */
const cache = new Map(); // key -> { doc, loading, error, promise }
const listeners = new Set();

const EMPTY_ENTRY = { doc: null, loading: false, error: '', promise: null };

function announce() {
  for (const listener of [...listeners]) listener();
}

function entryOf(key) {
  return cache.get(key) || EMPTY_ENTRY;
}

/* Forget everything: another device, another set of answers. */
export function resetPanels() {
  cache.clear();
  announce();
}

/* Replace what a key holds -- for a write whose reply is the new document,
 * so the card that wrote it and the card beside it both move at once. */
export function putPanel(key, doc) {
  cache.set(key, { doc, loading: false, error: '', promise: null });
  announce();
}

/* Read a panel's document once per device.  Returns [doc, loading, error,
 * read], the same tuple every caller destructures.
 *
 * `eager` is what a panel turns off when its read is not something to do
 * on arrival -- a listing fetched from the internet, a test that drives
 * the device -- so the read waits for the button that asks for it and the
 * answer is still kept, across tab switches, like every other panel's. */
export function usePanel(key, load, { eager = true } = {}) {
  const [, bump] = useState(0);

  useEffect(() => {
    const wake = () => bump((n) => n + 1);
    listeners.add(wake);
    return () => {
      listeners.delete(wake);
    };
  }, []);

  const read = () => {
    const running = cache.get(key)?.promise;
    if (running) return running; // someone else is already asking
    const held = entryOf(key).doc;
    const promise = Promise.resolve()
      .then(load)
      .then((doc) => {
        cache.set(key, { doc, loading: false, error: '', promise: null });
        announce();
        return doc;
      })
      .catch((err) => {
        /* Keep whatever was read before: a failed refresh should not blank
         * a card that was showing something a moment ago. */
        cache.set(key, {
          doc: held,
          loading: false,
          error: err.message,
          promise: null,
        });
        announce();
      });
    cache.set(key, { doc: held, loading: true, error: '', promise });
    announce();
    return promise;
  };

  useEffect(() => {
    if (eager && !cache.has(key)) read();
  }, [key]);

  const entry = entryOf(key);
  return [entry.doc, entry.loading, entry.error, read];
}

/* A whole card that could not be read: said once, with the way to try
 * again, rather than a page of em dashes that each shrug.
 *
 * `title` keeps it inside the card it belongs to.  Without one, a panel
 * being read and a panel that failed were both a bare line of grey text
 * where a card should be, so every card arriving changed the shape of the
 * grid under the ones already there. */
export function PanelError({ error, loading, onRetry, title }) {
  if (!error) return null;
  const said = html`<div class="empty">
    ${error}
    <div class="actions centred">
      <button class="btn" disabled=${loading} onClick=${onRetry}>Try again</button>
    </div>
  </div>`;
  return title
    ? html`<${Card} title=${title} badge=${html`<span class="badge bad">unread</span>`}>
        ${said}
      <//>`
    : said;
}

/* A card that is being read.  It holds its place in the grid, and shows
 * the shape of what is coming rather than the word "loading" -- so a page
 * of panels arriving settles into itself instead of jumping. */
export function Loading({ loading, what, title, rows }) {
  if (!loading) return null;
  const bones = html`<div class="skeleton" aria-label=${what || 'Reading...'}>
    ${Array.from({ length: rows || 4 }, (_, i) => html`<span class="bone" key=${i}></span>`)}
  </div>`;
  return title
    ? html`<${Card} title=${title}>${bones}<//>`
    : html`<div class="empty">${what || 'Reading...'}</div>`;
}

/* What a panel shows instead of itself, while it has nothing to show.
 *
 * Twelve panels wrote this pair of early returns out by hand, with the
 * card's title typed three times at each site -- and three of them had
 * drifted into returning `null` on an error, so a card that failed to read
 * simply was not there and nothing said why.  Here it is once: hand it the
 * tuple `usePanel` returned, and render what comes back if anything does.
 *
 *     const state = usePanel('auth', onLoad);
 *     const [doc] = state;
 *     const waiting = panelWait(state, { title: 'Authorization' });
 *     if (waiting !== undefined) return waiting;
 *
 * `undefined` -- not `null` -- is the answer that means "carry on": a card
 * may legitimately want to render nothing, which is what `null` is, and
 * `quiet` is when.  That is the second and third card behind one read: the
 * first of them reports the failure, and one endpoint saying it three
 * times is one endpoint shouting.  They still wait for themselves, though,
 * because a card that renders nothing while a read is in flight is a card
 * the grid does not know is coming -- three panels behind one read put up
 * one skeleton and then landed as three, and everything below them jumped
 * a row.  A skeleton is a card's way of saying it will be here.
 */
export function panelWait(state, { title, what, rows, quiet = false } = {}) {
  const [doc, loading, error, read] = state;
  if (error) {
    if (quiet) return null;
    return html`<${PanelError}
      error=${error}
      loading=${loading}
      onRetry=${read}
      title=${title}
    />`;
  }
  if (!doc) {
    return html`<${Loading} loading what=${what} title=${title} rows=${rows} />`;
  }
  return undefined;
}

/* A select for an enumerated register, offered as the device's own
 * dropdown: value to send, title to show, in table order. */
function EnumSelect({ value, table, onChange, disabled, includeBlank, pending }) {
  const entries = Object.entries(table || {}).map(([v, title]) => ({
    value: String(v),
    title,
  }));
  if (includeBlank) entries.unshift({ value: '', title: '—' });
  return html`<${Select}
    value=${value === null || value === undefined ? '' : String(value)}
    entries=${entries}
    onChange=${onChange}
    disabled=${disabled}
    pending=${pending}
  />`;
}

/* One number, as a small input with its unit, pending-coloured while it
 * differs from what the device holds. */
function NumInput({ value, live, min, max, step, unit, onChange, disabled, pending: held }) {
  const pending = held ?? Number(value) !== Number(live);
  return html`<span class="set num">
    <input
      type="number"
      class=${pending ? 'pending' : ''}
      min=${min}
      max=${max}
      step=${step || 1}
      value=${value ?? ''}
      disabled=${disabled}
      onInput=${(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
    />
    ${unit && html`<span class=${`unit${pending ? ' pending' : ''}`}>${unit}</span>`}
  </span>`;
}

/* One text input, edged in amber while it differs from what the device
 * holds -- the same "this is a draft, nothing has been written" cue a
 * number or a slider gets.  It was computed here and then thrown away, so
 * a typed-over URL looked exactly like the one the device is using. */
function TextInput({ value, live, onChange, disabled, placeholder, width, pending: held }) {
  const pending = held ?? (value ?? '') !== (live ?? '');
  return html`<input
    type="text"
    class=${pending ? 'pending' : ''}
    style=${width ? `width:${width}` : ''}
    value=${value ?? ''}

    placeholder=${placeholder || ''}
    disabled=${disabled}
    onInput=${(e) => onChange(e.target.value)}
  />`;
}

/* --- one setting, as a row -------------------------------------------------
 *
 * Six tabs were each writing the same three shapes out by hand: a `Row`
 * whose value is a ternary on `readOnly`, the reading spelled one way on
 * one side of it and an input on the other.  Which is why the same setting
 * could show "16 A" on one tab and "16" on the next, why a missing value
 * was a hand-typed em dash here and the muted one `Row` draws there, and
 * why every enumerated field repeated the same
 * `e.target.value === '' ? null : Number(...)` decode with its own chance
 * of getting it wrong.  These three say it once.
 */

/* What a select hands back, in the type the device's register wants: the
 * blank option is "unset" in every case, and the rest is the caller's
 * `kind` -- most registers are numbers, a couple are strings, and the
 * measurement source is a boolean the app writes as 0 or 1. */
function decode(kind, raw) {
  if (raw === '') return null;
  if (kind === 'boolean') return raw === '1' || raw === 'true';
  if (kind === 'text') return raw;
  return Number(raw);
}

/* ... and the option that value is, on the way back in.
 *
 * A boolean register is stored as 0 or 1 and named by a table keyed on
 * those, but what the device is written with is a real boolean -- so a
 * draft holds `true`, whose option is "1".  Without this the select fell
 * back to its first option the moment you changed it, which is an enumerated
 * setting silently reading as its own opposite. */
function encode(kind, value) {
  if (value === null || value === undefined) return '';
  if (kind === 'boolean') return value ? '1' : '0';
  return String(value);
}

/* A number the device holds, with its unit.  Read-only, the unit is part
 * of the reading; editable, it is the label beside the input.
 *
 * `pending` says the row holds an edit nobody has applied; left out, it is
 * worked out from `value` against `live`.  Every row below takes it, so a
 * card's changed fields are the same colour whatever kind of field they
 * are -- see `Row`. */
export function NumRow({
  k,
  title,
  readOnly,
  value,
  live,
  unit,
  min,
  max,
  step,
  onChange,
  disabled,
  pending,
  hint,
}) {
  const said =
    value === null || value === undefined ? undefined : `${value}${unit ? ` ${unit}` : ''}`;
  const held = !readOnly && (pending ?? (live !== undefined && Number(value) !== Number(live)));
  /* What it may be set to, on the value: the name's tooltip is for what it
   * is (`hint`). */
  const allowed =
    !readOnly && min !== undefined && min !== null && max !== undefined && max !== null
      ? `allowed ${min}${unit ? ` ${unit}` : ''} to ${max}${unit ? ` ${unit}` : ''}`
      : undefined;
  return html`<${Row}
    k=${k}
    title=${title ?? allowed}
    hint=${hint}
    pending=${held}
    v=${readOnly
      ? said
      : html`<${NumInput}
          value=${value}
          live=${live}
          min=${min}
          max=${max}
          step=${step}
          unit=${unit}
          onChange=${onChange}
          disabled=${disabled}
          pending=${held}
        />`}
  />`;
}

/* An enumerated register, shown as the catalog's word for it and edited as
 * a dropdown. */
export function EnumRow({
  k,
  title,
  readOnly,
  value,
  table,
  kind,
  onChange,
  disabled,
  includeBlank,
  pending,
  hint,
}) {
  const held = !readOnly && Boolean(pending);
  return html`<${Row}
    k=${k}
    title=${title}
    hint=${hint}
    pending=${held}
    v=${readOnly
      ? table?.[encode(kind, value)]
      : html`<${EnumSelect}
          value=${encode(kind, value)}
          table=${table}
          onChange=${(value) => onChange(decode(kind, value))}
          disabled=${disabled}
          includeBlank=${includeBlank}
          pending=${held}
        />`}
  />`;
}

/* A string register -- a URL, a path, a name. */
export function TextRow({
  k,
  title,
  readOnly,
  value,
  live,
  placeholder,
  width,
  onChange,
  disabled,
  pending,
  hint,
}) {
  const held = !readOnly && (pending ?? (live !== undefined && (value ?? '') !== (live ?? '')));
  return html`<${Row}
    k=${k}
    title=${title}
    hint=${hint}
    pending=${held}
    v=${readOnly
      ? value || undefined
      : html`<${TextInput}
          value=${value ?? ''}
          live=${live}
          placeholder=${placeholder}
          width=${width}
          onChange=${onChange}
          disabled=${disabled}
          pending=${held}
        />`}
  />`;
}

/* A row whose value side is a toggle. */
export function ToggleRow({ k, value, onChange, disabled, label, title, hint, pending }) {
  return html`<${Row}
    k=${k}
    title=${title}
    hint=${hint}
    pending=${pending}
    v=${html`<label class=${pending ? 'toggle pending' : 'toggle'}>
      <input
        type="checkbox"
        checked=${Boolean(value)}
        disabled=${disabled}
        onChange=${(e) => onChange(e.target.checked)}
      />
      ${label || (value ? 'on' : 'off')}
    </label>`}
  />`;
}

/* Rows rendered from a backend [label, value] table (a network panel, a health check). */
function Rows({ rows }) {
  if (!rows?.length) return html`<div class="empty">Nothing reported.</div>`;
  return html`<div class="rows">
    ${rows.map((r) => html`<${Row} key=${r.label} k=${r.label} v=${r.value || DASH} />`)}
  </div>`;
}

/* A plain card of rows, for read-only panels.
 *
 * The note is a line *about* the card, so it goes above what it is about
 * and folds -- the same `Help` every other card's explanation uses.  It
 * used to be a paragraph after the last row, which is where a card puts
 * its footnotes: a sentence explaining what the rows are, printed
 * underneath them, read last by anyone who read it at all.  `more` is the
 * rest of it, and without one the note is simply a line of prose. */
export function PanelCard({ title, rows, note, more, badge }) {
  return html`<${Card} title=${title} actions=${badge}>
    ${note && html`<${Help} summary=${note}>${more}<//>`}
    <${Rows} rows=${rows} />
  <//>`;
}
