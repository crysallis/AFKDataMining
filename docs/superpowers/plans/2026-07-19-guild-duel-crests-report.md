# Guild Duel Crests Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Guild Duel" tab to the RiffRaff stats site (`DiscordBotAfkJ/stats/`) showing a per-member crest trend chart and a period-scoped "had crests / no crests" data list.

**Architecture:** Follows the existing stats-site pattern exactly: one Express API route returning flat + precomputed-absence data, one client-side chart module that reshapes the flat rows into a `Map`, Chart.js for the trend chart, plain HTML tables for the lists. No server-side templating, no test framework (matches every existing report in this codebase).

**Tech Stack:** Express (better-sqlite3 driver via `utils/db.js`), vanilla JS + Vite, Chart.js v4.

## Global Constraints

- Spec: `AFKDataMining/docs/superpowers/specs/2026-07-19-guild-duel-crests-report-design.md`
- All file paths in this plan are relative to `C:\vscode\DiscordBotAfkJ\` unless stated otherwise.
- No automated test suite exists for `stats/` (confirmed: no `stats/**/*.test.js`, no test runner in `stats/package.json`). This plan does not add one — matches Power/Supreme Arena/Dream Realm, which all ship without tests. Verification is manual, driven by a real browser check against `guild.db`, described per task.
- Roster scoping convention: `WHERE m.active = 1 AND m.pending = 0` (used verbatim in every existing route).
- No em dashes in any code comment or commit message (project-wide convention) — use `·` or `...`.
- `guild_duel_rankings` schema (already exists, `AFKDataMining/src/db.py:151-158`, do not modify):
  ```sql
  CREATE TABLE IF NOT EXISTS guild_duel_rankings (
      member_id    INTEGER NOT NULL REFERENCES members(id),
      period_start TEXT NOT NULL,
      rank         INTEGER,
      crests       INTEGER,
      scanned_at   TEXT NOT NULL,
      PRIMARY KEY (member_id, period_start)
  );
  ```

---

### Task 1: `/api/guild-duel` route

**Files:**
- Modify: `stats/server.js` (add new route after the existing `/api/arena` route, which ends at line 157)

**Interfaces:**
- Produces: `GET /api/guild-duel` → `{ periods: string[], rows: GDRow[], absent: GDAbsentRow[] }` where
  - `GDRow = { member_id: number, ingame_name: string, warband_id: number|null, warband_name: string|null, period_start: string, crests: number|null }`
  - `GDAbsentRow = { member_id: number, ingame_name: string, warband_id: number|null, warband_name: string|null, period_start: string }`
  - `periods` is every distinct `period_start` in `guild_duel_rankings`, ascending.

- [ ] **Step 1: Add the route**

Insert immediately after the closing `});` of the `/api/arena` route (`server.js:157`):

```js
// GET /api/guild-duel — crest rankings + who's missing, per period
app.get('/api/guild-duel', (req, res) => {
    try {
        const periods = db.prepare(
            'SELECT DISTINCT period_start FROM guild_duel_rankings ORDER BY period_start'
        ).all().map(r => r.period_start);

        const rows = db.prepare(`
            SELECT m.id as member_id, m.ingame_name, m.warband_id, w.name as warband_name,
                   g.period_start, g.crests
            FROM guild_duel_rankings g
            JOIN members m ON m.id = g.member_id
            LEFT JOIN warbands w ON w.id = m.warband_id
            ORDER BY g.period_start, g.crests DESC
        `).all();

        const absent = db.prepare(`
            SELECT m.id as member_id, m.ingame_name, m.warband_id, w.name as warband_name,
                   p.period_start
            FROM members m
            CROSS JOIN (SELECT DISTINCT period_start FROM guild_duel_rankings) p
            LEFT JOIN warbands w ON w.id = m.warband_id
            LEFT JOIN guild_duel_rankings g ON g.member_id = m.id AND g.period_start = p.period_start
            WHERE m.active = 1 AND m.pending = 0 AND g.member_id IS NULL
            ORDER BY p.period_start, m.ingame_name
        `).all();

        res.json({ periods, rows, absent });
    } catch (err) {
        console.error('[stats] /api/guild-duel error:', err);
        res.status(500).json({ error: 'Database error' });
    }
});
```

- [ ] **Step 2: Manual verification**

Start the stats server (`npm run dev --prefix stats` or however it's normally run locally — check `stats/package.json` `scripts` if unsure) and hit the route directly:

```bash
curl -s http://localhost:3002/api/guild-duel -H "Cookie: <your session cookie>" | head -c 500
```

If auth blocks a bare curl (likely, per `auth.js` session gating), instead open the browser dev tools Network tab while logged into the stats site, navigate to `http://localhost:3002/api/guild-duel` directly in a new tab (reuses the browser's session cookie), and confirm the JSON has non-empty `periods`, `rows`, and (if any active member has ever missed a period) non-empty `absent`.

