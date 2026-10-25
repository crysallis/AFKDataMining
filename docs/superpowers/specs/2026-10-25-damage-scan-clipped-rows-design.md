# Guild Duel damage scan: ignore clipped rows, read on a settled screen

## Why

The Guild Duel Damage scan (`--guild-duel-damage`) prints a handful of
unmatched names on every run (2026-10-24: `UTNaizab`, `INaizaD`, `Haǎxa`;
2026-10-25: `EoccMpa3eñ`, `TareAimei`, `rarehiiici`, `ψ Kamz/ok`,
`φJ Kailiz/ ok`). Every one checked so far was a duplicate misread of a member
who was ALSO read correctly and saved, matched by identical damage value, so
no data was lost. The cost is a person verifying by hand against the in-game
list and the member table, every scan, to be sure nothing was missed.

Investigation (2026-10-25, lab in `ocr_lab/`, findings in project memory
`project_ocr-name-misread-investigation`) found two measurable causes in how
the scan READS the screen, not in the OCR engine:

1. **Rows that are partly hidden get read anyway.** The viewer's own row is a
   pinned bar that sits at the top of the list (after the scroll passes the
   viewer's rank), at the bottom (before it), or is an ordinary row on screen
   (while the viewer's rank is in view). A row sliding under the bar, or
   running off the list's top/bottom edge, is cut in half. A name cut that way
   reads as junk (`Tarehiiei`, confidence 0.66) in every engine tried, because
   the pixels are not there. A cut-off damage value reads as `455D` (the `B`
   half-hidden); the parser rejected that one by luck, a cut value that happens
   to parse as a number would be saved wrong. A row hidden entirely except its
   tag and damage pairs its damage with the bar's own tag as its "name".
2. **The scan reads while the list is still scrolling.** After the swipe
   returns the list keeps moving about 3.4 s (measured: pixel change 26, 22,
   14, 8, 3.2, 0.19 at 1.0, 1.5, 2.0, 2.4, 2.9, 3.4 s). After the swipe the
   scan sleeps 1.2 s and takes a probe screenshot for its end-of-list check,
   so a stop's first frame lands about 1.7 s after the swipe returns (still
   moving) and the second, after about a second of OCR and a 0.4 s sleep,
   about 3.3 s (nearly settled). Names still read correctly mid motion in the
   test, so this is a timing defect, not a proven cause of the misreads, but
   reading a moving screen is wrong on its face and cheap to fix.

## Goals

- A row is only read when its whole card is fully visible and not overlapped
  by the viewer's own row card, in all three states (own row pinned top,
  pinned bottom, on screen).
- Each scroll stop is read on a settled (not moving) screen.
- When a read still fails to resolve, the scan keeps the screenshot it came
  from so the cause can be seen, not guessed.
- Rank 1 and every row above the viewer's own rank stay readable (the bar is
  not pinned over them).
- No member who is read correctly today stops being read.
- A duplicate misread that still gets through is recognised as a duplicate of
  the member it belongs to (same damage value plus a similar name) instead of
  being listed as unmatched.
- A member whose in-game name is Cyrillic is read in Cyrillic, so the scan
  compares like with like and does not depend on Latin look-alike aliases.

## Non-goals

- Anything in the shared name matching (`RosterResolver`, fuzzy thresholds,
  aliases), with ONE exception: adding the Cyrillic letter range to
  `db.py` `_NAME_CHAR_RE` (see design 5). Nothing else there changes.
- Other scan modes. The shared scroll loop gains an OPT-IN option, off by
  default; every existing caller must behave exactly as before.
- A different OCR engine or model size. Measured and rejected: server models
  were 3 to 4 times slower and no better (one confidently wrong at 0.98),
  upscaling 2x/3x changed nothing.
- Per-hit damage or any flagging (unchanged from the damage-scan design).

## Anti-goals (a failure even if it technically works)

- Silently losing a member: the row filter must not drop a row that no other
  stop reads in full. (Geometry says each row is fully clear at some stop, see
  below, but the scan must prove it afterwards, not just assume it.)
- The filter hiding rank 1 or any row above the viewer, or dropping the
  viewer's own row (it is real data and is read today).
