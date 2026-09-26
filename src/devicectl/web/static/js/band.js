/* A reading, the limits it has to stay inside, and a way to move them.
 *
 * A number on its own does not say whether anything is wrong: 42 °C is
 * fine under a 60 °C over-temperature protection and a fault under a 40 °C
 * one, and 3.45 V per cell is the middle of the curve on one pack and an
 * over-voltage trip on the next.  The reading and the setpoints only mean
 * something against each other, so they are drawn in one picture rather
 * than left as a row here and two settings on another tab.
 *
 * Both programs had grown one of these and neither could be used by the
 * other: the charger's was a `.tempband` in its own stylesheet that read
 * two named fields, the battery's was a `Band` of inline styles that took
 * three numbers and drew them.  Neither could show more than two limits,
 * and a cell has four -- an over-voltage protection, the voltage it comes
 * back at, and the same pair underneath -- so the battery drew none of
 * them.  This is one widget with as many limits as the device has.
 *
 * Every one of those numbers is written above the track, not only offered
 * on hover.  A picture that says "the pack is comfortably inside its
 * limits" is worth little if reading *which* limits means chasing a
 * pointer across it, and a hover label says nothing at all to a finger on
 * a phone or to somebody reading a screenshot.
 *
 * And the limits are *draggable*.  The numbers are also on a settings tab,
 * in a table, as they have to be -- a number is how you set 3.650 exactly.
 * But somebody deciding where an over-voltage protection belongs is
 * looking at where the cells actually sit, and the picture that shows them
 * that is the place to put the setpoint.  Dragging never writes: it edits
 * the caller's draft, the same draft the table edits, and the card's own
 * Apply is still what sends anything to the device.
 */

import { Hovered } from '/core/js/chart.js';
import { fixed } from '/core/js/ui.js';
import { html, useLayoutEffect, useRef, useState } from '/core/vendor/preact-htm.module.js';

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}

function has(value) {
  return value !== null && value !== undefined && !Number.isNaN(value);
}

/* How many decimals a step implies, so that dragging in steps of 0.001
 * yields 3.451 rather than 3.4510000000000005.  A step written in
 * exponential notation has no decimals to count, and falls back to the
 * decimals the caller is displaying. */
function placesOf(step, digits) {
  const written = String(step);
  if (written.includes('e') || written.includes('E')) return digits;
  return written.split('.')[1]?.length ?? 0;
}

/* One draggable setpoint, or one that is only drawn.
 *
 * `side` is what the limit protects against -- `low` is a floor, `high` a
 * ceiling, anything else a mark that is neither -- and is what decides the
 * calm stretch in the middle: the band where no protection trips runs from
 * the highest floor to the lowest ceiling.  `soft` is for the release
 * point beside a protection, the voltage or temperature the device lets
 * the pack work again at.  A release is not a limit -- the calm band
 * reaches past it to the protection itself -- so it is drawn as a thin
 * tick and left out of that sum.
 */
function Grip({
  handle,
  at,
  low,
  high,
  digits,
  unit,
  movable,
  hold,
  onGrab,
  onKey,
  onShow,
  onHide,
}) {
  const cls = [
    'grip',
    handle.side === 'low' || handle.side === 'high' ? handle.side : 'mark',
    handle.soft ? 'soft' : '',
    has(handle.saved) && handle.value !== handle.saved ? 'pending' : '',
    movable ? '' : 'fixed',
  ]
    .filter(Boolean)
    .join(' ');
  const style = `left:${(at * 100).toFixed(3)}%`;
  if (!movable) {
    return html`<span class=${cls} style=${style} aria-hidden="true"></span>`;
  }
  return html`<button
    type="button"
    class=${cls}
    style=${style}
    role="slider"
    aria-label=${handle.label}
    aria-valuemin=${low}
    aria-valuemax=${high}
    aria-valuenow=${handle.value}
    aria-valuetext=${`${fixed(handle.value, digits)}${unit ? ` ${unit}` : ''}`}
    ref=${hold}
    onPointerDown=${onGrab}
    onKeyDown=${onKey}
    onFocus=${onShow}
    onBlur=${onHide}
  ></button>`;
}

