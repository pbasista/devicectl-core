/* What the plots on a page have in common: where the pointer is over one,
 * and the reading that follows it.
 *
 * These charts draw an SVG stretched to the card's width
 * (`preserveAspectRatio: none`), which is what lets them be laid out in
 * CSS and keep their stroke weights -- and which also means nothing drawn
 * inside them keeps its shape.  A circle marking the hovered sample would
 * come out an ellipse, and a differently-shaped ellipse in every card.  So
 * everything that has to stay round or stay thin is HTML positioned over
 * the plot in percentages, and the SVG holds only what is allowed to
 * stretch: the line and the area under it.
 */

import { fmt } from '/core/js/ui.js';
import { html, useRef, useState } from '/core/vendor/preact-htm.module.js';

/* How far from either edge the floating label is allowed to be centred.
 * Past this it stops following the pointer and leans in, so a reading
 * taken at the very start or end of a plot is still inside the card. */
const TIP_EDGE = 0.14;

/* Where the pointer is across a plot, as a fraction of its width.
 *
 * `null` when it is not over one at all, which is most of the time and is
 * what tells every caller to draw none of this.  Touch counts as a
 * pointer: a finger dragged along the line reads it the same way, which
 * on a phone is the only way to read it at all.
 */
export function useHover() {
  const [at, setAt] = useState(null);
  const box = useRef(null);

  const track = (event) => {
    const rect = box.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return;
    const point = event.touches?.[0] || event;
    const x = point.clientX - rect.left;
    setAt(Math.min(1, Math.max(0, x / rect.width)));
  };

  return {
    at,
    box,
    /* Spread onto whatever element owns the plot's width. */
    on: {
      onMouseMove: track,
      onMouseLeave: () => setAt(null),
      onTouchStart: track,
      onTouchMove: track,
      onTouchEnd: () => setAt(null),
      onTouchCancel: () => setAt(null),
    },
  };
}

/* Where the pointer is across the *drawing*, rather than across the box
 * that holds it.
 *
 * A chart that insets what it draws by a few units at each end and
 * then asks `useHover` -- which measures the whole box -- which sample the
 * pointer was over.  So the pointer's 0 was the box's left edge while the
 * first sample sat a pad in from it, and the two disagreed by a pad's
 * worth all the way across: at the left of the chart the marker was to the
 * right of the cursor, at the right of it the marker was to the left, and
 * in the middle they met.  This is the same fraction measured against the
 * region the samples are actually in.
 *
 * Clamped, because the pad itself is outside that region and a pointer in
 * it is still pointing at the nearest end.
 */
export function acrossPlot(at, pad, width) {
  if (at === null || at === undefined) return null;
  const inner = width - 2 * pad;
  if (inner <= 0) return null;
  return Math.min(1, Math.max(0, (at * width - pad) / inner));
}

/* The rule under the pointer, and the reading it is pointing at.
 *
 * `at` is where the pointer is; `x` is where the *sample* is, which is not
 * the same thing -- the rule snaps to the reading it is naming, or the
 * chart would claim a value at a moment nothing was measured.  `y`, when a
 * caller has one, puts a dot on the line itself.
 *
 * `rule` is for a chart that already shows what the pointer is on.  A line
 * has nothing to mark the moment with, so it gets the vertical rule; a bar
 * chart lights up the bar itself, and a rule down the middle of a lit bar
 * is a second answer to a question already answered -- one that, on a wide
 * bar, sits half a bar away from the cursor and reads as a mistake.
 *
 * `below` puts the label under the plot instead of over it.  The rule is
 * the same either way: cover the least.  A plot is a tall rectangle full of
 * the shape being explained, so the label goes above it; a band is a strip
 * eight pixels high with a card of readings above it and its own scale
 * underneath, so the label goes below and covers two numbers that are
 * printed on the label itself.
 */
