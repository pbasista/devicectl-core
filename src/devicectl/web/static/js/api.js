/* Talking to the program serving this page: requests, and the event stream.
 *
 * Every write carries the X-UI-Request header, which a cross-origin form
 * cannot set -- cookie authentication on its own would let any other page
 * drive this server through the browser of whoever has it open.  The
 * server refuses a POST without it; that refusal is the whole mechanism,
 * and the header's name is arbitrary beyond being one a simple request may
 * not carry.
 *
 * Two failure modes are told apart deliberately.  A request that reached
 * the server and was refused carries the server's own sentence -- a value
 * out of range, a setting the device will not take, a device that did not
 * answer -- and is shown as it arrived.  A request that never reached the
 * server at all means the program behind this page has gone, and every
 * control on it is about to lie about a device it can no longer see.  That
 * is a different thing, and `offline` on the error says so.
 */

const UI_HEADER = 'X-UI-Request';

/* What the program calls itself, for the one sentence this module writes
 * on its own: "the ... server is not answering".  Set once, from the
 * program's own api.js, before anything is asked for. */
let program = 'the';

export function configure({ name }) {
  program = name;
}

export class ApiError extends Error {
  constructor(message, { status = 0, offline = false, traceable = false } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.offline = offline;
    /* The server says the device -- or the program, while talking to it --
     * is what failed, which is the one kind of failure a trace can explain.
     * See js/trace.js. */
    this.traceable = traceable;
  }
}

/* Told when a request could not reach the server at all.
 *
 * The event stream notices too, but only on its own schedule -- a click is
 * often the first thing that finds out, so it says so here rather than
 * raising a toast about "Failed to fetch" and leaving the page looking
 * healthy.  See `useServerLink` in shell.js.
 */
const unreachable = new Set();

export function onUnreachable(fn) {
  unreachable.add(fn);
  return () => unreachable.delete(fn);
}

/* A query string from an object, dropping anything unset -- so an id can
 * be passed as undefined on a page that has not chosen a device yet. */
export function query(params) {
  const parts = Object.entries(params || {})
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
  return parts.length ? `?${parts.join('&')}` : '';
}

/* One request.  `body` is sent as JSON, `raw` as bytes; a GET carries
 * neither and no UI header, because the header is there to make a write
 * impossible to forge and a read is not a write. */
export async function send(method, path, { params, body, raw, signal } = {}) {
  const headers = {};
  let payload;
  if (raw !== undefined) {
    payload = raw;
    headers['Content-Type'] = 'application/octet-stream';
  } else if (body !== undefined) {
    payload = JSON.stringify(body);
    headers['Content-Type'] = 'application/json';
  }
  headers[UI_HEADER] = '1';
  let response;
  try {
    response = await fetch(`${path}${query(params)}`, {
      method,
      headers,
      body: payload,
      signal,
    });
  } catch (err) {
    /* An aborted request is the caller's own doing -- a panel that
     * unmounted, a navigation -- and says nothing about the server. */
    if (err?.name === 'AbortError') throw err;
    for (const fn of [...unreachable]) fn();
    throw new ApiError(`the ${program} server is not answering`, { offline: true });
  }
  const text = await response.text();
  let doc = null;
  if (text) {
    try {
      doc = JSON.parse(text);
    } catch {
      doc = null;
    }
  }
  if (!response.ok) {
    throw new ApiError(doc?.error || text || response.statusText, {
      status: response.status,
      traceable: Boolean(doc?.traceable),
    });
  }
  return doc;
}

export const get = (path, options) => send('GET', path, options);
export const post = (path, body, options) => send('POST', path, { body, ...options });
export const upload = (path, bytes, options) =>
  send('POST', path, { raw: bytes, ...options });

/* A file the server streams, saved wherever the browser puts downloads.
 *
 * Not `send`: what comes back is a backup, an export or a trace report
 * rather than a document to parse, and it is named by the server's own
 * `Content-Disposition` -- the filename carries the station, the unit or
 * the moment, which is what makes a folder of them readable a week later.
 * `fallback` is the name to use when the server did not send one.
 *
 * A failure still arrives as text, and it is the server's sentence, so it
 * is raised as `ApiError` exactly as it would be from any other request.
 */
export async function download(path, { params, fallback = 'download' } = {}) {
  const response = await fetch(`${path}${query(params)}`, {
    headers: { [UI_HEADER]: '1' },
  });
  if (!response.ok) {
    const text = await response.text();
    let doc = null;
    try {
      doc = JSON.parse(text);
    } catch {
      doc = null;
    }
    throw new ApiError(doc?.error || text || response.statusText, {
      status: response.status,
    });
  }
  const named = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') || '');
  const blob = await response.blob();
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = named ? named[1] : fallback;
  link.click();
  URL.revokeObjectURL(link.href);
  return link.download;
}

/* The event stream.  One connection for the whole page: the server
 * multiplexes by event name, and browsers only allow a handful of
 * connections per origin anyway.
 *
 * `handlers` is a map of event name to a function taking the parsed
 * payload, plus the two reserved names `onopen` and `onerror`.  Returns
 * the way to close it.
 */
export function subscribe(path, handlers) {
  let source = null;
  let retry = null;

  const open = () => {
    source = new EventSource(path);
    for (const [name, fn] of Object.entries(handlers)) {
      if (name === 'onopen' || name === 'onerror') continue;
      source.addEventListener(name, (event) => {
        try {
          fn(event.data ? JSON.parse(event.data) : null);
        } catch (err) {
          console.error('bad event payload', name, err);
        }
      });
    }
    source.onopen = () => handlers.onopen?.();
    source.onerror = () => {
      handlers.onerror?.();
      /* EventSource reconnects on its own, but not after the server has
       * gone away for good; a slow explicit retry covers a restart. */
      if (source.readyState === EventSource.CLOSED && !retry) {
        retry = setTimeout(() => {
          retry = null;
          open();
        }, 3000);
      }
    };
  };

  open();
  return () => {
    if (retry) clearTimeout(retry);
    if (source) source.close();
  };
}
