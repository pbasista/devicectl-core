/* Unsent edits: one store for the whole page, a view of it for each card,
 * and the header's count of everything that is waiting.
 *
 * Each card used to hold its own edits in its own `useState`.  That had
 * three consequences that were each worth fixing and that one store fixes
 * together.  A card that was not on screen -- a tab switched away from, in
 * a program that only builds the tab being looked at -- lost what had been
 * typed into it.  Two cards showing the same setting (a protection on the
 * dashboard's band and in the settings table) each had their own idea of
 * whether it had been changed.  And the header could only ever speak for
 * one page's edits, so an edit on a card with an Apply of its own was
 * invisible from anywhere but that card.
 *
 * So edits live here, in module state, filed by *scope*: the name of the
 * thing a write goes to -- a device's settings table, one endpoint of a
 * charger.  A card asks for a view of its scope, narrowed to the names it
 * shows if it shares the scope with other cards; the view counts, clears
 * and applies only those.  Whoever knows how to send a scope says so once,
 * with `offerWriter`, and from then on the card's tick, the header's Apply
 * and anything else that asks all send it the same way.
 *
 * It belongs to the device, not to the tree: the program calls
 * `resetDrafts` when the device changes, since an edit made for one battery
 * is not an edit for the next.
 */

import { Confirm } from '/core/js/ui.js';
import { html, useEffect, useState } from '/core/vendor/preact-htm.module.js';

const scopes = new Map(); // scope -> { edits, writer }
const listeners = new Set();
let applying = false;
let asking = null; // { jobs, answer } while the plan is on screen

function held(scope) {
  if (!scopes.has(scope)) scopes.set(scope, { edits: {}, writer: null });
  return scopes.get(scope);
}

function announce() {
  for (const listener of [...listeners]) listener();
}

/* A key's field: `cellConWireRes[3]` is an element of `cellConWireRes`, and
 * a card that shows the field shows every element of it. */
function field(key) {
  return key.replace(/\[\d+\]$/, '');
}

function narrow(edits, names) {
  if (!names) return edits;
  return Object.fromEntries(Object.entries(edits).filter(([key]) => names.has(field(key))));
}

/* Drop what was sent, and only if it is still what was sent. */
function settle(scope, sent) {
  const entry = held(scope);
  const next = { ...entry.edits };
  for (const [key, value] of Object.entries(sent)) {
    if (key in next && next[key] === value) delete next[key];
  }
  entry.edits = next;
}

/* Re-render on any change to the store.  Every view calls it; so does
 * anything else that shows a count. */
export function useDrafts() {
  const [, bump] = useState(0);
  useEffect(() => {
    const wake = () => bump((n) => n + 1);
    listeners.add(wake);
    return () => {
      listeners.delete(wake);
    };
  }, []);
}

/* Forget every edit: another device. */
export function resetDrafts() {
  for (const entry of scopes.values()) entry.edits = {};
  asking?.answer(false);
  asking = null;
  announce();
}

/* How a scope is sent.  Called while rendering, by whoever has what it
 * takes -- the program, for a scope several tabs edit; the card itself,
 * where only it does -- and the latest call wins, so `busy` and `disabled`
 * are always the current ones.  It is not a hook and takes nothing down
 * when its caller goes away: a tab that is not on screen still has edits,
 * and the header must still be able to send them.
 *
 *   write(edits)  -> a promise; the edits are dropped when it resolves.
 *   plan(edits)   -> a promise of the changes it would make, each
 *                    `{ name, label, oldText, newText }`: shown, and asked
 *                    about, before `write`.  Left out, `write` goes at once.
 *   title         -> what the scope is called where several are asked
 *                    about together.
 *
 * `write` and `plan` say what went wrong themselves (a toast); a rejection
 * here only stops what was going to follow it. */
export function offerWriter(scope, writer) {
  held(scope).writer = writer;
}

/* One card's edits: the scope's, or those of `names` among them.
 *
 * `get` is the whole point: a field reads through the draft and falls back
 * to what the device last said, so an untouched value keeps following the
 * device between polls while an edited one stays put.  `apply` sends this
 * view's edits the way the scope's writer says to, and `clear` drops them. */
export function useDraft(scope, { names } = {}) {
  useDrafts();
  const only = names ? new Set(names) : null;
  const entry = held(scope);
  const edits = narrow(entry.edits, only);
  const count = Object.keys(edits).length;
  return {
    scope,
    edits,
    count,
    dirty: count > 0,
    get: (key, live) => (key in entry.edits ? entry.edits[key] : live),
    has: (key) => key in entry.edits,
    set: (key, value) => {
      entry.edits = { ...entry.edits, [key]: value };
      announce();
    },
    drop: (key) => {
      if (!(key in entry.edits)) return;
      const next = { ...entry.edits };
      delete next[key];
      entry.edits = next;
      announce();
    },
    clear: () => {
      const keep = only
        ? Object.fromEntries(Object.entries(entry.edits).filter(([key]) => !only.has(field(key))))
        : {};
      entry.edits = keep;
      announce();
    },
    apply: () => applyDrafts([{ scope, names: only }]),
    /* Whether this view has somewhere to go, and whether it can go now --
     * read when the card draws, after the writer has been offered. */
    get writable() {
      return Boolean(held(scope).writer);
    },
    get busy() {
      return applying || Boolean(held(scope).writer?.busy);
    },
    get disabled() {
      return Boolean(held(scope).writer?.disabled);
    },
  };
}