- The settle wait hanging the scan, or adding more than a few minutes in total.
- A failure of the own-row detection stopping the scan. It must fail open
  (skip the overlap check, keep the edge check, log a warning).
- Screenshots with member names ending up in git. The repo is public.

## Measurements the design rests on (1080x1920 capture, 2026-10-25)

- Visible list window: y = 723 to 1733. The pinned bar starts exactly at 723
  (top) or ends exactly at 1733 (bottom).
- Own-row card: one contiguous dark run, about 162 px tall, in the strip
  x = 420 to 760 (median brightness under about 195; every normal row reads
  229 or brighter there, the own row reads about (161,140,131) RGB). Found at
  723-884 (pinned top), 1572-1733 (pinned bottom), 900-1062 (on screen, middle).
  Identical in five frames across the three states.
- Row card around its damage-value centre `dy`: card top = `dy - 104`, card
  bottom = `dy + 58` (checked on rank 1, 7, 8). Card pitch about 186 px.
- Clear window when pinned: about 850 px (723-1572 bottom-pinned, 884-1733
  top-pinned) against a 560 px swipe and a 162 px card, so a row has about
  688 px of positions where it is fully clear, more than one swipe. Every row
  is fully clear at some stop. With the own row on screen the window is the
  full 1010 px.
- Idle list region is perfectly static (consecutive-frame diff 0.0), so any
  diff in the list region is real motion, not avatar animation.

## Design

### 1. Row filter (damage mode only, `modes/guild_duel_damage.py`)

A pure, unit-testable function takes the frame and the parsed rows and returns
the rows that are safe. For each frame:

1. Find the own-row card: the dark run described above, accepted only if its
   height is about 150 to 175 px. If none or several plausible runs are found,
   skip step 3 and log a warning once per scan.
2. Visible window is [723, 1733].
3. A row is kept only if its card (`dy - 104` to `dy + 58`) lies fully inside
   the window, within a small tolerance, AND does not overlap the own-row card.
   The own-row card itself is kept as a row (its top matches the detected run
   within tolerance).

State-free by construction: with the own row on screen nothing overlaps it, so
nothing extra is dropped; pinned top or bottom, rows under or beside the bar
are dropped. The existing guild-tag check, `LIST_Y_MIN` and damage parsing stay
as they are.

The scan counts rows dropped by this filter and logs the total. It also
checks itself at the end: when it saves fewer members than the previous scan
saved for the same week, it prints a warning line (never fails the scan), since
a row dropped as clipped that no other stop read in full would otherwise be
silent.

### 2. Settle before reading (`modes/common.py` `scroll_scan`, opt-in)

New optional argument (default off, so every existing mode is unchanged). When
on, the settle poll REPLACES the fixed `sleep(1.2)` that follows each swipe: it
polls screenshots until two consecutive ones differ by less than about 0.5 in
the list region (idle baseline is 0.0, motion at 3.4 s was 0.19), roughly every
0.3 s, and returns the settled screenshot, which the loop then uses as the
`curr_img` for its existing end-of-list check. That check keeps its own
whole-frame 1.5 threshold exactly as today; the two thresholds coexist because
they answer different questions (did the list move at all, versus has it
stopped). Hard cap about 6 s; if the cap is hit the scan reads anyway and logs
a warning. The damage mode turns it on. Expected cost: about 3.4 s of settling
replaces 1.2 s, so about 2.2 s more per stop, roughly 2.5 minutes over the
70-stop cap the scan reaches today.

With settle on, use `frames_per_stop=1`: two frames of a settled screen are the
same image and OCR is deterministic, so a second read adds nothing but time.
Honest tradeoff: with the row filter a row is fully clear over about 688 px of
positions against a 560 px swipe, so most rows are read once or twice, not
three times, and a single bad read has little to outvote it. Unmatched reads
stay visible (never silently saved wrong, apart from the clipped-damage case
the filter exists to remove), and the swipe distance becomes a named, tunable
constant in `device.guild_duel_damage_scroll_down`; if the checkpoint scan
still shows single-read errors, shortening the swipe (for example 560 to 450
px) buys more overlap at the cost of more stops.

### 3. Keep the frame behind an unmatched read