/* --- the numbers, written out above the track ----------------------------
 *
 * Eight setpoints and a reading is nine numbers to fit along one strip,
 * and on a phone the strip is three hundred pixels.  They are laid out in
 * as many rows as it takes for none of them to sit on top of another,
 * nearest the track first, with a hairline from the track up to any number
 * that had to be lifted off it.
 *
 * The width of a label has to be guessed rather than measured: measuring
 * means rendering, reading the boxes back and rendering again, and the
 * second render lands after the first has been painted.  A digit at this
 * size is about seven pixels wide and the guess is deliberately a little
 * generous, because the cost of over-estimating is a row that did not have
 * to be there and the cost of under-estimating is two numbers printed on
 * top of each other.
 */
const CHAR = 7;
const CHROME = 22;
const AIRPX = 8;
const ROW = 15;
/* What the track is taken to be before it has been measured -- one render,
 * on a page that has not been shown yet.  Narrow, so that the first paint
 * errs towards a row too many rather than a collision. */
const UNMEASURED = 280;

/* The width of the track, in pixels, kept current as the window is
 * resized: the same eight numbers need one row across a desk and three
 * across a phone. */
function useWidth(ref) {
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    const read = () => setWidth(node.getBoundingClientRect().width);
    read();
    if (typeof ResizeObserver !== 'function') {
      window.addEventListener('resize', read);
      return () => window.removeEventListener('resize', read);
    }
    const watch = new ResizeObserver(read);
    watch.observe(node);
    return () => watch.disconnect();
  }, []);
  return width;
}

/* Where each label goes: its row, and how far along the track it is
 * written.  A label belongs over its own mark, except at the ends, where
 * being over the mark would mean half of it outside the picture; those are
 * pushed in far enough to be read. */
function lay(clusters, width) {
  const rows = [];
  const marks = [];
  for (const cluster of [...clusters].sort((a, b) => a.at - b.at)) {
    const half = (cluster.text.length * CHAR + CHROME) / 2 / width;
    const x = clamp(cluster.at, Math.min(half, 0.5), Math.max(1 - half, 0.5));
    let row = rows.findIndex((edge) => x - half >= edge);
    if (row < 0) {
      rows.push(0);
      row = rows.length - 1;
    }
    rows[row] = x + half + AIRPX / width;
    marks.push({ ...cluster, x, row });
  }
  return { marks, rows: rows.length };
}

/* Where a fraction of the scale is, across the strip the numbers are laid
 * out over.
 *
 * The setpoints and the needle are placed inside the track, and the track
 * has a one-pixel border: a fraction of it is a fraction of the width less
 * two pixels, starting one pixel in.  The numbers above it were placed on
 * the whole width, so a hairline dropped from a lifted number landed up to
 * a pixel and a half to the right of the dot it belonged to at the top of
 * the scale -- enough, on a one-pixel line, to be plainly beside the dot
 * rather than on it.  This is the track's own arithmetic, done out here. */
const TRACK_EDGE_PX = 1;

function along(fraction) {
  const px = TRACK_EDGE_PX * (1 - 2 * fraction);
  return `calc(${(fraction * 100).toFixed(3)}% + ${px.toFixed(3)}px)`;
}

function tagClass(mark) {
  return [
    'tag',
    mark.points.some((point) => point.kind === 'read') ? 'read' : '',
    mark.points.some((point) => point.pending) ? 'pending' : '',
    mark.points.length > 1 ? 'many' : '',
  ]
    .filter(Boolean)
    .join(' ');
}

/* The mark beside a number, drawn as whatever it is on the track below:
 * a filled dot for a protection, a hollow one for the release beside it, a
 * grey one for a setpoint that protects against nothing, a bar for the
 * reading.  A label carrying two of them is two things set to the same
 * value, which is otherwise one number over one dot with another dot
 * hidden underneath it. */
function pipClass(point) {
  return ['pip', point.kind, point.soft ? 'soft' : ''].filter(Boolean).join(' ');
}

