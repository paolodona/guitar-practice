/**
 * sections.js (front end) — lane rendering, dragging, creation for the
 * section spans drawn under the waveform. D3's file; signatures fixed
 * here (D0).
 *
 * Not to be confused with src/woodshed/sections.py, the server-side pure
 * module this front end never re-implements: containment and lane
 * assignment are DERIVED SERVER-SIDE (woodshed.sections.assign_lanes /
 * .ancestors — invariant 2, "spans, not tiles": nothing about nesting is
 * stored) and arrive already computed on every section in GET
 * /api/song/<slug>'s `sections` array (`lane`: int, `ancestors`: ids
 * outermost-first — see server.py's _song). This module ONLY draws what
 * that payload says; a drag that changes start_s/end_s is committed with
 * ONE POST /api/section per gesture-end (never per mousemove), and the
 * response's `sections` array — re-validated and re-assigned server-side —
 * is the new ground truth to redraw from, not a locally-patched copy.
 *
 * POST /api/section body shape (server.py's _post_section): {song: slug,
 * id?, action?: "upsert"|"delete", name, start_s, end_s, snapped?, target_
 * speed?, ladder_step?, reps_to_advance?, notes?, patch?, counts_toward_
 * readiness?}. Only `song`+`start_s`+`end_s` are required for a create;
 * `id` omitted means "new section, server mints an id".
 *
 * CROSS-UNIT GAP, flagged rather than silently worked around: server.py's
 * `_post_section` replies with `{sections: [s.model_dump(...) for s in
 * song.sections]}` — the RAW Section models, with no `lane`/`ancestors`
 * merged in (only GET /api/song's `_song` handler computes and adds those
 * two keys). A caller that re-renders straight from a POST /api/section
 * response therefore hands `renderSections` sections with no `lane`. This
 * module degrades rather than throws (a missing `lane` is treated as `0`,
 * and `ancestors` as `[]`), but the honest fix is on the caller's side:
 * re-fetch GET /api/song/<slug> after a commit rather than trust the POST
 * response's `sections` array for redraw. Not this unit's file to fix —
 * reported as a blocker.
 *
 * DESIGN SOURCE: design/SongPage.dc.html's lane stack (two 44px rows,
 * children positioned with percentage left/width, `.sect` tiles 46px
 * tall). Variant colours and the two 5x28px bronze drag handles are lifted
 * from that artboard, not from the plan's summary table alone.
 */

import { viewX, positionAt, snapToGrid } from './timeline.js';

/** px per lane row (design/SongPage.dc.html: "two 44px rows"). */
const LANE_HEIGHT = 44;

/** px, the `.sect` tile's own height — 2px taller than the row it sits in,
 * on purpose (see the artboard: rows are 44px, `.sect{height:46px}`). */
const SECTION_HEIGHT = 46;

/** Smallest gap a drag or a create-gesture is allowed to leave between
 * start_s and end_s. Not in the plan; a defensive floor so a released
 * pointer that hasn't moved can never propose an inverted or zero-length
 * span (the server would refuse it anyway via Section's `_check_span`,
 * but there is no reason to round-trip a POST for that). */
const MIN_DURATION_S = 0.05;

/** Smallest on-screen drag, in CSS px, before a create-gesture counts as
 * a drag rather than a stray click on empty lane space. */
const MIN_CREATE_DRAG_PX = 4;

/** The four lane-tile looks, hex-for-hex from design/SongPage.dc.html's
 * `.sect` variants ("Component details: ordinary #1B2422/#26302E;
 * container #1E2724/#3A4844; selected #2A2118/#E0913F ...; dragging
 * #161E1C with 1px dashed #5B6A64"). Where a value equals a token in
 * web/tokens.css that token is used instead of the bare hex, so a later
 * palette edit there still reaches this file; the two variants with no
 * matching token (container, dragging) are hex literals because that is
 * what the artboard actually specifies for them. */
const VARIANTS = {
  ordinary: { background: 'var(--raised)', border: '1px solid var(--line)' },
  container: { background: '#1E2724', border: '1px solid #3A4844' },
  selected: { background: 'var(--accent-tint)', border: '1px solid var(--accent)' },
  dragging: { background: '#161E1C', border: '1px dashed var(--ink-4)' },
};

/** source seconds -> percent of *view*'s width. A thin wrapper over
 * timeline.js's viewX so every left/width in this file goes through the
 * one shared mapping rather than re-deriving pixel math locally. */
