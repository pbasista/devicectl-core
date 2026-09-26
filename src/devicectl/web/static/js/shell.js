/* The page around the panels: the theme, the tab strip, and whether the
 * program behind the page is still there.
 *
 * None of this is about a device.  Both programs that use this package had
 * re-typed all of it -- a three-way theme with the same two storage keys,
 * hash routing over a table of tabs, a six-second grace before calling the
 * server gone, a tablist with the same roving tabindex -- and the copies
 * had already begun to disagree about which key the theme is under.
 */

import { get, onUnreachable } from '/core/js/api.js';
import { Dialog, Lines, span } from '/core/js/ui.js';
import { h, html, useEffect, useRef, useState } from '/core/vendor/preact-htm.module.js';

/* What the program calls itself, which is what its storage keys are named
 * after: two of these served from one machine are two origins only by
 * port, and `localStorage` is not scoped by port. */
let program = 'devicectl';

export function configure({ name }) {
  program = name;
}

const themeKey = () => `${program}-theme-mode`;
/* The key an earlier version wrote on every load, so every browser that
 * ever opened the page has "dark" in it and would never see the system
 * default.  Dropped on the way past. */
const oldThemeKey = () => `${program}-theme`;

/* system first: a device checked from a laptop in a bright office and one
 * checked from a phone in a dark garage want different pages, and the
 * machine already knows which. */
const THEMES = ['system', 'light', 'dark'];

const LIGHT_QUERY = '(prefers-color-scheme: light)';

/* The page's theme: the system's, unless this browser has said otherwise.
 *
 * Resolved here rather than in the stylesheet.  The sheets are dark on
 * `:root` and light behind `[data-theme="light"]`, so answering
 * `prefers-color-scheme` in CSS as well would mean keeping the whole light
 * palette written twice; this stamps the attribute the sheets already
 * read.  The page does not run without JavaScript anyway -- there is a
 * `<noscript>` on it saying so.
 */
export function useTheme() {
  const [mode, setMode] = useState(() => {
    const stored = localStorage.getItem(themeKey());
    return THEMES.includes(stored) ? stored : 'system';
  });

  useEffect(() => localStorage.removeItem(oldThemeKey()), []);

  useEffect(() => {
    const media = window.matchMedia(LIGHT_QUERY);
    const stamp = () => {
      document.documentElement.dataset.theme =
        mode === 'system' ? (media.matches ? 'light' : 'dark') : mode;
    };
    stamp();
    if (mode !== 'system') return undefined;
    // Only while following the system is there anything to follow.
    media.addEventListener('change', stamp);
    return () => media.removeEventListener('change', stamp);
  }, [mode]);

  const next = THEMES[(THEMES.indexOf(mode) + 1) % THEMES.length];
  return {
    mode,
    next,
    /* A choice is remembered; the resolved theme is not.  Writing it back
     * on every load is what left every browser pinned to the old default. */
    cycle: () => {
      setMode(next);
      localStorage.setItem(themeKey(), next);
    },
  };
}

/* What all three theme marks share: the live switch's box, its stroke
 * weight and its cap -- so the header's icons are one set. */