export function Hovered({ x, y, heading, lines, rule = true, below = false }) {
  const lean = Math.min(1 - TIP_EDGE, Math.max(TIP_EDGE, x));
  return html`<div class="hover" aria-hidden="true">
    ${rule && html`<span class="rule" style=${`left:${(x * 100).toFixed(2)}%`}></span>`}
    ${y !== null &&
    y !== undefined &&
    html`<span
      class="spot"
      style=${`left:${(x * 100).toFixed(2)}%;top:${(y * 100).toFixed(2)}%`}
    ></span>`}
    <span class=${below ? 'tip below' : 'tip'} style=${`left:${(lean * 100).toFixed(2)}%`}>
      <span class="when">${heading}</span>
      ${lines.map((line) => html`<span class="what" key=${line}>${line}</span>`)}
    </span>
  </div>`;
}

/* A clock time, in the reader's own zone: a chart of the last half hour is
 * read against the wall, not against an ISO stamp. */
export function clockTime(seconds) {
  return new Date(seconds * 1000).toLocaleTimeString();
}

/* --- a reading over time -------------------------------------------------
 *
 * The one plot both programs draw: what a number has been doing for as long
 * as the page has been watching it, with the moments something was written
 * marked on it.  It answers the question no single live number can --
 * "I changed something, did anything happen" -- because the device itself
 * keeps no history a browser can ask for.  Both had written it out, and the
 * copies had not aged alike: one grew an area fill, a hover reading, a key
 * and a scale, and the other was still four lines of SVG drawn in colours
 * that no longer existed in either palette, which is a line the browser
 * resolves to black and a chart that appears not to work at all.
 *
 * What is *not* here is where the samples come from.  One program keeps its
 * own in the tab as the charger reports; the other reads a window the
 * server has been recording all along.  That is the half that is genuinely
 * different, and it stays with each of them.
 *
 * A chart may carry a second reading (`also`), drawn in its own colour on
 * its own scale.  Both programs wanted the same second one -- the
 * temperature beside the power -- and both would otherwise have had to
 * spend a whole card on a single flat line.  They cannot share an axis,
 * since a kilowatt and a degree have no common scale, so the key under the
 * plot names each line and prints the range it is drawn against.
 */

/* The drawing's own units.  It is stretched to the card's width, so these
 * are a shape rather than a size -- see the note at the top of this file. */
const W = 600;
const H = 120;
const PAD = 6;

/* The narrowest window the chart will draw.  Without it the first three
 * samples would be stretched across the whole width, and a line whose shape
 * changes because more of it arrived is a line that lies twice. */
const MIN_SPAN_S = 180;

/* The sample nearest a moment, which is what the pointer is really asking
 * for.  A chart of one reading every three seconds has gaps in it -- a
 * paused refresh, a device that took a while to answer -- and a cursor that
 * interpolated across one would be inventing a measurement.  This names a
 * reading that was actually taken. */
function nearest(samples, when) {
  let best = samples[0];
  for (const sample of samples) {
    if (Math.abs(sample.t - when) < Math.abs(best.t - when)) best = sample;
  }
  return best;
}


/* Only the readings that are numbers, oldest first.  A device that did not
 * answer for a field leaves a hole, and a hole drawn as zero is a fault the
 * pack never had. */
function numbers(samples) {
  return (samples || []).filter((s) => typeof s.v === 'number');
}

/* The scale one trace is drawn on.
 *
 * Two rules, because there are two kinds of reading here.  A *flow* -- the
 * power into and out of a pack -- is drawn against zero: which side of the
 * line it is on is half of what it says, and the area is filled from there.
 * A *level* -- a temperature -- has no zero worth drawing: a pack sitting
 * between 18 and 24 degrees on an axis that starts at zero is a flat line
 * in the top quarter of the box, which is a chart of nothing.  So a level
 * gets a scale rounded outwards to the nearest step around what it did,
 * and the scale is printed underneath, because a plot with a floating
 * baseline exaggerates everything on it unless it says so.
 */