Expected: valid JSON, no 500, `rows.length > 0`, `periods.length > 0`.

- [ ] **Step 3: Commit**

```bash
git add stats/server.js
git commit -m "feat(stats): add /api/guild-duel route"
```

---

### Task 2: `guildDuel.js` chart module — state, warband filter, period selector

**Files:**
- Create: `stats/src/charts/guildDuel.js`

**Interfaces:**
- Consumes: nothing from other tasks (first frontend task).
- Produces: `export async function initGuildDuel(me)` — the only export, called by `main.js` in Task 4. Internal `state` shape (used by Task 3's rendering):
  ```js
  state = {
      periods: string[],              // ascending, includes 'all' sentinel logic handled separately
      memberMap: Map<number, {
          id: number, name: string, warbandId: number|null, warbandName: string,
          crestsByPeriod: { [period_start]: number },
      }>,
      absentByPeriod: Map<string, Set<number>>,   // period_start -> Set of member_id
  }
  ```

- [ ] **Step 1: Write the module skeleton with fetch + state building**

```js
import { Chart, registerables } from 'chart.js';
import { escHtml, getCSSVar } from '../utils.js';
Chart.register(...registerables);

let chart = null;
let state = null;
let meId  = null;

export async function initGuildDuel(me) {
    const res  = await fetch('/api/guild-duel');
    const data = await res.json();

    meId = me?.memberId ?? null;

    const memberMap = new Map();
    function getMember(r) {
        if (!memberMap.has(r.member_id)) {
            memberMap.set(r.member_id, {
                id:             r.member_id,
                name:           r.ingame_name,
                warbandId:      r.warband_id,
                warbandName:    r.warband_name || 'Unassigned',
                crestsByPeriod: {},
            });
        }
        return memberMap.get(r.member_id);
    }

    for (const r of data.rows) {
        getMember(r).crestsByPeriod[r.period_start] = r.crests;
    }

    const absentByPeriod = new Map();
    for (const r of data.absent) {
        getMember(r); // ensure the member exists in memberMap even if they never had a single crest row
        if (!absentByPeriod.has(r.period_start)) absentByPeriod.set(r.period_start, new Set());
        absentByPeriod.get(r.period_start).add(r.member_id);
    }

    state = { periods: data.periods, memberMap, absentByPeriod };

    // Warband filter
    const wbFilter = document.getElementById('gd-warband-filter');
    const warbands = [...new Set([...memberMap.values()].map(m => m.warbandName))].sort();
    for (const wb of warbands) {
        const opt = document.createElement('option');
        opt.value = wb; opt.textContent = wb;
        wbFilter.appendChild(opt);
    }
    wbFilter.addEventListener('change', renderAll);

    // Period selector: most recent period first, then older periods, then "All Time"
    const periodSel = document.getElementById('gd-period-select');
    const reversed = [...state.periods].reverse();
    for (const p of reversed) {
        const opt = document.createElement('option');
        opt.value = p; opt.textContent = p;
        periodSel.appendChild(opt);
    }
    const allOpt = document.createElement('option');
    allOpt.value = 'all'; allOpt.textContent = 'All Time';
    periodSel.appendChild(allOpt);
    if (reversed.length) periodSel.value = reversed[0];
    periodSel.addEventListener('change', renderAll);

    renderAll();
}

function renderAll() {
    // filled in by Task 3
}
```

- [ ] **Step 2: Manual verification (state building only)**

Temporarily add `window.__gdDebug = () => state;` at the end of `initGuildDuel` (after `renderAll()`), load the stats site in a browser with the Guild Duel tab wired in later (Task 4) — for now, verify by importing and calling the module directly in a scratch Vite entry, OR skip standalone verification and fold this check into Task 4's manual verification once the tab exists. Prefer the latter: remove the `window.__gdDebug` line before committing, don't add debug-only code to the file.

Note: this step has no independent runtime check until Task 4 wires the tab into `index.html`/`main.js` — that's fine, `renderAll` being a no-op stub means this task can't crash anything yet. Proceed to Step 3.

- [ ] **Step 3: Commit**

```bash
git add stats/src/charts/guildDuel.js
git commit -m "feat(stats): add guildDuel.js state loading (chart/table rendering stubbed)"
```

---

### Task 3: `guildDuel.js` — chart + data list rendering

**Files:**
- Modify: `stats/src/charts/guildDuel.js` (implement `renderAll`, called from Task 2's `initGuildDuel`)

**Interfaces:**
- Consumes: `state` shape from Task 2, DOM ids from Task 4 (`gd-warband-filter`, `gd-period-select`, `gd-member-wrap`, `chart-guildduel`, `gd-had-crests`, `gd-no-crests`) — these ids are defined here so Task 4 must match them exactly.
- Produces: fully working `renderAll()`, plus a member multi-select dropdown builder `buildMemberDropdown()` (copied/adapted from `power.js`'s `buildDropdown`, since the per-member trend chart uses the same multi-select UX per the design spec).

- [ ] **Step 1: Replace the `renderAll` stub with the full implementation**

Replace the `// filled in by Task 3` stub with:

```js
let selectedIds = new Set();
let allSelected = true;

function renderAll() {
    renderChart();
    renderLists();
}

function renderChart() {
    const { periods, memberMap } = state;
    const wbSel = document.getElementById('gd-warband-filter').value;

    let filtered = [...memberMap.values()];
    if (!allSelected && selectedIds.size > 0) {
        filtered = filtered.filter(m => selectedIds.has(m.id));
    } else if (wbSel) {
        filtered = filtered.filter(m => m.warbandName === wbSel);
    }

    const lastPeriod = periods[periods.length - 1];
    filtered.sort((a, b) =>
        (b.crestsByPeriod[lastPeriod] || 0) - (a.crestsByPeriod[lastPeriod] || 0)
    );

    const labels   = periods;
    const colors   = memberColors(filtered.length);
    const datasets = filtered.map((m, i) => {
        const isMe  = m.id === meId;
        const muted = allSelected && !isMe;
        const color = colors[i];
        return {
            label:           m.name,
            data:            periods.map(p => m.crestsByPeriod[p] ?? null),
            borderColor:     muted ? muteHsl(color, 0.9) : color,
            backgroundColor: muted ? muteHsl(color, 0.9) : color,
            borderWidth:     muted ? 1 : isMe ? 2.5 : 2,
            pointRadius:     muted ? 0 : 2,
            spanGaps:        true,
            tension:         0.2,
        };
    });

    if (chart) {
        chart.data.labels   = labels;
        chart.data.datasets = datasets;
        chart.options.plugins.legend.display = filtered.length <= 5;
        chart.update();
        return;
    }

    chart = new Chart(document.getElementById('chart-guildduel'), {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend:  { display: filtered.length <= 5, labels: { color: getCSSVar('--color-base-content'), font: { size: 11 }, boxWidth: 12, boxHeight: 12 } },
                tooltip: { callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y ?? 0}` } },
            },
            scales: {
                x: { grid: { color: getCSSVar('--color-base-content') }, ticks: { color: getCSSVar('--color-base-content') } },
                y: {
                    grid:  { color: getCSSVar('--color-base-content') },
                    ticks: { color: getCSSVar('--color-base-content') },
                    title: { display: true, text: 'Crests', color: getCSSVar('--color-base-content') },
                },
            },
        },
    });
    chart._recolor = renderChart;
}

