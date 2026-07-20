# Dream Realm Tier-Aware Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the Dream Realm stats tab so scores are never sorted, charted, or diffed across different difficulty tiers (Hell vs Endless etc.) as if directly comparable.

**Architecture:** Presentation-only fix, entirely inside `DiscordBotAfkJ/stats/src/charts/dreamrealm.js` plus two small CSS additions (missing `--hell`/`--endless` color vars and badge classes). No backend/API change — `tier` and `rank` are already correct in the data returned by `/api/dream-realm`. Tasks are ordered so every task leaves the tab in a fully correct, independently-verifiable state (colors before charts that use them; the single `tierGroups` computation is declared exactly once, in the task that needs it first, and only ever read afterward — never redeclared or relocated by a later task).

**Tech Stack:** Vanilla JS + Vite, Chart.js v4, DaisyUI/Tailwind theme vars.

## Global Constraints

- Spec: `AFKDataMining/docs/superpowers/specs/2026-07-19-dream-realm-tier-aware-report-design.md`
- All file paths in this plan are relative to `C:\vscode\DiscordBotAfkJ\` unless stated otherwise.
- No automated test suite exists for `stats/`. Verification is manual, against real `guild.db` data. This plan's designated test case throughout: **Snow Stomper, 2026-07-15** (confirmed live to have both `hell` and `endless` rows) and **Crysallis on Snow Stomper across scans** (confirmed live: `epic 143B (2026-06-29) → hell 830B (2026-07-07) → endless 179B (2026-07-15)` — a same-boss, cross-scan, tier-changing sequence).
- Tier order, worst to best: `common < hard < epic < hell < endless`. Any tier value outside this set sorts after `endless` (visible, not silently dropped).
- No em dashes in code comments or commit messages · use `·` or `...`.

---

### Task 1: Add `--hell` / `--endless` theme vars, badge classes, and `tierColor()` support

**Files:**
- Modify: `shared/theme.css` (two blocks: the `:root` rarity-color block at lines 9-15, and the `[data-theme="autumn"]` override at lines 40-45)
- Modify: `shared/components.css` (badge classes at lines 120-122)
- Modify: `stats/src/charts/dreamrealm.js` (`tierColor` function, currently lines 154-158)

**Interfaces:**
- Produces: CSS custom properties `--hell` and `--endless` (dark defaults in `:root`, light overrides in `[data-theme="autumn"]`), classes `.tier-hell` / `.tier-endless`, and an extended `tierColor(tier)` that returns a distinct color for all 5 known tiers. Consumed by Task 2 (table badges, already existing markup) and Task 3 (per-tier chart bar coloring).

- [ ] **Step 1: Add the dark-theme vars**

In `shared/theme.css`, change lines 9-15 from:
```css
:root {
  /* Rarity colors — dark defaults; autumn (light theme) keeps these readable */
  --hover-bg: rgba(255,255,255,.03);
  --hard:     #e05c5c;
  --epic:     #9b6bc7;
  --common:   #4a9ec7;
```
to:
```css
:root {
  /* Rarity colors — dark defaults; autumn (light theme) keeps these readable */
  --hover-bg: rgba(255,255,255,.03);
  --hard:     #e05c5c;
  --epic:     #9b6bc7;
  --common:   #4a9ec7;
  --hell:     #d9822b;
  --endless:  #2ecc71;
```

- [ ] **Step 2: Add the light-theme (autumn) overrides**

Change lines 40-45 from:
```css
[data-theme="autumn"] {
  --hover-bg: rgba(0,0,0,.04);
  --hard:     #c0392b;
  --epic:     #7d3c98;
  --common:   #1a5276;
}
```
to:
```css
[data-theme="autumn"] {
  --hover-bg: rgba(0,0,0,.04);
  --hard:     #c0392b;
  --epic:     #7d3c98;
  --common:   #1a5276;
  --hell:     #a8500f;
  --endless:  #1e8449;
}
```

- [ ] **Step 3: Add the badge classes**

In `shared/components.css`, change lines 120-122 from:
```css
  .tier-hard   { background: rgba(224,92,92,.2);  color: var(--hard); }
  .tier-epic   { background: rgba(155,107,199,.2); color: var(--epic); }
  .tier-common { background: rgba(74,158,199,.2);  color: var(--common); }
```
to:
```css
  .tier-hard    { background: rgba(224,92,92,.2);  color: var(--hard); }
  .tier-epic    { background: rgba(155,107,199,.2); color: var(--epic); }
  .tier-common  { background: rgba(74,158,199,.2);  color: var(--common); }
  .tier-hell    { background: rgba(217,130,43,.2);  color: var(--hell); }
  .tier-endless { background: rgba(46,204,113,.2);  color: var(--endless); }
```

- [ ] **Step 4: Extend `tierColor()`**

In `stats/src/charts/dreamrealm.js`, change:
```js
function tierColor(tier) {
    if (tier === 'hard')  return cssVarRgba('--hard',   0.75);
    if (tier === 'epic')  return cssVarRgba('--epic',   0.75);
    return cssVarRgba('--common', 0.75);
}
```
to:
```js
function tierColor(tier) {
    if (tier === 'hard')    return cssVarRgba('--hard',    0.75);
    if (tier === 'epic')    return cssVarRgba('--epic',    0.75);
    if (tier === 'hell')    return cssVarRgba('--hell',    0.75);
    if (tier === 'endless') return cssVarRgba('--endless', 0.75);
    return cssVarRgba('--common', 0.75);
}
```

- [ ] **Step 5: Manual verification**

The current `dreamrealm.js` already renders `<span class="tier-badge tier-${r.tier}">` in its table (line 128) and calls `tierColor(r.tier)` for bar coloring (line 69) — both consume the changes in this task immediately, before any other task touches the file. Open the stats site, go to the Dream Realm tab, select Snow Stomper / 2026-07-15. Confirm:
- The `Tier` column shows Hell rows with an orange badge and Endless rows with a green badge (previously both were blue/`--common`, indistinguishable).
- The existing single bar chart (not yet split by tier — that's Task 3) now colors Hell bars orange and Endless bars green instead of both blue.

- [ ] **Step 6: Commit**

```bash
git add shared/theme.css shared/components.css stats/src/charts/dreamrealm.js
git commit -m "feat(stats): add hell/endless tier colors, badges, and tierColor() support"
```

---

### Task 2: Group table rows by tier, sort by score within tier

**Files:**
- Modify: `stats/src/charts/dreamrealm.js` (rewrite `renderDR`'s row-building and table-building sections)
- Modify: `stats/src/style.css` (add `.tier-group` wrapper CSS)

**Interfaces:**
- Consumes: `allData.scores` rows (unchanged shape: `{ boss_id, boss_name, scan_date, rank, score, tier, member_id, ingame_name }`).
- Produces: `TIER_ORDER` constant, `tierRank(tier)` helper, and `groupByTier(rows)` helper — all three are declared once in this task and read (never redeclared) by Task 3. Produces a **single** `const tierGroups = groupByTier(rows);` declaration placed at the top of `renderDR`, immediately after the existing `prevMap` setup block — Task 3's chart code reads this same variable, it does not declare its own copy.

- [ ] **Step 1: Add the tier-ordering and grouping helpers**

Near the top of `stats/src/charts/dreamrealm.js`, after the existing `let rowsRef = [];` (line 8), add:

```js
const TIER_ORDER = ['common', 'hard', 'epic', 'hell', 'endless'];

function tierRank(tier) {
    const i = TIER_ORDER.indexOf(tier || 'common');
    return i === -1 ? TIER_ORDER.length : i; // unrecognized tiers sort after endless, not silently dropped
}

function groupByTier(rows) {
    const groups = new Map(); // tier -> rows[]
    for (const r of rows) {
        const t = r.tier || 'common';
        if (!groups.has(t)) groups.set(t, []);
        groups.get(t).push(r);
    }
    for (const list of groups.values()) {
        list.sort((a, b) => parseScore(b.score) - parseScore(a.score));
    }
    // Best tier first (descending tier rank) for display order.
    return [...groups.entries()].sort((a, b) => tierRank(b[0]) - tierRank(a[0]));
}
```

Note: `parseScore` is already defined later in the same file — function declarations are hoisted, so this works regardless of file order.

- [ ] **Step 2: Drop the old rank-based sort and add the single `tierGroups` declaration**

Find this block near the top of `renderDR` (originally lines 49-51):
```js
    const rows = allData.scores
        .filter(s => s.boss_id === bossId && s.scan_date === date)
        .sort((a, b) => a.rank - b.rank);
```
Replace with:
```js
    const rows = allData.scores
        .filter(s => s.boss_id === bossId && s.scan_date === date);
```
(Drop the `.sort()` — `groupByTier` now owns all ordering, both tier-group order and within-group score order.)

Then, immediately after the existing `prevMap` building block (the code that builds `prevDate` and `prevMap` from the previous scan date — locate it by searching for `const prevMap = {};` in the current file), add this single line, which Task 3 will also depend on:
```js
    const tierGroups = groupByTier(rows);
```

- [ ] **Step 3: Replace the flat table with per-tier grouped tables**

Replace the existing table-building block in `renderDR` (the code from the `// Table` comment through `document.getElementById('dr-table').innerHTML = html;`) with:

```js
    // Table — grouped by tier, best tier first, sorted by score within each tier
    const hasDelta = Object.keys(prevMap).length > 0;

    let html = '';
    for (const [tier, tierRows] of tierGroups) {
        html += `<div class="tier-group">
            <h3 class="tier-group-heading"><span class="tier-badge tier-${escHtml(tier)}">${escHtml(tier)}</span></h3>
            <table class="data-table"><thead><tr>
                <th>#</th><th>Member</th><th>Score</th>
                ${hasDelta ? '<th>vs prev</th>' : ''}
            </tr></thead><tbody>`;

        for (const r of tierRows) {
            const isMe = r.member_id === me?.memberId;
            const prev = prevMap[r.member_id];
            let delta  = '';
            if (hasDelta) {
                if (prev && prev.tier === r.tier) {
                    const diff = parseScore(r.score) - parseScore(prev.score);
                    if (diff > 0) delta = `<span class="delta-pos">+${fmtScore(diff)}</span>`;
                    else if (diff < 0) delta = `<span class="delta-neg">${fmtScore(diff)}</span>`;
                    else delta = `<span class="delta-neu">--</span>`;
                } else if (prev) {
                    delta = `<span class="delta-neu">tier changed</span>`;
                } else {
                    delta = `<span class="delta-neu">new</span>`;
                }
            }
            html += `<tr${isMe ? ' class="me"' : ''}>
                <td data-label="#">${rankBadge(r.rank)}</td>
                <td data-label="Member">${escHtml(r.ingame_name)}</td>
                <td data-label="Score"><strong>${escHtml(r.score)}</strong></td>
                ${hasDelta ? `<td data-label="Change">${delta}</td>` : ''}
            </tr>`;
        }
        html += '</tbody></table></div>';
    }

    if (prevDate) html += `<div class="scan-note">Comparing to ${prevDate}</div>`;

    document.getElementById('dr-table').innerHTML = html;
```

This drops the standalone `Tier` column (redundant — the group heading already names the tier).

- [ ] **Step 4: Add minimal CSS for the new `.tier-group` wrapper**

In `stats/src/style.css`, add after the existing `.table-box h2` rule (line 162):

```css
.tier-group { margin-bottom: 1.25rem; }
.tier-group:last-child { margin-bottom: 0; }
.tier-group-heading { margin-bottom: .5rem; }
```

- [ ] **Step 5: Manual verification**

Load the Dream Realm tab, select Snow Stomper / 2026-07-15. Confirm:
- Two tier-group tables render: an "endless" group above a "hell" group (best tier first), each with its own colored `.tier-badge` heading (from Task 1).
- Within the endless group, rows are sorted by score descending (200B, 194B, 192B, ... down to 116B).
- Within the hell group, rows are sorted by score descending (2065B, 1881B, ... down to 1228B).
- No standalone `Tier` column inside the per-row table anymore.
- Select a boss/day with only one tier present (most historical scans, before Hell/Endless existed) and confirm only one tier-group table renders, no empty groups.
- Look up Crysallis's Snow Stomper history (epic → hell → endless across three scans): confirm the "vs prev" column shows "tier changed" or "new" rather than a raw numeric diff whenever the tier differs from the previous scan.

- [ ] **Step 6: Commit**

```bash
git add stats/src/charts/dreamrealm.js stats/src/style.css
git commit -m "fix(stats): group Dream Realm table by tier instead of mixing raw scores"
```

---

### Task 3: One bar chart per tier instead of one shared-axis chart

**Files:**
- Modify: `stats/src/charts/dreamrealm.js` (replace the single-chart bar block inside `renderDR`)
- Modify: `stats/src/index.html` (replace the single `<canvas id="chart-dr">` with a container div for dynamically-created per-tier canvases)
- Modify: `stats/src/style.css` (add per-tier chart wrapper CSS)

**Interfaces:**
- Consumes: `tierGroups` (the single variable declared once in Task 2, at the top of `renderDR` — this task only reads it, it does not declare its own copy), `tierColor()` (extended in Task 1, already covers hell/endless by the time this task runs).
- Produces: module-level `chart` variable becomes `chartsByTier: Map<string, Chart>`.

- [ ] **Step 1: Swap the canvas element for a container**

In `stats/src/index.html`, change the Dream Realm section's chart box (originally lines 96-99) from:
```html
        <div class="chart-box">
          <h2>Scores</h2>
          <canvas id="chart-dr"></canvas>
        </div>
```
to:
```html
        <div class="chart-box">
          <h2>Scores</h2>
          <div id="chart-dr-container"></div>
        </div>
```

- [ ] **Step 2: Replace the single-chart module state and rendering**

At the top of `stats/src/charts/dreamrealm.js`, change:
```js
let chart    = null;
```
to:
```js
let chartsByTier = new Map();
```

Replace the entire bar-chart block inside `renderDR` (the code from the `// Bar chart` comment through the closing of the `if (chart) {...} else {...}` block) with:

```js
    // Bar charts — one per tier group, each with its own scale so a max Hell
    // score never visually dwarfs a max Endless score on a shared axis.
    rowsRef = rows;
    const container = document.getElementById('chart-dr-container');

    const seenTiers = new Set(tierGroups.map(([t]) => t));
    for (const tier of [...chartsByTier.keys()]) {
        if (!seenTiers.has(tier)) {
            chartsByTier.get(tier).destroy();
            chartsByTier.delete(tier);
            const el = document.getElementById(`chart-dr-wrap-${tier}`);
            if (el) el.remove();
        }
    }

    for (const [tier, tierRows] of tierGroups) {
        let wrap = document.getElementById(`chart-dr-wrap-${tier}`);
        if (!wrap) {
            wrap = document.createElement('div');
            wrap.id = `chart-dr-wrap-${tier}`;
            wrap.className = 'tier-chart-wrap';
            const heading = document.createElement('div');
            heading.className = 'tier-group-heading';
            heading.innerHTML = `<span class="tier-badge tier-${tier}">${tier}</span>`;
            const canvas = document.createElement('canvas');
            canvas.id = `chart-dr-${tier}`;
            wrap.appendChild(heading);
            wrap.appendChild(canvas);
            container.appendChild(wrap);
        }

        const labels = tierRows.map(r => r.ingame_name);
        const values = tierRows.map(r => parseScore(r.score));
        const colors = tierRows.map(r => r.member_id === me?.memberId ? getCSSVar('--color-primary') : tierColor(tier));

        const existing = chartsByTier.get(tier);
        if (existing) {
            existing.data.labels = labels;
            existing.data.datasets[0].data            = values;
            existing.data.datasets[0].backgroundColor = colors;
            existing.update();
            continue;
        }

        const newChart = new Chart(document.getElementById(`chart-dr-${tier}`), {
            type: 'bar',
            data: {
                labels,
                datasets: [{
                    label: 'Score',
                    data: values,
                    backgroundColor: colors,
                    borderRadius: 3,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                indexAxis: 'y',
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: { label: ctx => ' ' + tierRows[ctx.dataIndex].score } },
                },
                scales: {
                    x: { grid: { color: getCSSVar('--color-base-content') }, ticks: { color: getCSSVar('--color-base-content'), callback: v => fmtScore(v) } },
                    y: { grid: { color: getCSSVar('--color-base-content') }, ticks: { color: getCSSVar('--color-base-content'), font: { size: 11 } } },
                },
            },
        });
        newChart._recolor = () => renderDR(meRef);
        chartsByTier.set(tier, newChart);
    }
```

- [ ] **Step 3: Add CSS for the per-tier chart wrapper**

In `stats/src/style.css`, add alongside the `.tier-group` rules from Task 2:

```css
.tier-chart-wrap { margin-bottom: 1.25rem; }
.tier-chart-wrap:last-child { margin-bottom: 0; }
.tier-chart-wrap canvas { max-height: 240px; }
```

- [ ] **Step 4: Manual verification**

Reload the Dream Realm tab, select Snow Stomper / 2026-07-15. Confirm:
- Two separate bar charts render in the chart box, one labeled "endless" (green badge), one labeled "hell" (orange badge), stacked vertically.
- The endless chart's x-axis max is around 200B, the hell chart's x-axis max is around 2065B — each chart's bars fill its own width proportionally, not squashed by the other tier's scale.
- Bars are colored orange (hell) / green (endless), matching Task 1's `tierColor()` extension.
- Switching to a boss/day with only one tier renders exactly one chart, no empty second chart.
- Switching between dates/bosses repeatedly doesn't leak duplicate canvases (check via browser devtools: the number of `.tier-chart-wrap` elements in `#chart-dr-container` should always equal the number of tiers present for the currently selected boss/day — old tier charts from a previous selection should be gone).

- [ ] **Step 5: Commit**

```bash
git add stats/src/charts/dreamrealm.js stats/src/index.html stats/src/style.css
git commit -m "fix(stats): render one Dream Realm bar chart per tier instead of a shared axis"
```

---

## Self-Review Notes

- **Spec coverage:** `--hell`/`--endless` CSS vars + badge classes + `tierColor()` (Task 1), tier grouping worst-to-best with unrecognized-tier fallback + score-based sort within tier (not the nullable `rank` field) + numeric delta only when tier unchanged / distinct "tier changed" vs "new" labels (Task 2), one mini-chart per tier not shared axis (Task 3). All spec sections covered.
- **Single-declaration fix:** `tierGroups` is declared exactly once, in Task 2 Step 2, at the top of `renderDR`. Task 3 only reads it — it does not declare, move, or duplicate the variable, eliminating the redeclaration/`SyntaxError` risk of an earlier draft of this plan where Task 3 both introduced its own `tierGroups` line and asked the executor to delete Task 2's.
- **Task ordering fix:** color support (`--hell`/`--endless` vars, badge classes, `tierColor()`) now lands in Task 1, before Task 3's per-tier charts consume `tierColor()` — so Task 3 never has an intermediate "correct axis split, wrong color" state, and every task leaves the tab fully correct and independently verifiable.
- **Type consistency:** `groupByTier(rows)` returns `[tier, rows[]][]`, used identically by Task 2 (table) and Task 3 (charts) via the single shared `tierGroups` variable.
- **Ordering direction check:** `groupByTier` sorts by `tierRank(b[0]) - tierRank(a[0])` (descending tier rank = best tier first), matching "endless above hell" display order confirmed against the live DB query earlier in this session (Jinbei/endless rank 1, XIII'th/hell rank 25 on the same day).
- **No placeholders:** every step has complete, runnable code.