function scaleOf(values, { step, floor, zero }) {
  const hi = Math.max(...values);
  const lo = Math.min(...values);
  if (zero) {
    const top = Math.max(floor, Math.ceil(Math.max(hi, 0) / step) * step) || step;
    return { top, bottom: lo < 0 ? -Math.ceil(-lo / step) * step : 0 };
  }
  const top = Math.ceil(hi / step) * step;
  const bottom = Math.floor(lo / step) * step;
  return top > bottom ? { top, bottom } : { top: bottom + step, bottom };
}

/* One trace, worked out: what of it is in the window, the scale it needs,
 * where a value sits on that scale, and how to say one in words.  `null`
 * when there is not enough of it in the window to draw a line at all,
 * which is what a second reading the device stopped answering for does. */
function plotted(spec, x, from) {
  const shown = spec.points.filter((s) => s.t >= from);
  if (shown.length < 2) return null;
  const values = shown.map((s) => s.v);
  const { top, bottom } = scaleOf(values, spec);
  const y = (v) => H - PAD - ((v - bottom) / (top - bottom)) * (H - 2 * PAD);
  return {
    ...spec,
    shown,
    values,
    top,
    bottom,
    y,
    say: (v) => `${fmt(v, spec.digits)}${spec.unit ? ` ${spec.unit}` : ''}`,
    /* A whole-numbered step means a whole-numbered axis: "2 kW full scale"
     * rather than "2.00 kW full scale" under a line read to two places. */
    scaleDigits: Number.isInteger(spec.step) ? 0 : spec.digits,
    line: shown.map((s) => `${x(s.t).toFixed(1)},${y(s.v).toFixed(1)}`).join(' '),
  };
}

/* What a trace's end of the scale says, under the plot. */
function scaleSaid(p) {
  return p.zero && p.bottom >= 0
    ? `${fmt(p.top, p.scaleDigits)} ${p.unit} full scale`
    : `${fmt(p.bottom, p.scaleDigits)}–${fmt(p.top, p.scaleDigits)} ${p.unit}`;
}

/* What the pointer is over, if it is over anything: the reading nearest the
 * moment under it, placed by where that reading actually is rather than by
 * where the pointer is. */