function renderLists() {
    const { periods, memberMap, absentByPeriod } = state;
    const wbSel  = document.getElementById('gd-warband-filter').value;
    const period = document.getElementById('gd-period-select').value;

    let members = [...memberMap.values()];
    if (wbSel) members = members.filter(m => m.warbandName === wbSel);

    let hadRows, noRows;

    if (period === 'all') {
        hadRows = members
            .map(m => ({ m, total: Object.values(m.crestsByPeriod).reduce((a, b) => a + b, 0), count: Object.keys(m.crestsByPeriod).length }))
            .filter(r => r.count > 0)
            .sort((a, b) => b.total - a.total);
        noRows = members
            .filter(m => Object.keys(m.crestsByPeriod).length === 0)
            .sort((a, b) => a.name.localeCompare(b.name));
    } else {
        const absentIds = absentByPeriod.get(period) || new Set();
        hadRows = members
            .filter(m => m.crestsByPeriod[period] !== undefined)
            .map(m => ({ m, total: m.crestsByPeriod[period], count: 1 }))
            .sort((a, b) => b.total - a.total);
        noRows = members
            .filter(m => absentIds.has(m.id))
            .sort((a, b) => a.name.localeCompare(b.name));
    }

    document.getElementById('gd-had-crests').innerHTML = renderHadTable(hadRows, period);
    document.getElementById('gd-no-crests').innerHTML  = renderNoTable(noRows);
}