function pct(sourceS, view) {
  return (viewX(sourceS, view) / view.widthPx) * 100;
}

/**
 * @typedef {Object} SectionView
 * @property {string} id
 * @property {string} name
 * @property {number} start_s
 * @property {number} end_s
 * @property {number} lane - 0-based, server-assigned; do not recompute
 * @property {string[]} ancestors - section ids, outermost first
 * @property {boolean} counts_toward_readiness
 * @property {boolean} [full_song] - the whole-song entry: reps count, but
 *   it is excluded from next/prev cycling (practice.js) and from the
 *   readiness bar (practice.py's song_readiness) -- see manifest.
 *   Section.full_song's docstring. Purely a caption cue in this file;
 *   the exclusions themselves live in the two places that actually do
 *   the excluding.
 */

/**
 * Render *sectionsData* (the `sections` array from GET /api/song/<slug>)
 * into *laneRoot*. Each span is positioned with PERCENTAGE left/width
 * against *view* (source seconds -> percent of the lane stack's width),
 * per the plan's per-screen table — unlike the canvas-based waveform,
 * lanes are plain positioned DOM elements so drag handles can be real
 * elements with their own hit-testing.
 *
 * Selection is local UI state owned entirely by this function's closure —
 * the payload carries no "selected" field, so a click just swaps which
 * tile looks selected (and grows drag handles) without touching the
 * network; only a drag-end or a rename reaches out via *handlers*.
 * Re-running renderSections (e.g. after a commit's response comes back)
 * always starts unselected, since the fresh sectionsData has no way to
 * say which id a caller wants re-selected — a caller that wants selection
 * to survive a redraw has to re-select the tile itself. This is an
 * unavoidable consequence of `renderSections`' two-argument-plus-handlers
 * shape carrying no `selectedId` (fixed by D0); flagged, not silently
 * patched around by inventing a new parameter.
 * `grid` (Phase 1, G1) is passed straight through to `attachDragHandlers` —
 * see that function's doc for how it snaps a live drag. `{bars: [], beats:
 * []}` (an empty grid — no tempo, or a caller not yet passing one) means no
 * dragged boundary ever snaps, matching the pre-G1 behaviour exactly.
 * @param {HTMLElement} laneRoot
 * @param {SectionView[]} sectionsData
 * @param {import('./timeline.js').View} view
 * @param {import('./timeline.js').Grid} [grid]
 * @param {{onSelect?: (id: string) => void, onDragCommit?: (patch: object) => void}} [handlers]
 */