export function Band({
  min,
  max,
  step = 1,
  digits = 1,
  unit = '',
  now = null,
  nowLabel = 'now',
  spread = null,
  spreadLabel = 'reading',
  handles = [],
  onChange = null,
  label = '',
  caption = null,
  foot = null,
}) {
  const plot = useRef(null);
  /* Which setpoint the pointer has hold of.  A ref rather than state
   * because every move event reads it, and a move that had to wait for a
   * render to know what it was dragging would drop the first few. */
  const dragging = useRef(null);
  /* The grips themselves, by key.  A pointer that takes hold of one hands
   * it the keyboard as well -- see `grab` -- and handing the keyboard to
   * something means having the element to hand it to. */
  const grips = useRef(new Map());
  // Which setpoint to name in the label: the one being dragged, or the one
  // the keyboard is on.  Both are "this is the one you are working with".
  const [held, setHeld] = useState(null);
  const [pointer, setPointer] = useState(null);
  const width = useWidth(plot);

  const size = max - min;
  const place = (value) => (size > 0 ? clamp((value - min) / size, 0, 1) : 0);
  const num = (value) => fixed(value, digits);
  const say = (value) => `${num(value)}${unit ? ` ${unit}` : ''}`;
  /* How coarse one setpoint is, which is not always how coarse the band
   * is.  A band is drawn to one scale and its setpoints need not share a
   * resolution: a JK board keeps its cell voltages to a millivolt and the
   * voltage balancing starts at to ten, on the same volts.  Move the
   * coarse one by the fine one's step and the value it is written back at
   * rounds the move straight back out -- a keypress that does nothing at
   * all, on the setpoint least able to be dragged to where it belongs. */
  const stepOf = (handle) => (has(handle.step) && handle.step > 0 ? handle.step : step);
  const grain = (handle) => fixed(stepOf(handle), placesOf(stepOf(handle), digits));

  const set = handles.filter((h) => has(h.value));
  const editable = (handle) => Boolean(onChange) && !handle.fixed;
  const floors = set.filter((h) => h.side === 'low' && !h.soft).map((h) => h.value);
  const ceilings = set.filter((h) => h.side === 'high' && !h.soft).map((h) => h.value);
  /* The calm stretch is only drawn where there is something to be outside
   * of.  A card of voltages the pack is *meant* to reach -- where a charge
   * stops, what counts as full -- has no floor and no ceiling, and filling
   * its whole track green would say the opposite of what it means: that
   * every value on the scale is fine, on a scale drawn precisely because
   * some of them are not. */
  const guarded = floors.length > 0 || ceilings.length > 0;
  const safeFrom = floors.length ? Math.max(...floors) : min;
  const safeTo = ceilings.length ? Math.min(...ceilings) : max;
  const under = has(now) && floors.length > 0 && now < safeFrom;
  const over = has(now) && ceilings.length > 0 && now > safeTo;
  const ranged = Boolean(spread) && has(spread?.low) && has(spread?.high);

  /* Everything that has a place on the track: every setpoint, and the
   * reading -- one value on a pack, a stretch on its cells, because
   * sixteen cells are not one voltage and their average hides the one that
   * is running away. */
  const points = set.map((handle) => ({
    key: handle.key,
    value: handle.value,
    text: num(handle.value),
    kind: handle.side === 'low' || handle.side === 'high' ? handle.side : 'mark',
    soft: Boolean(handle.soft),
    pending: has(handle.saved) && handle.value !== handle.saved,
    heading: handle.label,
    lines: [
      say(handle.value),
      has(handle.saved) && handle.value !== handle.saved ? `was ${say(handle.saved)}` : null,
      handle.note || null,
    ].filter(Boolean),
  }));
  if (has(now)) {
    points.push({
      key: '.now',
      value: now,
      text: num(now),
      kind: 'read',
      soft: false,
      pending: false,
      heading: nowLabel,
      lines: [say(now), under ? 'below the band' : over ? 'above the band' : null].filter(Boolean),
    });
  }
  if (ranged) {
    points.push({
      key: '.spread',
      value: (spread.low + spread.high) / 2,
      text: `${num(spread.low)} … ${num(spread.high)}`,
      kind: 'read',
      soft: false,
      pending: false,
      heading: spreadLabel,
      lines: [`${say(spread.low)} … ${say(spread.high)}`],
    });
  }

  /* Two setpoints holding the same value are two dots drawn on top of one
   * another under a single number, which reads as one setpoint unless it
   * says otherwise -- and "the release is still where the protection is"
   * is exactly the thing somebody setting these needs to see.  So numbers
   * that agree are written once, with a mark for each of the things at
   * that value. */
  const clusters = [];
  const byText = new Map();
  for (const point of points) {
    const found = byText.get(point.text);
    if (found) {
      found.points.push(point);
      continue;
    }
    const made = { key: point.key, text: point.text, at: place(point.value), points: [point] };
    byText.set(point.text, made);
    clusters.push(made);
  }

  /* What the pointer can be told about, each at its own place on the
   * track.  The label names whichever of them it is nearest, which is what
   * makes hovering the track answer "what is it now" and hovering a
   * setpoint answer "what is this one set to" -- and, where several of
   * them sit together, names all of them rather than whichever of the two
   * happened to be a pixel nearer. */
  const features = clusters.map((cluster) =>
    cluster.points.length === 1
      ? { ...cluster, heading: cluster.points[0].heading, lines: cluster.points[0].lines }
      : {
          ...cluster,
          heading: `${cluster.text}${unit ? ` ${unit}` : ''}`,
          lines: cluster.points.map((point) =>
            point.pending ? `${point.heading} (unsent)` : point.heading
          ),
        }
  );

  const { marks, rows } = lay(clusters, width || UNMEASURED);

  const nearest = (fraction) => {
    let best = null;
    for (const feature of features) {
      const gap = Math.abs(feature.at - fraction);
      if (best === null || gap < best.gap) best = { gap, feature };
    }
    return best?.feature ?? null;
  };

  const fractionOf = (event) => {
    const rect = plot.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return null;
    return clamp((event.clientX - rect.left) / rect.width, 0, 1);
  };

  const floorOf = (handle) => (has(handle.min) ? Math.max(min, handle.min) : min);
  const ceilingOf = (handle) => (has(handle.max) ? Math.min(max, handle.max) : max);

  const snap = (handle, raw) => {
    const by = stepOf(handle);
    const inside = clamp(raw, floorOf(handle), ceilingOf(handle));
    const stepped = clamp(Math.round(inside / by) * by, floorOf(handle), ceilingOf(handle));
    return Number(stepped.toFixed(placesOf(by, digits)));
  };

  const moveTo = (handle, fraction) => {
    const wanted = snap(handle, min + fraction * size);
    if (wanted !== handle.value) onChange(handle.key, wanted);
  };

  /* Anywhere on the track takes hold of the nearest setpoint, not only the
   * dot itself.  A dot is nine pixels wide and a fingertip is not, and the
   * question being asked -- "put the protection about here" -- is answered
   * by where the pointer went down, whichever mark was closest to it. */
  const grab = (event, only) => {
    if (!onChange) return;
    const fraction = fractionOf(event);
    if (fraction === null) return;
    const movable = handles.filter((h) => editable(h) && has(h.value));
    if (!movable.length) return;
    const wanted = only
      ? movable.find((h) => h.key === only)
      : movable.reduce((best, h) =>
          Math.abs(place(h.value) - fraction) < Math.abs(place(best.value) - fraction) ? h : best
        );
    if (!wanted) return;
    event.preventDefault();
    /* A press on a grip runs this twice -- once from the grip, once from
     * the track it bubbles up to -- and the second pass picks the nearest
     * setpoint rather than the one that was pressed.  Those are the same
     * setpoint except where two sit on one value, which is precisely the
     * case somebody is trying to pull apart.  So a press that named its
     * grip keeps it. */
    if (only) event.stopPropagation();
    /* Pressing a grip is how somebody says which setpoint they mean, so it
     * is also how the keyboard is aimed: the grip takes focus, and the
     * arrow keys go on moving the same setpoint after the hand lets go.
     * Dragging puts a setpoint roughly where it belongs and cannot do
     * better than a pixel -- which is 0.02 V on a cell band -- and the
     * last three decimals are then a few taps rather than a trip to the
     * table underneath.  `preventDefault` above is what stops the text
     * under the pointer being selected as it moves; it also stops the
     * button being focused, so focus is given here instead. */
    grips.current.get(wanted.key)?.focus?.({ preventScroll: true });
    dragging.current = wanted.key;
    setHeld(wanted.key);
    setPointer(fraction);
    plot.current?.setPointerCapture?.(event.pointerId);
    moveTo(wanted, fraction);
  };

  const move = (event) => {
    const fraction = fractionOf(event);
    if (fraction === null) return;
    setPointer(fraction);
    const key = dragging.current;
    if (key === null) return;
    const handle = handles.find((h) => h.key === key);
    if (handle) moveTo(handle, fraction);
  };

  const release = (event) => {
    if (dragging.current === null) return;
    plot.current?.releasePointerCapture?.(event.pointerId);
    const key = dragging.current;
    dragging.current = null;
    // Let go of the label only if the grip let go of the keyboard.  The
    // one still holding it is still the one being worked on, and its
    // label is where the step the arrow keys move by is written.
    if (grips.current.get(key) !== document.activeElement) setHeld(null);
  };

  /* The keyboard moves a setpoint too.  A picture that can only be worked
   * with a mouse is a picture that half the settings on the page cannot be
   * reached through, and the arrow keys are what a slider answers to
   * everywhere else on the page. */
  const key = (handle) => (event) => {
    const by =
      {
        ArrowLeft: -1,
        ArrowDown: -1,
        ArrowRight: 1,
        ArrowUp: 1,
        PageDown: -10,
        PageUp: 10,
      }[event.key] ?? 0;
    if (by) {
      event.preventDefault();
      onChange(handle.key, snap(handle, handle.value + by * stepOf(handle)));
      return;
    }
    if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      onChange(handle.key, snap(handle, event.key === 'Home' ? -Infinity : Infinity));
    }
  };

  // The one being worked on wins over the one the pointer happens to be
  // nearest: while a setpoint is being dragged, the label is about it.
  const shown = held
    ? (features.find((f) => f.points.some((point) => point.key === held)) ?? null)
    : pointer === null
      ? null
      : nearest(pointer);
  /* And while one is being worked on, its label says what the keyboard
   * would do to it.  Nothing else on the page can say this: the arrow keys
   * work on any slider anywhere, and a reader who has just dragged one has
   * no reason to suspect that the thing they dragged is also the thing
   * that will take 0.001 V off the value.  Writing the step out is also
   * the only place the page says what the step *is*. */
  const holding = held ? (handles.find((handle) => handle.key === held) ?? null) : null;
  const hint =
    holding && editable(holding)
      ? `arrow keys: ±${grain(holding)}${unit ? ` ${unit}` : ''}`
      : null;
  const reading = has(now)
    ? `${say(now)}, `
    : ranged
      ? `${say(spread.low)} to ${say(spread.high)}, `
      : '';
  /* The numbers above the track are the same numbers as these, so they are
   * written for the reader who cannot see them rather than read out twice
   * over. */
  const spoken = [
    `${label ? `${label}: ` : ''}${reading}between ${say(min)} and ${say(max)}`,
    ...set
      .filter((handle) => !editable(handle))
      .map((handle) => `${handle.label} ${say(handle.value)}`),
  ].join('; ');

  return html`<div class="chart band">
    ${caption ? html`<div class="cap">${caption}</div>` : null}
    ${marks.length
      ? html`<div class="marks" style=${`height:${rows * ROW}px`} aria-hidden="true">
          ${marks
            .filter((mark) => mark.row > 0)
            .map(
              (mark) => html`<span
                class="stem"
                key=${`stem:${mark.key}`}
                style=${`left:${along(mark.at)};height:${mark.row * ROW}px`}
              ></span>`
            )}
          ${marks.map(
            (mark) => html`<span
              class=${tagClass(mark)}
              key=${mark.key}
              style=${`left:${along(mark.x)};bottom:${mark.row * ROW}px`}
              title=${mark.points.map((point) => point.heading).join(' · ')}
            >
              ${mark.points.map(
                (point) => html`<span class=${pipClass(point)} key=${point.key}></span>`
              )}
              ${mark.text}
            </span>`
          )}
        </div>`
      : null}
    <div
      class=${`plot${onChange ? ' settable' : ''}`}
      ref=${plot}
      onPointerDown=${(event) => grab(event, null)}
      onPointerMove=${move}
      onPointerUp=${release}
      onPointerCancel=${release}
      onPointerLeave=${() => dragging.current === null && setPointer(null)}
    >
      <div class="track" role="img" aria-label=${spoken}>
        ${guarded &&
        safeTo > safeFrom &&
        html`<span
          class="safe"
          style=${`left:${(place(safeFrom) * 100).toFixed(3)}%;right:${(100 - place(safeTo) * 100).toFixed(3)}%`}
        ></span>`}
        ${ranged &&
        html`<span
          class="spread"
          style=${`left:${(place(spread.low) * 100).toFixed(3)}%;right:${(100 - place(spread.high) * 100).toFixed(3)}%`}
        ></span>`}
        ${has(now) &&
        html`<span
          class=${`needle${under || over ? ' bad' : ''}`}
          style=${`left:${(place(now) * 100).toFixed(3)}%`}
        ></span>`}
        ${set.map(
          (handle) => html`<${Grip}
            key=${handle.key}
            handle=${handle}
            at=${place(handle.value)}
            low=${floorOf(handle)}
            high=${ceilingOf(handle)}
            digits=${digits}
            unit=${unit}
            movable=${editable(handle)}
            hold=${(node) => {
              if (node) grips.current.set(handle.key, node);
              else grips.current.delete(handle.key);
            }}
            onGrab=${(event) => grab(event, handle.key)}
            onKey=${key(handle)}
            onShow=${() => setHeld(handle.key)}
            onHide=${() => dragging.current === null && setHeld(null)}
          />`
        )}
      </div>
      ${shown &&
      html`<${Hovered}
        x=${shown.at}
        y=${null}
        rule=${false}
        below=${true}
        heading=${shown.heading}
        lines=${hint ? [...shown.lines, hint] : shown.lines}
      />`}
    </div>
    <div class="scale">
      <span>${say(min)}</span>
      ${foot ? html`<span class="mid">${foot}</span>` : null}
      <span>${say(max)}</span>
    </div>
  </div>`;
}