function renderHadTable(rows, period) {
    if (!rows.length) return '<div class="empty-state">No data for this filter.</div>';
    const label = period === 'all' ? 'Total Crests' : 'Crests';
    let html = `<table class="data-table"><thead><tr><th>Member</th><th>Warband</th><th>${label}</th></tr></thead><tbody>`;
    for (const { m, total } of rows) {
        const isMe = m.id === meId;
        html += `<tr${isMe ? ' class="me"' : ''}>
            <td data-label="Member">${escHtml(m.name)}</td>
            <td data-label="Warband"><span class="warband-tag">${escHtml(m.warbandName)}</span></td>
            <td data-label="${label}"><strong>${total}</strong></td>
        </tr>`;
    }
    html += '</tbody></table>';
    return html;
}

function renderNoTable(rows) {
    if (!rows.length) return '<div class="empty-state">Everyone had crests.</div>';
    let html = '<table class="data-table"><thead><tr><th>Member</th><th>Warband</th></tr></thead><tbody>';
    for (const m of rows) {
        const isMe = m.id === meId;
        html += `<tr${isMe ? ' class="me"' : ''}>
            <td data-label="Member">${escHtml(m.name)}</td>
            <td data-label="Warband"><span class="warband-tag">${escHtml(m.warbandName)}</span></td>
        </tr>`;
    }
    html += '</tbody></table>';
    return html;
}

function memberColors(n) {
    return Array.from({ length: n }, (_, i) =>
        `hsl(${Math.round((i * 360) / Math.max(n, 1))}, 65%, 60%)`
    );
}