export function renderSections(laneRoot, sectionsData, view, grid = { bars: [], beats: [] }, handlers = {}) {
  // Children are positioned with `left`/`top` in absolute terms against
  // this element, so it needs to be a positioning context. Only set it
  // when nothing already has — a caller's own stylesheet (song.js's lane
  // stack wrapper, per the artboard, already declares position:relative)
  // should win over a default this module imposes as a fallback.
  if (!laneRoot.style.position) laneRoot.style.position = 'relative';

  let selectedId = null;
  /** @type {(() => void) | null} */
  let detachDrag = null;

  // Containment is READ from the payload's already-computed `ancestors`
  // list (membership only) — never recomputed from start_s/end_s. A
  // section with no descendants in `ancestors` is "ordinary"; one that
  // some other section's `ancestors` names is a "container".
  const isContainer = (id) =>
    sectionsData.some((other) => other.id !== id && (other.ancestors ?? []).includes(id));
  const childCount = (id) =>
    sectionsData.filter((other) => (other.ancestors ?? []).includes(id)).length;

  function variantOf(section) {
    if (section.id === selectedId) return 'selected';
    if (isContainer(section.id)) return 'container';
    return 'ordinary';
  }

  function buildTile(section) {
    const variant = variantOf(section);
    const look = VARIANTS[variant];

    const el = document.createElement('div');
    el.dataset.sectionId = section.id;
    el.style.position = 'absolute';
    el.style.left = `${pct(section.start_s, view)}%`;
    el.style.width = `${Math.max(0, pct(section.end_s, view) - pct(section.start_s, view))}%`;
    el.style.top = `${(section.lane ?? 0) * LANE_HEIGHT}px`;
    el.style.height = `${SECTION_HEIGHT}px`;
    el.style.borderRadius = '3px';
    el.style.display = 'flex';
    el.style.flexDirection = 'column';
    el.style.justifyContent = 'center';
    el.style.padding = '0 12px';
    // NOT overflow:hidden here, even though the name/caption need clipping
    // (see nm's own overflow/ellipsis below) -- the drag handles below are
    // deliberately positioned outside this element's own box
    // (left:-3px/right:-3px, per the artboard) and a parent overflow:hidden
    // clips a child positioned past its edge regardless of z-index. That
    // silently ate 3 of the handle's 5px on both sides, leaving only a
    // sliver anyone could actually grab -- found live 2026-09-06 as "the
    // start handle doesn't work" (the end handle was equally broken, just
    // less noticed). Text clipping now lives on nm/bt individually instead.
    el.style.cursor = 'pointer';
    el.style.background = look.background;
    el.style.border = look.border;

    const nameColor = variant === 'selected' ? 'var(--on-tint)' : '#C6D2CD';
    const captionColor = variant === 'selected' ? 'var(--on-tint-sub)' : 'var(--ink-4)';

    const nm = document.createElement('div');
    nm.className = 'sect__nm';
    nm.textContent = section.name;
    nm.style.cssText =
      `font-size:13px;font-weight:500;color:${nameColor};white-space:nowrap;` +
      'overflow:hidden;text-overflow:ellipsis';

    const bt = document.createElement('div');
    bt.className = 'sect__bt';
    const duration = section.end_s - section.start_s;
    // No bar/beat grid is reachable from this module in this phase
    // (timeline.js's Grid is populated in Phase 1; Phase 0 callers pass
    // an empty one) — the honest caption is source seconds, not a bar
    // number this file has no way to compute correctly.
    let caption = `${section.start_s.toFixed(1)}s · ${duration.toFixed(1)}s`;
    if (section.full_song) caption += ' · FULL SONG';
    else if (variant === 'container') caption += ` · CONTAINS ${childCount(section.id)}`;
    bt.textContent = caption;
    bt.style.cssText =
      "font-family:'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace;" +
      `font-size:10.5px;color:${captionColor};margin-top:2px;white-space:nowrap;` +
      'overflow:hidden;text-overflow:ellipsis';

    el.append(nm, bt);

    // A click anywhere on the tile selects it; stopPropagation keeps it
    // from also being read by attachCreateHandler's laneRoot listener as
    // "empty space" (which would otherwise start a create-drag on top of
    // an existing section).
    el.addEventListener('pointerdown', (e) => e.stopPropagation());
    el.addEventListener('click', (e) => {
      e.stopPropagation();
      select(section.id);
    });
    // Double-click the name to rename. window.prompt is synchronous and
    // blocks the tab, which is the same trade the rest of this tool makes
    // elsewhere for a single-user, no-framework front end — not worth a
    // custom inline-edit widget for one text field.
    nm.addEventListener('dblclick', (e) => {
      e.stopPropagation();
      const next = window.prompt('Section name', section.name);
      if (next != null && next.trim() && next.trim() !== section.name) {
        // Spread `section` so fields this gesture didn't touch (target_
        // speed, notes, counts_toward_readiness, ...) survive the round
        // trip — _post_section defaults an OMITTED name to the section's
        // id, so a patch that only sent {id, name} would be safe, but one
        // that only sent {id, start_s, end_s} silently renaming to the id
        // is exactly the bug spreading the full section avoids everywhere
        // in this file.
        handlers.onDragCommit?.({ ...section, name: next.trim() });
      }
    });

    return el;
  }

  function paint() {
    if (detachDrag) {
      detachDrag();
      detachDrag = null;
    }
    laneRoot.innerHTML = '';
    for (const section of sectionsData) {
      const el = buildTile(section);
      laneRoot.appendChild(el);
      if (section.id === selectedId) {
        detachDrag = attachDragHandlers(el, section, view, grid, handlers);
      }
    }
  }

  function select(id) {
    if (selectedId === id) return;
    selectedId = id;
    handlers.onSelect?.(id);
    paint();
  }

  paint();
}