const THEME_SVG = {
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

/* The three theme marks, drawn rather than typed.
 *
 * They were the characters U+25D0, U+2600 and U+263E, and a font decides
 * how big a character is: the sun came with its own generous side
 * bearings, the moon was set at cap height beside it, and the half-filled
 * circle standing for "follow the system" was drawn smaller than either.
 * Three buttons of one size holding three marks of three sizes, in a
 * header whose other icon -- the live switch -- is a 15px drawing.  These
 * are that drawing's siblings, at one optical size, with no font in the
 * decision.
 *
 * The system mark is a disc half filled: the two themes in one circle,
 * which says "whichever of them the machine is on" without introducing a
 * third idea for it. */
const THEME_ICON = {
  system: html`<svg ...${THEME_SVG}>
    <circle cx="8" cy="8" r="5.6" />
    <path d="M8 2.4a5.6 5.6 0 0 0 0 11.2z" fill="currentColor" stroke="none" />
  </svg>`,
  light: html`<svg ...${THEME_SVG}>
    <circle cx="8" cy="8" r="3.4" />
    <path
      d="M8 1.1v1.7M8 13.2v1.7M1.1 8h1.7M13.2 8h1.7M3.15 3.15l1.2 1.2M11.65 11.65l1.2 1.2M12.85 3.15l-1.2 1.2M4.35 11.65l-1.2 1.2"
    />
  </svg>`,
  dark: html`<svg ...${THEME_SVG}>
    <path d="M13.4 9.6A5.9 5.9 0 0 1 6.4 2.6a5.9 5.9 0 1 0 7 7z" />
  </svg>`,
};

/* The theme, as one button in the header. */
export function ThemeToggle({ theme }) {
  return html`<button
    class="btn small ghost icon"
    type="button"
    title=${`theme: ${theme.mode} -- click for ${theme.next}`}
    aria-label=${`theme: ${theme.mode}`}
    onClick=${theme.cycle}
  >
    ${THEME_ICON[theme.mode]}
  </button>`;
}

/* --- what the program says about itself ------------------------------------
 *
 * Its name, the version being served, and the project's own URLs -- the
 * page it lives on, its release notes, its licence.  All of it comes from
 * `GET /api/about`, which answers out of the packaging metadata, so no URL
 * is written into the browser half at all: `[project.urls]` in the
 * program's `pyproject.toml` is the one place any of them is typed.
 *
 * One request per page, whatever asks: the answer never changes while the
 * server is up, so the promise is kept and handed to every caller.  A
 * failed one is *not* kept -- a page loaded while the server was still
 * coming up would otherwise have no version and no links for as long as it
 * stayed open.
 */
let pending = null;

function about() {
  if (!pending) {
    pending = get('/api/about').catch((err) => {
      pending = null;
      throw err;
    });
  }
  return pending;
}

export function useAbout() {
  const [doc, setDoc] = useState(null);
  useEffect(() => {
    let alive = true;
    about().then(
      (answer) => alive && setDoc(answer),
      () => {
        /* No version and no links, which is a wordmark that is not
         * clickable.  The page is about a device and is worth drawing
         * without them; `useServerLink` is what says the server has gone. */
      }
    );
    return () => {
      alive = false;
    };
  }, []);
  return doc;
}

/* --- a program's own glyph -------------------------------------------------
 *
 * Each of these programs has one mark: jkctl a battery, alfenctl a bolt.  It
 * is drawn in two places -- in front of the wordmark, and in the browser's
 * tab -- and those two had drifted apart, because they were two separate
 * drawings kept in two languages.  The header's was built out of divs and
 * borders in the program's own stylesheet, or was an emoji the font decided
 * the shape of; the tab's was an SVG typed into a percent-encoded `data:`
 * URI in `index.html`, which is unreadable, unreachable from anything, and
 * was never going to be edited twice.  Nothing could have told you the two
 * disagreed except looking at them, and by the time anyone did they were a
 * battery next to a toolbox.
 *
 * So a mark is declared once, as data: a viewBox and a list of shapes.
 * `Glyph` renders it into the header and `useFavicon` serialises the same
 * list into the `data:` URI the tab wants.  There is one drawing and it
 * cannot drift from itself.
 *
 * A shape is `[tag, attributes]`.  Anything in it set to `currentColor`
 * follows the text in the header, and becomes the tab icon's own colour on
 * the way into the favicon, which is a document of its own with no text to
 * inherit from.
 */

/* The house style every mark and every header icon is drawn in: one box,
 * one stroke weight, one cap.  A mark that opts out of it is a mark that
 * looks like it came from somewhere else. */
const MARK_BOX = '0 0 16 16';

const MARK_STROKE = {
  fill: 'none',
  stroke: 'currentColor',
  'stroke-width': 1.5,
  'stroke-linecap': 'round',
  'stroke-linejoin': 'round',
};

/* The program's mark, in front of its name.
 *
 * The shapes go through `h` rather than through a template, because their
 * tags are data -- `rect` here, `path` there -- and a template with a
 * variable tag in it is a template nothing can check.  `h(tag, attrs)` is
 * what the template would have compiled to anyway. */
export function Glyph({ mark, size = 16 }) {
  return html`<svg
    class="glyph"
    viewBox=${mark.viewBox || MARK_BOX}
    width=${size}
    height=${size}
    ...${MARK_STROKE}
    aria-hidden="true"
    focusable="false"
  >
    ${mark.shapes.map(([tag, attrs], i) => h(tag, { key: i, ...attrs }))}
  </svg>`;
}

/* How much of the tab icon is the tile around the mark rather than the mark.
 * A favicon is drawn at 16px inside browser chrome that crops and rounds it,
 * so the drawing is inset rather than run to the edges. */
const TILE_PAD = 2.2;

/* Attributes, as the text of them, with `currentColor` resolved.  A favicon
 * is a document of its own with no text to inherit a colour from. */
function attrText(attrs, color) {
  return Object.entries(attrs)
    .map(([key, value]) => `${key}="${value === 'currentColor' ? color : value}"`)
    .join(' ');
}

/* The mark as a whole SVG document, on its tile: what a tab icon is.
 *
 * The house style goes on the group and each shape carries only what is its
 * own, which is exactly how `Glyph` hangs it on the `<svg>` above the same
 * shapes.  Two ways of drawing one list of shapes is how this drifted in the
 * first place; this way an attribute that a shape overrides overrides it in
 * both, and a shape that says nothing looks the same in both.
 */
function faviconSvg(mark, { color, tile }) {
  const box = mark.viewBox || MARK_BOX;
  const side = Number(box.split(/\s+/)[3]);
  const scale = (side - 2 * TILE_PAD) / side;
  const inset = `translate(${TILE_PAD} ${TILE_PAD}) scale(${scale.toFixed(4)})`;
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${box}">` +
    `<rect width="${side}" height="${side}" rx="${side * 0.22}" fill="${tile}"/>` +
    `<g transform="${inset}" ${attrText(MARK_STROKE, color)}>` +
    mark.shapes.map(([tag, attrs]) => `<${tag} ${attrText(attrs, color)}/>`).join('') +
    `</g></svg>`
  );
}