The damage mode's parse step remembers the screenshot of any observation whose
name does not resolve at read time, capped at about 20 frames per scan to bound
memory. At the end of the scan the frames behind names that are still
unmatched after voting are written as PNGs to a git-ignored folder (my call:
`debug_unmatched/` at the repo root, added to `.gitignore`), named by timestamp
and damage value (never by member name: names contain `/`, Cyrillic and other
characters unsafe for file names). The existing `GUILD_DUEL_DAMAGE: unmatched`
line prints the file name beside the name and value.

### 4. Value tiebreaker for duplicate reads (damage mode only)

A step in `modes/guild_duel_damage.py`, run after `resolve_entries` and before
saving. It never touches the shared voting or `RosterResolver`.

It runs BEFORE `resolve_entries`, so a merged duplicate never reaches that
function's `REVIEW_NAMES` line (the bot's `/scan` turns that line into the
"Name review needed" warning). Flow: resolve the names once with `resolve_names`
(the same resolver `resolve_entries` uses), find duplicates, remove them from
the entry list, print the merge lines, then call `resolve_entries` on what is
left. Resolving twice is harmless (`save_correction` is an idempotent upsert).

For each entry that did NOT resolve to a member, look at the resolved entries
with exactly the same damage value. Score the unresolved read against each of
those members' canonical names with a normalized comparison (case-folded,
look-alike letters folded such as Cyrillic н to h and d to b, accents stripped,
the read also tried as each space/bracket-separated token and with a leading
one or two characters removed as a misread tag glyph, non-letters ignored). Do
NOT fold l or 1 to i: that made `XIII'th` and `Lilith` look 0.91 alike.
Accept the merge only when the best score is at least about 0.7 AND beats the
runner-up by at least about 0.15. On accept, the unresolved entry is dropped as a
duplicate of that member and one line is printed (`merged 'X' into 'Y' (same
311B)`) so it is never silent. No member row is created or changed by a merge,
and a merge never replaces the resolved entry's own value.

Why the floor and margin: damage values are rounded to billions and collide.
On 2026-10-25 `Araleth`, `XIII'th` and `Ramz78k` all showed 166B. Measured
with the folding above: `UTNaizab` and `INaizaD` 1.00 against `NaizaB`,
`TareAimei` 0.89 against `Tare_Ameli`, `EoccMpa3eñ` 0.84 against `БоссМразей`
(all merge); `rarehiiici` 0.44, `ψ Kamz/ok` 0.67 against `Ramz78k` and
`φJ Kailiz/ ok` at best 0.40 against `XIII'th` (all stay unmatched). A floor of
0.5 would have merged `φJ Kailiz/ ok` into the WRONG member, which is why the
floor is 0.7; those weak reads are exactly what the row filter should stop
producing in the first place. If nothing clears the bar the entry stays unmatched exactly as
today, which is the safe default (a person looks), and a genuinely new member
who happens to tie a value is never absorbed without a similar name.

### 5. Cyrillic second pass (damage mode only)

Production reads names with the Chinese/English recognizer, which writes
Cyrillic names as Latin look-alikes (`Hanxa`, `BoccMpa3eñ`). The PP-OCRv5
Cyrillic recognizer (`cyrillic_PP-OCRv5_rec_mobile`, already downloaded on this
machine, about 7.7 MB, downloaded automatically elsewhere) read both real
names correctly in testing (`Найхэ` 0.97, `БоссМразей` 0.95) and reads Latin
names identically, but cannot read CJK and once scored 0.66 on a clean Latin
name.

The pass works on the OCR blocks BEFORE rows are paired (blocks inside the
name x-range and the list window): a block whose crop re-reads as accepted
Cyrillic gets its text replaced, keeping its box and confidence, so pairing,
the row filter and everything after are unchanged. Re-reading a crop took 4 to
90 ms in testing, so running it on every block in the name x-range (guild tags
included) is cheap. For each such block, re-read the crop (text recognition only, no detection)
with the Cyrillic recognizer, lazily initialised so nothing else pays for it
(an additive getter next to the existing engines in `ocr.py`). Replace the
row's name only when the result is mostly Cyrillic letters (about 60 percent or
more of its letters in U+0400 to U+04FF) AND its confidence is at least about
0.8 (measured on name crops: `Найхэ` 0.87, `БоссМразей` 0.95; 0.9 would have
rejected the first). Anything else keeps the production read. It must never override on
confidence alone and never touch a CJK name, since a CJK crop will not come
back mostly Cyrillic.