function muteHsl(hsl, alpha) {
    return hsl.replace('hsl(', 'hsla(').replace(')', `, ${alpha})`);
}
```

- [ ] **Step 2: Add the member multi-select dropdown (copy of `power.js`'s pattern)**

Add this function to the same file, and call `buildMemberDropdown(document.getElementById('gd-member-wrap'), state.memberMap, me)` at the end of `initGuildDuel` (after the period selector wiring, before `renderAll()`):

```js
function buildMemberDropdown(container, members, me) {
    const wrap = document.createElement('div');
    wrap.className = 'member-dropdown';

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'member-dropdown-btn';

    const menu = document.createElement('div');
    menu.className = 'member-dropdown-menu hidden';

    const allItem  = makeCheckItem('gd-mb-all', 'All members', allSelected);
    const allCheck = allItem.querySelector('input');
    menu.appendChild(allItem);

    const divider = document.createElement('hr');
    menu.appendChild(divider);

    const sorted = [...members.values()].sort((a, b) => {
        if (a.id === meId) return -1;
        if (b.id === meId) return 1;
        return a.name.localeCompare(b.name);
    });

    const grid = document.createElement('div');
    grid.className = 'member-grid';
    for (const m of sorted) {
        const label = m.id === meId ? m.name + ' (you)' : m.name;
        const item  = makeCheckItem(`gd-mb-${m.id}`, label, selectedIds.has(m.id));
        if (m.id === meId) item.classList.add('me-item');
        const check = item.querySelector('input');
        check.dataset.memberId = m.id;
        grid.appendChild(item);
    }
    menu.appendChild(grid);

    btn.addEventListener('click', e => {
        e.stopPropagation();
        menu.classList.toggle('hidden');
    });
    document.addEventListener('click', () => menu.classList.add('hidden'), { passive: true });
    menu.addEventListener('click', e => e.stopPropagation());

    allCheck.addEventListener('change', () => {
        if (allCheck.checked) {
            allSelected = true;
            selectedIds.clear();
            menu.querySelectorAll('input[data-member-id]').forEach(c => { c.checked = false; });
        } else {
            if (selectedIds.size === 0) { allCheck.checked = true; return; }
            allSelected = false;
        }
        syncBtn();
        renderChart();
    });

    menu.querySelectorAll('input[data-member-id]').forEach(check => {
        check.addEventListener('change', () => {
            const id = parseInt(check.dataset.memberId);
            if (check.checked) {
                selectedIds.add(id);
                allCheck.checked = false;
                allSelected = false;
            } else {
                selectedIds.delete(id);
                if (selectedIds.size === 0) {
                    allCheck.checked = true;
                    allSelected = true;
                }
            }
            syncBtn();
            renderChart();
        });
    });

    function syncBtn() {
        if (allSelected) {
            btn.textContent = 'All members ▾';
        } else if (selectedIds.size === 1) {
            const m = members.get([...selectedIds][0]);
            btn.textContent = (m?.name ?? '?') + ' ▾';
        } else {
            const names = [...selectedIds].map(id => members.get(id)?.name).filter(Boolean);
            btn.textContent = `${names[0]} +${names.length - 1} ▾`;
        }
    }

    wrap.appendChild(btn);
    wrap.appendChild(menu);
    container.appendChild(wrap);
    syncBtn();
}

function makeCheckItem(id, label, checked) {
    const el     = document.createElement('label');
    el.className = 'member-check-item';
    el.htmlFor   = id;
    const check  = document.createElement('input');
    check.type   = 'checkbox';
    check.id     = id;
    check.checked = checked;
    const span = document.createElement('span');
    span.textContent = label;
    el.appendChild(check);
    el.appendChild(span);
    return el;
}
```

And update `initGuildDuel`'s tail (replace the final `renderAll();` line) to:

```js
    buildMemberDropdown(document.getElementById('gd-member-wrap'), memberMap, me);

    renderAll();
```

- [ ] **Step 3: Commit**

```bash
git add stats/src/charts/guildDuel.js
git commit -m "feat(stats): implement Guild Duel chart and data-list rendering"
```

(Manual verification for this task happens in Task 4, once the tab/DOM exists to render into.)

---

### Task 4: Wire the tab into `index.html` and `main.js`

**Files:**
- Modify: `stats/src/index.html` (add tab button at line 46, add `<section>` after the Supreme Arena section, before the Arcane Lab section at line 153)
- Modify: `stats/src/main.js` (import + `activateTab` branch)

**Interfaces:**
- Consumes: `initGuildDuel` export from Task 2/3, DOM ids `gd-warband-filter`, `gd-period-select`, `gd-member-wrap`, `chart-guildduel`, `gd-had-crests`, `gd-no-crests` (must match exactly what Task 3's code queries via `getElementById`).

- [ ] **Step 1: Add the tab button**

In `stats/src/index.html`, change line 46 from:
```html
      <button class="tab" data-tab="preview">Theme Preview</button>