/* Put the program's mark in the tab, in the colours its own stylesheet
 * names for it.
 *
 * The colours are read off `:root` rather than passed as hexes, because a
 * colour written in JavaScript is a colour that is not in the palette with
 * the others.  They are their own two tokens and not the theme's `--brand`:
 * the tile is dark under both themes, so the mark on it has to be the shade
 * that reads on a dark tile whichever theme the page itself is wearing.
 *
 * Stamped from here rather than written into `index.html` because that is
 * what makes it the same drawing as the header's.  The page does not run
 * without JavaScript anyway -- there is a `<noscript>` on it saying so.
 */
export function useFavicon(mark, { color = '--tab-mark', tile = '--tab-tile' } = {}) {
  useEffect(() => {
    const token = (name) =>
      name.startsWith('--')
        ? getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888'
        : name;
    let link = document.querySelector('link[rel="icon"]');
    if (!link) {
      link = document.createElement('link');
      link.rel = 'icon';
      document.head.appendChild(link);
    }
    link.type = 'image/svg+xml';
    link.href = `data:image/svg+xml,${encodeURIComponent(
      faviconSvg(mark, { color: token(color), tile: token(tile) })
    )}`;
  }, [mark, color, tile]);
}

/* The wordmark: the program's name, and the version beside it.
 *
 * The name links to the project and the version to the release notes for
 * exactly this build -- which is the question a version number in a header
 * is actually asking, and one nobody can answer by reading the number.
 * Both hrefs come from `about()`; without them the same two pieces of text
 * are drawn, unlinked.
 *
 * `children` is the program's own glyph, in front of the name.
 */
export function Brand({ children }) {
  const doc = useAbout();
  const name = doc?.app || program;
  const home = doc?.links?.homepage || '';
  const releases = doc?.links?.releases || '';
  const mark = html`${children}<span class="name">${name}</span>`;
  return html`<div class="brand">
    ${home
      ? html`<a class="home" href=${home} target="_blank" rel="noreferrer" title=${`${name} on the web`}>
          ${mark}
        </a>`
      : html`<span class="home">${mark}</span>`}
    ${doc?.version
      ? releases
        ? html`<a
            class="ver"
            href=${releases}
            target="_blank"
            rel="noreferrer"
            title=${`release notes for ${name} ${doc.version}`}
          >${doc.version}</a>`
        : html`<span class="ver">${doc.version}</span>`
      : null}
  </div>`;
}

