/**
 * tuning.js — the browser's mirror of `src/woodshed/tuning.py`.
 *
 * The front end has no way to import a Python module, so the tuning ladder
 * has to exist twice. It used to exist three times: screens/dashboard.js and
 * screens/capture.js each carried their own `KNOWN_TUNINGS` array, and
 * capture.js's comment said out loud what this file is ("there is no shared
 * JS module for it yet"). Both now import from here, so the mirror is one
 * file and `web/tests/test_tuning_label.mjs` checks it against tuning.py's
 * own source rather than trusting three copies to drift together.
 *
 * What this module is NOT: a second opinion about what a shift means. The
 * numbers here are the same offsets tuning.py defines and the derivation
 * (`pitch_of(setlist) - pitch_of(recording)`) stays server-side, on the one
 * path that writes it down. This file names things for a caption.
 */

/** Semitone offset of each standard tuning's pitch centre from E standard. */
export const TUNINGS = {
  'E standard': 0,
  'Eb standard': -1,
  'D standard': -2,
  'C# standard': -3,
  'C standard': -4,
  'B standard': -5,
};

/**
 * Drop tunings resolve to their parent standard tuning's offset, and they
 * are deliberately a separate family rather than entries in TUNINGS —
 * tuning.py's own comment is the long version: a drop name lowers ONE string,
 * so no single semitone number describes it, and its pitch centre is its
 * parent's. What that buys here is that a drop tuning shifts within its own
 * family: Drop D taken down a semitone is Drop C#, not Eb standard. Both
 * share a pitch centre of −1; only one of them is a tuning you could
 * actually play the part in.
 */
export const DROP_PARENTS = {
  'Drop D': 'E standard',
  'Drop C#': 'Eb standard',
  'Drop C': 'D standard',
  'Drop B': 'C# standard',
};

/** Every name the app offers, in tuning.py's KNOWN_TUNINGS order. */
export const KNOWN_TUNINGS = [...Object.keys(TUNINGS), ...Object.keys(DROP_PARENTS)];

export const MAX_SHIFT = 6;

const FLAT_NOTE_RE = /^([A-G])b(?=\s|$)/;

/**
 * Semitone offset of `name`'s pitch centre from E standard, or `null` when
 * the name is not one this app knows. Python's `pitch_of` raises there; here
 * a `null` is the right answer, because the caller is a caption and an
 * unrecognised tuning is a thing that exists on disk — song.yaml files
 * predate the fixed-choice dropdown, and a header that throws is worse than
 * a header that says less.
 *
 * @param {string} name
 * @returns {number|null}
 */
export function pitchOf(name) {
  const parent = DROP_PARENTS[name];
  if (parent !== undefined) return TUNINGS[parent];
  const offset = TUNINGS[name];
  return offset === undefined ? null : offset;
}

/**
 * The tuning you are in after moving a recording in `name` by `shift`
 * semitones, or `null` when that lands on no tuning this app has a name for.
 *
 * `null` is a real answer and not a failure: the ladder runs E standard down
 * to B standard and stops, so E standard taken UP a semitone has no name
 * here. Inventing "F standard" for it would put a string in front of me that
 * nothing else in the repo — not the dropdown, not tuning.py, not a
 * setlist — would accept back. The caller says the shift instead.
 *
 * @param {string} name
 * @param {number} shift
 * @returns {string|null}
 */
export function shiftedTuning(name, shift) {
  const family = DROP_PARENTS[name] !== undefined ? DROP_PARENTS : TUNINGS;
  const from = pitchOf(name);
  if (from === null) return null;
  const target = from + shift;
  for (const candidate of Object.keys(family)) {
    if (pitchOf(candidate) === target) return candidate;
  }
  return null;
}

/**
 * `name` with a flat note spelled with the unicode flat (U+266D), matching
 * Python's `tuning_label`: "Eb standard" → "E♭ standard". Sharps already read
 * correctly as "#", and a "b" that is not the note letter is left alone.
 *
 * @param {string} name
 * @returns {string}
 */
export function prettyTuning(name) {
  return String(name).replace(FLAT_NOTE_RE, `$1${String.fromCharCode(0x266d)}`);
}

/**
 * The song page's transpose caption: what you will actually hear.
 *
 * At shift 0 that is the recording's own tuning. Otherwise it is the tuning
 * the shift lands on, and — when the shift lands between the names — the
 * shift itself, which is the honest thing to say and the one thing that is
 * always true.
 *
 * @param {string} recordingTuning
 * @param {number} shift
 * @returns {string}
 */
export function playsInLabel(recordingTuning, shift) {
  if (!shift) return `plays in ${prettyTuning(recordingTuning)}`;
  const landed = shiftedTuning(recordingTuning, shift);
  if (landed !== null) return `plays in ${prettyTuning(landed)}`;
  return `shifted ${shift > 0 ? '+' : ''}${shift}`;
}