One shared change, deliberately tiny: add the Cyrillic range to `db.py`
`_NAME_CHAR_RE` so a decorated Cyrillic read (tag glyph plus name) reduces to a
real name core instead of an empty one. Today Cyrillic letters are stripped, so
those reads skip the decoration-stripping tier. It also feeds
`_normalized_roster`, which indexes roster names: the two Cyrillic members
(`Найхэ`, `БоссМразей`) currently have an empty core and are left out of that
index, and after the change they join it for every mode that uses
`RosterResolver`. Accepted as low risk with two such members, and covered by a
regression test over the resolver (Cyrillic names resolve exact and decorated,
every existing Latin and CJK roster name still resolves to itself), not just a
test of the regex.

Cost to measure and report: extra seconds per stop (expected small, see above).

## Verification

- Unit tests: the filter with synthetic frames and rows for all three states
  (own row top, bottom, middle, plus not found), row inside, straddling an
  edge, overlapping the own card, equal to the own card; settle logic with
  fake screenshots (becomes stable, never stabilizes, hits the cap); default
  `scroll_scan` behavior unchanged for the opt-out case (existing
  `test_rank_scan.py` must still pass).
- Replay: the lab (`ocr_lab/`) runs the filter on the saved frames
  (`frame_00` to `frame_08`) and shows that `Tare_Ameli` half-cut is dropped
  while `Matikhv`, `Eudemonios`, `Hira`, rank 1-4 and the own row are kept.
  Frames contain member names, so they live in `ocr_lab/data/frames/`, which is
  git-ignored; the lab scripts themselves (`ocr_lab/*.py`) are committed.
- Live: run a full damage scan. Expect zero duplicates of the kinds seen
  (no `TareAimei`-style fragments), matched count not lower than the prior
  scan's 69, and the scan log shows how many rows were dropped as clipped. The
  saved set of members must match the previous scan's set (no one lost).
- Live: confirm the added scan time is within the few minutes budgeted.
- Tiebreaker tests: a unique value with a similar name merges; the 166B
  three-way tie merges `Kamz/ok`-style reads into the closest name only; a
  similar-name read with a DIFFERENT value does not merge; a read with no
  similar name stays unmatched; a clear-margin failure stays unmatched; the
  merged line is printed; no member row is changed.
- Cyrillic tests (pure accept rule): a Cyrillic read at 0.95 replaces a Latin
  look-alike; a Cyrillic-script read below 0.9 does not; a result with few
  Cyrillic letters (a Latin or CJK name) does not; `_NAME_CHAR_RE` keeps
  Cyrillic letters and still drops tag glyphs such as ψ and φ.
- Replay: the Cyrillic pass on `frame_00` and `frame_03` returns `Найхэ` and
  `БоссМразей`, and leaves `Kit`, `MonkeyAFK` and `谢霆锋` untouched.

## Build order

One plan, staged so a problem is attributable:

1. Row filter (design 1), settle option (2), frame keeping (3). Then a full
   live damage scan and a check against the verification list. This is the
   checkpoint: what is still unmatched afterwards is the real input for stages
   2 and 3, and the saved frames show what it looks like.
2. Value tiebreaker (4).
3. Cyrillic second pass and the `_NAME_CHAR_RE` change (5).

All three stages ship in this work; the checkpoint just means 4 and 5 are
tuned against real leftovers instead of guesses.

Already done, data only (2026-10-25): members 156 and 158 renamed to their real
Cyrillic names `Найхэ` and `БоссМразей` with aliases for the Latin look-alike
reads. Those aliases stay as the fallback for any look-alike read the Cyrillic
pass does not catch.

## Left to the builder

Exact tolerances and thresholds (tuned live against real frames), function and
file names, test structure, how the parse step hands its frames to the end of
the scan, and the poll interval of the settle wait. Keep the measured numbers
above as the starting point, they are not hard-coded requirements.