/**
 * Attach move/resize drag handles to one already-rendered section element
 * (design system: handles appear only on the selected span, two 5x28px
 * bronze grips at left:-3px/right:-3px). Tracks the drag locally and calls
 * handlers.onDragCommit ONCE, on drag end, with a partial section body
 * (at minimum {id, start_s, end_s}) suitable for POST /api/section —
 * never on every mousemove/pointermove.
 *
 * *sectionEl* is assumed to be a direct child of the lane track (the
 * element pixel math is measured against, via `sectionEl.parentElement`)
 * — true of every tile `renderSections` builds, and the assumption a
 * caller attaching this to some other element structure must preserve.
 * Not fixed explicitly by D0's contract; documented here since getting it
 * wrong fails silently (wrong pixel origin, not a thrown error).
 *
 * `grid` (Phase 1, G1): while `section.snapped` is `'beat'` or `'bar'`, the
 * dragged edge snaps LIVE to the nearest mark in `grid.beats`/`grid.bars`
 * (via timeline.js's `snapToGrid`, the same tolerance `woodshed.sections.
 * snap` uses server-side) — a network round trip per pointermove is not an
 * option, so this mirrors the algorithm rather than calling out to it.
 * `section.snapped === 'free'` (or an empty grid — no tempo yet) never
 * snaps, unchanged from before this parameter existed. The commit at drag
 * end now sends `snapped: section.snapped` (this section's OWN, already-
 * chosen mode) rather than the hardcoded `'free'` a pre-G1 drag always
 * wrote — a beat-snapped section stays recorded as beat-snapped after
 * being dragged, which is the whole point of `docs/02-data-model.md`'s "a
 * section records HOW its boundary was placed".
 * @param {HTMLElement} sectionEl
 * @param {SectionView & {snapped: string}} section
 * @param {import('./timeline.js').View} view
 * @param {import('./timeline.js').Grid} [grid]
 * @param {{onDragCommit?: (patch: object) => void}} [handlers]
 * @returns {() => void} detach
 */
export function attachDragHandlers(sectionEl, section, view, grid = { bars: [], beats: [] }, handlers = {}) {
  const track = sectionEl.parentElement;

  const makeHandle = (side) => {
    const h = document.createElement('div');
    h.className = `sect__handle sect__handle--${side}`;
    // FOUND LIVE 2026-09-06, and it's the real bug behind "only one handle,
    // only the end one moves": `side` is 'start'/'end', not a CSS physical
    // property -- `start:-3px`/`end:-3px` are not valid CSS and the browser
    // silently drops them, so NEITHER handle ever got a left/right position.
    // Both absolutely-positioned handles collapsed onto the same default
    // spot, and since endHandle is appended after startHandle it painted on
    // top and ate every pointerdown regardless of where you clicked. The
    // earlier overflow:hidden fix was real but could not have been
    // sufficient on its own -- there was nothing correctly positioned yet
    // for it to have been clipping.
    const edge = side === 'start' ? 'left' : 'right';
    h.style.cssText =
      'position:absolute;top:9px;width:5px;height:28px;border-radius:2px;' +
      `background:var(--accent);cursor:ew-resize;${edge}:-3px`;
    return h;
  };
  const startHandle = makeHandle('start');
  const endHandle = makeHandle('end');
  sectionEl.append(startHandle, endHandle);

  // Live drag state, independent of `section`'s own fields so the source
  // object handed in never gets mutated mid-gesture.
  const live = { start_s: section.start_s, end_s: section.end_s };

  function applyLivePosition() {
    const left = pct(live.start_s, view);
    sectionEl.style.left = `${left}%`;
    sectionEl.style.width = `${Math.max(0, pct(live.end_s, view) - left)}%`;
  }

  function setDraggingLook(dragging) {
    const look = dragging ? VARIANTS.dragging : VARIANTS.selected;
    sectionEl.style.background = look.background;
    sectionEl.style.border = look.border;
  }

  function beginDrag(side) {
    return (downEvt) => {
      downEvt.stopPropagation();
      downEvt.preventDefault();
      const trackRect = track.getBoundingClientRect();
      downEvt.target.setPointerCapture?.(downEvt.pointerId);
      setDraggingLook(true);

      const onMove = (moveEvt) => {
        const pixelX = moveEvt.clientX - trackRect.left;
        let t = positionAt(pixelX, view);
        if (section.snapped === 'beat' || section.snapped === 'bar') {
          const marks = section.snapped === 'bar' ? grid.bars : grid.beats;
          t = snapToGrid(t, marks, section.snapped);
        }
        if (side === 'start') {
          live.start_s = Math.min(t, live.end_s - MIN_DURATION_S);
        } else {
          live.end_s = Math.max(t, live.start_s + MIN_DURATION_S);
        }
        applyLivePosition();
      };
      const onUp = () => {
        window.removeEventListener('pointermove', onMove);
        setDraggingLook(false);
        // Full section spread (see renderSections' rename handler for why):
        // an edge-only patch must not silently drop name/target_speed/etc.
        handlers.onDragCommit?.({
          ...section,
          start_s: live.start_s,
          end_s: live.end_s,
          snapped: section.snapped,
        });
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp, { once: true });
    };
  }

  startHandle.addEventListener('pointerdown', beginDrag('start'));
  endHandle.addEventListener('pointerdown', beginDrag('end'));

  return () => {
    startHandle.remove();
    endHandle.remove();
  };
}