/* Which device this page is about, in the two lines every one of these
 * headers carries: what it is called, and what it is.
 *
 * `primary` is the one string no other device on the bench shares -- a name
 * it was given, or its serial number.  `secondary` is the model and the
 * versions of the hardware and the software that are answering, because
 * what a device will do at all depends on them.
 *
 * With `onPick` the block is itself the way to change device, and there is
 * no separate button.  There had been one, a ghost button reading "change"
 * sitting next to the name -- a second control, with its own hit area and
 * its own word, for an action whose subject was the thing right beside it.
 * The name is what somebody looks at to decide they are on the wrong
 * device, so the name is where they try to click.
 *
 * Nothing is drawn beside it to say so.  There was a chevron, and a
 * chevron is the disclosure mark: it promises a list that drops from the
 * control it is on, and what this opens is a dialog over the middle of the
 * page.  The other conventional mark, a trailing ellipsis, is the one
 * character this particular button must not end in -- the name truncates
 * with an ellipsis of its own, so "BMS 3 on the bench…" would be saying
 * either "there is more of this name" or "this opens something", and the
 * reader cannot tell which.  What says it is a control is that it becomes
 * one under the pointer and under the keyboard's focus, in the same fill
 * and edge as every button on the page.
 *
 * It stays exactly two lines tall.  The header's height is set by this
 * block and by nothing else, so a control that grew by even its own padding
 * would push the tab strip and the whole page down; the padding it needs to
 * show a hover is taken back out as a negative margin, which leaves the
 * outer box the size the two lines always were.  See `.device-id.pick`.
 */
export function DeviceId({ primary, secondary, onPick, title }) {
  const lines = html`<${Lines} primary=${primary} secondary=${secondary} />`;
  if (!onPick) return html`<div class="device-id">${lines}</div>`;
  return html`<button class="device-id pick" type="button" title=${title} onClick=${onPick}>
    ${lines}
  </button>`;
}

/* Which tab the address bar is asking for, and the way to change it.
 *
 * The tab is in the URL so a reload comes back where you were, and so a
 * link to "the logs on this device" is a link somebody can send.  `tabs`
 * is the program's own `[id, label]` table; the first is the default and
 * the fallback for a hash naming nothing.
 *
 * A program whose page is about one of several devices carries that too,
 * as the first segment: `#3/dashboard`.  `prefix` is whatever the program
 * puts there, or null for a page that is about the one device it is
 * connected to.
 *
 * `replaceState` rather than assigning to `location.hash`: walking the tab
 * strip is not navigation, and a Back button that has to be pressed nine
 * times to leave the page is a Back button that does not work.  The
 * `hashchange` listener is still there, because a Back out of the page --
 * or a pasted link -- does fire it.
 */