export function Series({
  samples,
  marks = [],
  unit = '',
  digits = 1,
  /* What the full scale is rounded up to.  A ceiling that does not twitch:
   * rounding to a whole kilowatt means the line's height means the same
   * thing between one look and the next, instead of rescaling every time
   * the car takes another 40 W. */
  step = 1,
  /* The smallest full scale.  A pack drawing 40 W on an axis that ends at
   * 40 W is a chart of noise drawn as a mountain range. */
  floor = 0,
  /* A second reading on the same time axis and its own scale: the same
   * `{samples, unit, digits, step}`, plus the `label` the key names it by.
   *
   * On the same picture rather than in a card of its own because the
   * question it answers is a question about the first one -- did that hour
   * of charging warm anything up -- and two charts one above the other are
   * read by remembering the top one.  What keeps them apart is colour and
   * the key under them, since they cannot share an axis: a kilowatt and a
   * degree have no common scale, and pretending otherwise would be the one
   * dishonest thing a chart can do. */
  also = null,
  label = '',
  minSpanS = MIN_SPAN_S,
  empty,
  what = 'the reading',
}) {
  /* Every hook first, and unconditionally: the empty chart below returns
   * before the drawing does, and a hook behind an early return belongs to a
   * different slot on the render that has samples. */
  const hover = useHover();
  const main = numbers(samples);
  const other = also ? numbers(also.samples) : [];
  if (main.length < 2) {
    return html`<div class="chart empty-chart">${empty}</div>`;
  }

  /* The window is the widest either trace needs.  Measured across both so
   * that a second reading the device only started answering for a minute
   * ago does not crop an hour of the first one. */
  const ends = [main[main.length - 1].t, ...(other.length ? [other[other.length - 1].t] : [])];
  const starts = [main[0].t, ...(other.length ? [other[0].t] : [])];
  const now = Math.max(...ends);
  const span = Math.max(minSpanS, now - Math.min(...starts));
  const from = now - span;

  const x = (t) => PAD + ((t - from) / span) * (W - 2 * PAD);
  const first = plotted(
    { points: main, unit, digits, step, floor, zero: true, label: label || what },
    x,
    from
  );
  const second = also
    ? plotted(
        {
          points: other,
          unit: also.unit || '',
          digits: also.digits ?? 1,
          step: also.step ?? 1,
          floor: 0,
          zero: false,
          label: also.label || 'the second reading',
        },
        x,
        from
      )
    : null;
  if (!first) {
    return html`<div class="chart empty-chart">${empty}</div>`;
  }

  const base = first.y(0).toFixed(1);
  const left = x(first.shown[0].t).toFixed(1);
  const area = `${left},${base} ${first.line} ${x(now).toFixed(1)},${base}`;
  const ticks = marks.filter((m) => m.at >= from && m.at <= now);

  const across = acrossPlot(hover.at, PAD, W);
  const when = across === null ? null : from + across * span;
  const under = when === null ? null : nearest(first.shown, when);
  const beside = when === null || !second ? null : nearest(second.shown, when);

  /* The plot is its own box so that the pointer's fraction across it is a
   * fraction across the SVG, and so the cursor and the label -- which are
   * HTML, because nothing drawn inside a stretched SVG keeps its shape --
   * can be positioned in percentages of exactly the same rectangle.  The
   * scale under it is not part of that rectangle. */
  return html`<div class="chart">
    <div class="plot" ref=${hover.box} ...${hover.on}>
      <svg
        viewBox=${`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label=${[
          `${what} over the last ${Math.round(span / 60)} minutes, between ${first.say(
            Math.min(...first.values)
          )} and ${first.say(Math.max(...first.values))}`,
          second &&
            `${second.label} between ${second.say(Math.min(...second.values))} and ${second.say(
              Math.max(...second.values)
            )}`,
        ]
          .filter(Boolean)
          .join('; ')}
      >
        <polygon class="area" points=${area} />
        ${first.bottom < 0 &&
        html`<line
          class="axis"
          x1=${PAD}
          x2=${W - PAD}
          y1=${base}
          y2=${base}
          vector-effect="non-scaling-stroke"
        />`}
        <polyline class="line" points=${first.line} vector-effect="non-scaling-stroke" />
        ${second &&
        html`<polyline
          class="line alt"
          points=${second.line}
          vector-effect="non-scaling-stroke"
        />`}
        ${ticks.map(
          (tick) => html`<line
            class="mark"
            key=${tick.at}
            x1=${x(tick.at)}
            x2=${x(tick.at)}
            y1=${PAD}
            y2=${H - PAD}
            vector-effect="non-scaling-stroke"
          >
            <title>${tick.what}</title>
          </line>`
        )}
      </svg>
      ${under &&
      html`<${Hovered}
        x=${x(under.t) / W}
        y=${first.y(under.v) / H}
        heading=${clockTime(under.t)}
        lines=${[
          second ? `${first.label} ${first.say(under.v)}` : first.say(under.v),
          beside ? `${second.label} ${second.say(beside.v)}` : null,
        ].filter(Boolean)}
      />`}
    </div>
    <div class=${second ? 'ends keyed' : 'ends'}>
      <span>${Math.round(span / 60)} min ago</span>
      ${second
        ? html`<span class="key"><i class="swatch"></i>${first.label}, ${scaleSaid(first)}</span>
            <span class="key"
              ><i class="swatch alt"></i>${second.label}, ${scaleSaid(second)}</span
            >`
        : null}
      ${ticks.length > 0 &&
      html`<span class="key"
        ><i class="tick"></i>${ticks.length} setting${ticks.length === 1 ? '' : 's'} written</span
      >`}
      ${second ? null : html`<span>${scaleSaid(first)}</span>`}
    </div>
  </div>`;
}