/**
 * Begin a click-drag-to-create gesture on empty space within *laneRoot*
 * (not on an existing section), ending in one handlers.onCreateCommit call
 * with a fresh section body (no `id` — the server mints one) suitable for
 * POST /api/section with action "upsert".
 *
 * **Found live 2026-09-06**: nothing marked the empty lane strip as an
 * active drop zone — same cursor as everywhere else, no visual difference
 * from dead space, so there was no way to tell "drag here" from "this is
 * just a gap". `laneRoot` now gets `cursor: crosshair` for as long as this
 * handler is attached (a section tile's own `cursor: pointer`, set in
 * `buildTile`, wins over this by CSS specificity — an inline style on a
 * more specific element beats one inherited from its parent) plus a faint
 * tint on pointerenter/leave, the same accent colour family the
 * design system already uses for a hover state elsewhere.
 * @param {HTMLElement} laneRoot
 * @param {import('./timeline.js').View} view
 * @param {{onCreateCommit?: (body: object) => void}} [handlers]
 * @returns {() => void} detach
 */
export function attachCreateHandler(laneRoot, view, handlers = {}) {
  laneRoot.style.cursor = 'crosshair';
  const onEnter = () => { laneRoot.style.background = 'rgba(224,145,63,.05)'; };
  const onLeave = () => { laneRoot.style.background = ''; };
  laneRoot.addEventListener('pointerenter', onEnter);
  laneRoot.addEventListener('pointerleave', onLeave);

  const onDown = (downEvt) => {
    // renderSections' tiles call stopPropagation on their own pointerdown,
    // so this only ever fires for a press that started on bare lane
    // space; the target check is a second, cheap guard against the same
    // thing in case a caller mounts sections some other way.
    if (downEvt.target !== laneRoot) return;
    if (downEvt.button !== 0) return;
    downEvt.preventDefault();

    const rect = laneRoot.getBoundingClientRect();
    const anchorX = downEvt.clientX - rect.left;
    const row = Math.max(0, Math.floor((downEvt.clientY - rect.top) / LANE_HEIGHT));

    const ghost = document.createElement('div');
    ghost.style.cssText =
      `position:absolute;top:${row * LANE_HEIGHT}px;height:${SECTION_HEIGHT}px;` +
      'border-radius:3px;pointer-events:none;' +
      `background:${VARIANTS.dragging.background};border:${VARIANTS.dragging.border}`;
    ghost.style.left = `${anchorX}px`;
    ghost.style.width = '0px';
    laneRoot.appendChild(ghost);
    laneRoot.setPointerCapture?.(downEvt.pointerId);

    const onMove = (moveEvt) => {
      const x = moveEvt.clientX - rect.left;
      ghost.style.left = `${Math.min(anchorX, x)}px`;
      ghost.style.width = `${Math.abs(x - anchorX)}px`;
    };
    const onUp = (upEvt) => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      ghost.remove();

      const x = upEvt.clientX - rect.left;
      if (Math.abs(x - anchorX) < MIN_CREATE_DRAG_PX) return; // a click, not a drag

      const a = positionAt(Math.min(anchorX, x), view);
      const b = positionAt(Math.max(anchorX, x), view);
      if (b - a < MIN_DURATION_S) return;

      handlers.onCreateCommit?.({
        name: 'New section',
        start_s: a,
        end_s: b,
        snapped: 'free',
      });
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp, { once: true });
  };

  laneRoot.addEventListener('pointerdown', onDown);
  return () => {
    laneRoot.removeEventListener('pointerdown', onDown);
    laneRoot.removeEventListener('pointerenter', onEnter);
    laneRoot.removeEventListener('pointerleave', onLeave);
    laneRoot.style.cursor = '';
    laneRoot.style.background = '';
  };
}