```
to:
```html
      <button class="tab" data-tab="guildduel">Guild Duel</button>
      <button class="tab" data-tab="preview">Theme Preview</button>
```

- [ ] **Step 2: Add the tab section**

Insert this new `<section>` right after the Supreme Arena section's closing `</section>` (after line 151, before the `<!-- Arcane Lab -->` comment at line 153):

```html
    <!-- Guild Duel -->
    <section id="tab-guildduel" class="tab-content hidden">
      <div class="controls">
        <label>
          Warband
          <select id="gd-warband-filter">
            <option value="">All Warbands</option>
          </select>
        </label>
        <label>
          Period
          <select id="gd-period-select"></select>
        </label>
        <label>
          Members
          <div id="gd-member-wrap"></div>
        </label>
      </div>
      <div class="chart-box full">
        <canvas id="chart-guildduel"></canvas>
      </div>
      <div class="dr-layout">
        <div class="table-box">
          <h2>Had Crests</h2>
          <div id="gd-had-crests"></div>
        </div>
        <div class="table-box">
          <h2>No Crests</h2>
          <div id="gd-no-crests"></div>
        </div>
      </div>
    </section>

```

(Reuses `.dr-layout`'s existing two-column grid CSS for the side-by-side lists — no new CSS needed.)

- [ ] **Step 3: Wire up in `main.js`**

In `stats/src/main.js`, change line 10 from:
```js
import { initLab }        from './charts/lab.js';
```
to:
```js
import { initLab }        from './charts/lab.js';
import { initGuildDuel }  from './charts/guildDuel.js';
```

And change line 106-107 from:
```js
        if (name === 'lab')        await initLab(me);
        // preview tab is static HTML — no async init needed
```
to:
```js
        if (name === 'lab')        await initLab(me);
        if (name === 'guildduel')  await initGuildDuel(me);
        // preview tab is static HTML — no async init needed
```

- [ ] **Step 4: Manual verification**

Run the stats site locally (check `stats/package.json` for the dev script, likely `npm run dev --prefix stats` or similar — Vite dev server), log in, click the new "Guild Duel" tab.

Confirm:
- Chart renders with at least one line, x-axis shows real period dates, y-axis labeled "Crests".
- Switching the period selector between two real weeks changes both the "Had Crests" and "No Crests" tables.
- Selecting "All Time" shows summed totals in "Had Crests" and shows only members with zero total periods in "No Crests".
- Warband filter narrows both tables and the chart's default (all-members) view.
- Multi-select member dropdown narrows the chart to just selected members.
- No console errors.

Cross-check one number against the DB directly to catch a wiring bug:
```sql
SELECT ingame_name, crests FROM guild_duel_rankings g JOIN members m ON m.id = g.member_id
WHERE period_start = (SELECT MAX(period_start) FROM guild_duel_rankings) ORDER BY crests DESC LIMIT 5;
```
Confirm the top 5 in this query match the top 5 shown in the "Had Crests" table for the most recent period.

- [ ] **Step 5: Commit**

```bash
git add stats/src/index.html stats/src/main.js
git commit -m "feat(stats): wire Guild Duel tab into index.html and main.js"
```

---

## Self-Review Notes

- **Spec coverage:** warband filter (Task 4), period selector incl. "All Time" (Task 2/3), per-member multi-select trend chart with `spanGaps` (Task 3), "had crests" sorted desc / "no crests" alphabetical per period (Task 3), all-time sum + ever-zero list (Task 3), no delta column (intentionally absent, matches spec's "Out of scope"). All covered.
- **Type consistency:** `GDRow`/`GDAbsentRow` field names (`member_id`, `ingame_name`, `warband_id`, `warband_name`, `period_start`, `crests`) match between Task 1's SQL aliases and Task 2's `data.rows`/`data.absent` consumption.
- **DOM id consistency:** `gd-warband-filter`, `gd-period-select`, `gd-member-wrap`, `chart-guildduel`, `gd-had-crests`, `gd-no-crests` are used identically in Task 2/3 (`getElementById`) and Task 4 (HTML element ids).