export function useTabs(tabs, { prefix: initial = null } = {}) {
  const read = () => {
    const raw = (window.location.hash || '').replace(/^#/, '');
    const cut = raw.indexOf('/');
    const [head, rest] = cut === -1 ? [raw, null] : [raw.slice(0, cut), raw.slice(cut + 1)];
    const want = rest === null ? head : rest;
    return {
      prefix: rest === null ? initial : head || null,
      tab: tabs.some(([id]) => id === want) ? want : tabs[0][0],
    };
  };
  const [route, setRoute] = useState(read);

  useEffect(() => {
    const follow = () => setRoute(read());
    window.addEventListener('hashchange', follow);
    return () => window.removeEventListener('hashchange', follow);
  }, [tabs]);

  const show = (id, prefix = route.prefix) => {
    setRoute({ tab: id, prefix });
    const next = prefix === null || prefix === undefined ? `#${id}` : `#${prefix}/${id}`;
    if (window.location.hash !== next) window.history.replaceState(null, '', next);
  };

  return { tab: route.tab, prefix: route.prefix, show, tabs };
}

/* The tab strip, with the keyboard the WAI-ARIA tablist pattern asks for:
 * one stop in the page's tab order, arrows between the tabs, Home and End
 * to the ends, and the focus following the selection. */
export function Tabs({ nav, label }) {
  const onKey = (event, index) => {
    const step = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
    const count = nav.tabs.length;
    const at =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? count - 1
          : step
            ? (index + step + count) % count
            : null;
    if (at === null) return;
    event.preventDefault();
    nav.show(nav.tabs[at][0]);
    document.getElementById(`tab-${nav.tabs[at][0]}`)?.focus();
  };

  return html`<nav class="tabs" role="tablist" aria-label=${label || 'What to look at'}>
    ${nav.tabs.map(
      ([id, text], index) => html`<button
        key=${id}
        id=${`tab-${id}`}
        role="tab"
        type="button"
        aria-selected=${nav.tab === id}
        aria-controls=${`pane-${id}`}
        tabIndex=${nav.tab === id ? 0 : -1}
        onKeyDown=${(event) => onKey(event, index)}
        onClick=${() => nav.show(id)}
      >
        ${text}
      </button>`
    )}
  </nav>`;
}

/* One tab's pane.  Every tab is rendered and all but one is `hidden`, so a
 * panel keeps its scroll position and its half-typed field across a switch
 * -- and so the pane exists for `aria-controls` to point at. */
export function Pane({ id, shown, children }) {
  return html`<div
    class="pane"
    id=${`pane-${id}`}
    role="tabpanel"
    aria-labelledby=${`tab-${id}`}
    hidden=${!shown}
  >
    ${children}
  </div>`;
}

/* How long a dropped event stream may spend trying before the page calls
 * the server gone.
 *
 * EventSource reconnects on its own within a few seconds, so a server
 * being restarted comes back inside this and the page never says anything.
 * Longer than this is a server that is not coming back by itself, which is
 * worth a banner. */
const SERVER_GRACE_MS = 6000;

/* Whether the program behind this page is still there.
 *
 * `connecting` until the stream first opens, then `online`; `reconnecting`
 * the moment anything fails to reach the server, and `offline` if it is
 * still failing when the grace runs out.  A failed fetch counts as well as
 * a dropped stream: a click is often what finds out first, and a page that
 * only listened to the stream would keep taking orders for a device it
 * cannot reach.
 */
export function useServerLink() {
  const [state, setState] = useState('connecting');
  const timer = useRef(null);

  const clear = () => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  };

  const lost = () => {
    setState((was) => (was === 'offline' ? was : 'reconnecting'));
    if (timer.current) return;
    timer.current = setTimeout(() => {
      timer.current = null;
      setState('offline');
    }, SERVER_GRACE_MS);
  };

  const found = () => {
    clear();
    setState('online');
  };

  useEffect(() => onUnreachable(lost), []);
  useEffect(() => clear, []);

  return { state, lost, found, offline: state === 'offline' };
}

/* The class the header wears while the server is in doubt. */
function headerClass(state) {
  if (state === 'offline') return 'top gone';
  if (state === 'reconnecting') return 'top lost';
  return 'top';
}

/* The whole of the page's chrome: what the page is about on one row, the
 * tabs on the next, and the hairline under both.
 *
 * Both programs had written this out, and had written it differently -- one
 * put the tab strip outside the header with a second hairline of its own,
 * one centred the top row on the content column and the other ran it to the
 * window's edges.  Side by side the two pages did not line up at the one
 * place a person looks first.  `children` is what the program hangs on that
 * top row; everything about where the row *is* is here.
 */
export function Header({ state, nav, label, children }) {
  return html`<header class=${headerClass(state)}>
    <div class="top-inner">${children}</div>
    ${nav ? html`<${Tabs} nav=${nav} label=${label} />` : null}
  </header>`;
}

/* The EU flag, drawn inline to the official geometry: twelve upright
 * five-pointed gold stars, each with a circumscribed radius of 1/18 of the
 * flag height, on a circle of radius 1/3 of it, on a 3:2 field of the
 * official blue.  The viewBox is the whole rectangle; the frame and the
 * halo are set in CSS so they follow the theme. */
/* --- who else has this page open ------------------------------------------
 *
 * Both programs put one device behind one connection and let any number of
 * browsers watch it, which makes "something is holding the device" a
 * question with an answer: the tab on the next desk, the phone in the
 * garage, or nobody at all and the page is simply slow.  The server has
 * always been able to say -- `/api/clients` and the `hello` event are in
 * the shared server, and the count is already in every link document -- but
 * only one of the two pages asked, so the other had the endpoint, a
 * function in its API module to call it, and nothing that ever did.
 */