/* How much wider than the numbers on it the track is drawn, when the
 * caller does not say.  It is what a setpoint can be dragged into: room to
 * move, without the scale opening out so far that the setpoints crowd
 * together in the middle of it.  Past the end of it there is the number in
 * the row, which is how an exact value is set in any case. */
const AIR = 0.15;

/* The track the setpoints and the reading are drawn on.
 *
 * The ends are not the device's own limits.  A charger whose alarm is set
 * to 100 °C on a track that stops at 80 would draw that setpoint off the
 * end of its own picture, or -- worse -- pinned to the end, which is a
 * control lying about what it is set to.  So the track always reaches
 * every number it has to show, with a little air either side.
 *
 * What it is measured from matters more than it looks.  Measure it from
 * the values being dragged and the scale moves under the hand doing the
 * dragging: pull a setpoint inward and everything else slides outward to
 * fill the space it left.  Worse, at the very end of the track the value
 * *is* the end, so each move would push the end out and the next move
 * would land further out again -- a setpoint that climbs on its own for as
 * long as the pointer is held against the edge.  So a caller measures this
 * from what the device is holding, and widens the result for anything a
 * draft has put outside it: the ends then stand still while a setpoint is
 * dragged between them, and a value typed into the row beside the picture
 * is still somewhere on it.
 */
export function trackFor(values, { min = null, max = null, pad = null, round = 1 } = {}) {
  const known = values.filter(has);
  const low = Math.min(...(has(min) ? [min] : []), ...known);
  const high = Math.max(...(has(max) ? [max] : []), ...known);
  if (!Number.isFinite(low) || !Number.isFinite(high)) return null;
  const air = has(pad) ? pad : (high - low) * AIR || round;
  return {
    min: Math.floor((low - air) / round) * round,
    max: Math.ceil((high + air) / round) * round,
  };
}