/* Everything waiting, across every scope. */
function pendingCount() {
  let n = 0;
  for (const entry of scopes.values()) n += Object.keys(entry.edits).length;
  return n;
}

/* Send some scopes' edits: plan the ones that plan, ask once about all of
 * it, then write them in turn.  A write that fails stops the rest, and what
 * was not written stays in the draft to be tried again. */
export async function applyDrafts(parts) {
  if (applying) return;
  const jobs = parts
    .map(({ scope, names }) => {
      const entry = held(scope);
      return { scope, writer: entry.writer, edits: narrow(entry.edits, names) };
    })
    .filter((job) => job.writer && !job.writer.disabled && Object.keys(job.edits).length);
  if (!jobs.length) return;
  applying = true;
  announce();
  try {
    if (jobs.some((job) => job.writer.plan)) {
      for (const job of jobs) {
        job.changes = job.writer.plan ? await job.writer.plan(job.edits) : null;
      }
      const yes = await new Promise((answer) => {
        asking = { jobs, answer };
        announce();
      });
      /* A value the device already holds is not an edit, whatever the
       * answer was: the plan has just said so. */
      for (const job of jobs) if (job.changes && !job.changes.length) settle(job.scope, job.edits);
      if (!yes) return;
    }
    for (const job of jobs) {
      if (job.changes && !job.changes.length) continue;
      await job.writer.write(job.edits);
      settle(job.scope, job.edits);
    }
  } catch {
    /* The writer has said what went wrong. */
  } finally {
    applying = false;
    asking = null;
    announce();
  }
}

function applyAll() {
  return applyDrafts([...scopes.keys()].map((scope) => ({ scope })));
}

function discardAll() {
  for (const entry of scopes.values()) entry.edits = {};
  announce();
}

/* --- the header's count ---------------------------------------------------
 *
 * Every card with an edit says so in its own title, with its own tick and
 * cross.  This is the same thing for the whole page, pinned to the top of
 * the window: how many edits are waiting anywhere -- on this tab or on
 * one not being looked at -- a way to drop them all and a way to send them
 * all.  Nothing at all until something has been changed.  See `.page-apply`
 * in core.css for why it moves nothing else in the header. */
export function PageApply() {
  useDrafts();
  const count = pendingCount();
  if (!count) return null;
  const what = `${count} change${count === 1 ? '' : 's'}`;
  const said = `${what} not sent yet`;
  const writable = [...scopes.values()].some(
    (entry) => entry.writer && !entry.writer.disabled && Object.keys(entry.edits).length
  );
  const busy = applying || [...scopes.values()].some((entry) => entry.writer?.busy);
  return html`<div class="page-apply" role="group" aria-label=${said}>
    <span class="dot" aria-hidden="true"></span>
    <span class="state">${said}</span>
    <button class="btn small ghost" disabled=${applying} onClick=${discardAll}>Discard</button>
    <button class="btn small primary" disabled=${busy || !writable} onClick=${applyAll}>
      Apply ${what}
    </button>
  </div>`;
}

/* --- the plan, before anything goes -------------------------------------- */

function planLine(change) {
  const label = change.label && change.label !== change.name ? change.label : null;
  return html`<li key=${change.name}>
    <span>${label || html`<span class="mono">${change.name}</span>`}</span>
    <span class="muted">${change.oldText} →</span>
    <b>${change.newText}</b>
  </li>`;
}

/* A plan, asked about: what each value is and what it will be.  The same
 * dialog whatever made the plan -- a card's tick, the header's Apply, or a
 * program's own button (a chemistry preset) -- so a write looks the same
 * however it was asked for. */
export function PlanDialog({ plan, title, onCancel, onConfirm, note, sections }) {
  const groups = sections || [{ changes: plan }];
  const count = groups.reduce((n, group) => n + (group.changes?.length ?? group.count ?? 0), 0);
  return html`<${Confirm}
    title=${count ? title || `Apply ${count} change${count === 1 ? '' : 's'}?` : 'Nothing to change'}
    danger=${false}
    confirmLabel=${count ? 'Apply' : 'Close'}
    onCancel=${onCancel}
    onConfirm=${count ? onConfirm : onCancel}
    body=${count
      ? html`<div>
          <p class="muted">Values the device already holds are left out.</p>
          ${note || null}
          ${groups.map((group) =>
            group.changes
              ? group.changes.length
                ? html`<div key=${group.title || 'plan'}>
                    ${group.title ? html`<h4 class="plan-head">${group.title}</h4>` : null}
                    <ul class="list">
                      ${group.changes.map(planLine)}
                    </ul>
                  </div>`
                : null
              : html`<div key=${group.title}>
                  <h4 class="plan-head">${group.title}</h4>
                  <p class="muted">
                    ${group.count} change${group.count === 1 ? '' : 's'}, sent as they are.
                  </p>
                </div>`
          )}
        </div>`
      : html`<p class="muted">The device already holds every value you set.</p>`}
  />`;
}

/* Where the question is asked.  Rendered once, at the root of the page: the
 * header that starts most of these draws its own children inside a blurred
 * layer, which a dialog cannot escape from. */
export function DraftDialog() {
  useDrafts();
  if (!asking) return null;
  const { jobs, answer } = asking;
  const titled = jobs.length > 1;
  const sections = jobs.map((job) => ({
    title: titled ? job.writer.title || job.scope : null,
    changes: job.changes,
    count: job.changes ? undefined : Object.keys(job.edits).length,
  }));
  return html`<${PlanDialog}
    sections=${sections}
    onCancel=${() => answer(false)}
    onConfirm=${() => answer(true)}
  />`;
}