/* The count, as the control that opens the list.  It lives in the menu
 * under the link pill in both programs, because the link is the thing being
 * shared and this is who it is being shared with. */
export function Watching({ clients, onOpen }) {
  const many = clients || 1;
  return html`<button class="btn small ghost" onClick=${onOpen}>
    ${many} ${many === 1 ? 'browser' : 'browsers'} watching
  </button>`;
}

/* Which they are.  `me` is this browser's own id, which only the stream can
 * say: several tabs share one address and one user agent, so nothing the
 * browser knows about itself would tell it from its neighbour. */
export function Watchers({ me, onClose, toast }) {
  const [rows, setRows] = useState(null);

  useEffect(() => {
    get('/api/clients')
      .then((doc) => setRows(doc.clients))
      .catch((err) => {
        toast.error(err.message);
        setRows([]);
      });
  }, []);

  const now = Date.now() / 1000;
  return html`<${Dialog} title="Browsers watching" onClose=${onClose} width=${560}>
    ${rows === null
      ? html`<p class="note flush">Asking the server…</p>`
      : rows.length === 0
        ? html`<div class="empty">No open event streams.</div>`
        : html`<div class="scroller">
            ${rows.map(
              (row) => html`<div class=${`entry${row.id === me ? ' you' : ''}`} key=${row.id}>
                <div class="head">
                  <span class="name">${row.label}</span>
                  ${row.id === me && html`<span class="badge good">this browser</span>`}
                  <span class="act name data">${row.address}${row.port ? `:${row.port}` : ''}</span>
                </div>
                <div class="meta" title=${row.agent}>
                  watching for ${span(Math.max(0, now - (row.since || now)))}
                </div>
              </div>`
            )}
          </div>`}
  <//>`;
}

export function EuFlag() {
  return html`<svg class="eu-flag" viewBox="0 0 810 540" width="30" height="20" aria-hidden="true">
    <defs>
      <path
        id="eu-star"
        fill="#ffcc00"
        d="M0.00,-33.33L7.48,-10.30L31.70,-10.30L12.11,3.93L19.59,26.97L0.00,12.73L-19.59,26.97L-12.11,3.93L-31.70,-10.30L-7.48,-10.30Z"
      />
    </defs>
    <rect x="0" y="0" width="810" height="540" fill="#003399" />
    <use href="#eu-star" x="405" y="90" />
    <use href="#eu-star" x="495" y="114.1" />
    <use href="#eu-star" x="560.9" y="180" />
    <use href="#eu-star" x="585" y="270" />
    <use href="#eu-star" x="560.9" y="360" />
    <use href="#eu-star" x="495" y="425.9" />
    <use href="#eu-star" x="405" y="450" />
    <use href="#eu-star" x="315" y="425.9" />
    <use href="#eu-star" x="249.1" y="360" />
    <use href="#eu-star" x="225" y="270" />
    <use href="#eu-star" x="249.1" y="180" />
    <use href="#eu-star" x="315" y="114.1" />
  </svg>`;
}

/* The licence line every one of these pages carries, flag and all.
 *
 * It goes last inside `.app > .body`, where `core.css` makes it a footer:
 * on a short tab it sits on the bottom edge of the window rather than
 * halfway up it under the last card.
 *
 * The href is the ``License`` entry in the program's own `[project.urls]`,
 * fetched with everything else it says about itself, so neither program
 * writes the URL down.  Without one the sentence and the flag still draw --
 * what this copy is under is true whether or not the text of it is one
 * click away.
 */
export function License() {
  const doc = useAbout();
  const name = doc?.app || program;
  const href = doc?.links?.license || '';
  const title = `The licence this copy of ${name} is under`;
  return html`<footer class="license">
    <${EuFlag} />
    ${href
      ? html`<a href=${href} target="_blank" rel="noreferrer" title=${title}>
          Licensed under the EUPL-1.2
        </a>`
      : html`<span title=${title}>Licensed under the EUPL-1.2</span>`}
  </footer>`;
}
