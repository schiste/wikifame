# Renaming the tool: WikiFame → WikiPeople

This runbook covers one specific migration and can be deleted once it is done. It exists
because the rename is not one change but two of opposite natures, and doing them in the
wrong order breaks the gadget for readers.

## Why this is not a search-and-replace

**A Toolforge tool cannot be renamed.** The tool account, its `toolforge.org` subdomain and
its ToolsDB database prefix are all the same name, and the documented answer is to create a
new tool account and migrate to it
([Help:Toolforge/Tool accounts](https://wikitech.wikimedia.org/wiki/Help:Toolforge/Tool_accounts)).
So the work splits in two:

| Half | Size | Reversible? |
| --- | --- | --- |
| Inside the repository | 444 occurrences, 4 casings, 20 renamed paths, 81 imports | yes, it is a commit |
| Outside the repository | new tool, new database, new URL, on-wiki pages | no, and it is visible to readers |

The second half is what this runbook is for. The first half is mechanical and the test
suite proves it.

## The ordering constraint

The gadget hard-codes the API base URL and the CSS class names its stylesheet targets.
Those three — the URL that answers, the published gadget, the on-wiki CSS — must change in
the same sitting. Everything else is prepared cold, before anyone notices.

**The single-writer rule.** Two tools running their jobs against two databases diverge from
the moment the data is copied. So the new tool is stood up and validated *without ever
writing*, and becomes the only writer at the moment of the switch. The divergence window is
then minutes, not days.

## Rules that hold throughout

- **`ALGORITHM_VERSION` does not move.** The current values — `surviving-tokens-v1`,
  `attribution-ladder-v3` — do not contain the tool name, so the rename cannot touch them by
  accident, but verify it before committing. Moving it would send 20,000 stored results back
  through a WikiWho that killed 548 jobs in the week this was written.
- **Keep the WikiWho contact continuous.** The user agent is what its operators have on file
  against the agreed rate. Renaming the variable is fine; changing the contact address in the
  same breath makes the tool look like a new client asking for capacity nobody granted.
- **Never commit credentials.** The database name and the environment variable *values* are
  deployment state. This file records variable names only.

## Phase 1 — Stand the new tool up, writing nothing

1. **Maintainer:** create the `wikipeople` tool in
   [toolsadmin](https://toolsadmin.wikimedia.org/), then log out of SSH and back in. Tool
   creation cannot be automated from here.
2. Clone the repository under the new tool and deploy the web service only:

   ```bash
   toolforge build start https://github.com/schiste/wikifame
   toolforge webservice buildservice start
   ```

3. Create the database, as in the first-deployment procedure in
   [operations.md](operations.md): `CREATE DATABASE ${TOOL_TOOLSDB_USER}__wikipeople`.
4. **Maintainer:** set the environment variables. There are 36. Four carry new *values*, not
   just new names:

   | Variable | Why it changes |
   | --- | --- |
   | `TOOL_TOOLSDB_USER`, `TOOL_TOOLSDB_PASSWORD` | injected by Toolforge for the new tool |
   | `TOOLSDB_DATABASE` | the new database |
   | `WIKIFAME_USER_AGENT` → `WIKIPEOPLE_USER_AGENT` | same contact, new name |
   | `OPTOUT_PAGE`, `METHODOLOGY_URL` | new on-wiki titles |

5. **Create no jobs.** Web service only, empty database. Nothing writes.

**Gate:** `https://wikipeople.toolforge.org/v1/stats` answers. Readers have seen nothing.

## Phase 2 — Rename inside the repository

One commit, no reader impact, guarded by 198 tests and `node --check`.

| What changes | Count | Risk |
| --- | --- | --- |
| `src/wikifame/` → `src/wikipeople/`, and imports | 81 | none; the suite proves it |
| `wikifame.js` / `wikifame.css` → `wikipeople.*` | 2 files | none until published |
| CSS class names `wikifame-*` | 89 | **breaks on-wiki styling — phase 3** |
| `mw.hook( 'wikifame.summary' / '.history' )` | 2 | **breaks on-wiki scripts — phase 3** |
| `/wikifame-config.json` suffix | 1 | **breaks reader config pages — phase 3** |
| `TOOLFORGE_API_BASE` in the gadget | 1 | points at the new tool |
| `WIKIFAME_USER_AGENT` → `WIKIPEOPLE_USER_AGENT` | 6 | set in phase 1 |
| Docs, ADRs, README, `jobs.yaml`, `Procfile` | ~260 | none |

The hooks and class names are the gadget's public extension point (ADR-0004). They are
renamed in one go rather than shimmed: there is one known consumer, and a compatibility
layer emitting both sets is cruft that outlives its reason.

Also in this phase, the **migration script**, because it is code and belongs under test.

### Why a script and not `mysqldump`

There is no `mysqldump` on the Toolforge bastion — only the `mysql` client. Dumping through
`SELECT` into TSV is not an option either: block and lock reasons are free text and now
legitimately contain tabs and newlines.

So migration reuses what the project already has. `models.py` defines the schema,
`create_schema()` creates it in the new database, and a `migrate` entry point copies rows
table by table, reading through the same SQLAlchemy models that wrote them. It runs **as the
old tool**, which already holds its own credentials, and writes to the new database using
the new tool's credentials passed in the environment — so no cross-tool `GRANT` is needed.

Six tables, 17 MB, ~34,000 rows. It must report per-table counts and refuse to run against a
non-empty target.

## Phase 3 — The switch

The only window where anything can look broken. Do it in one sitting.

1. Stop the old tool's jobs. There is now no writer.

   ```bash
   toolforge jobs delete attribution-worker    # and each scheduled job
   ```

2. Run the migration, then compare the six table counts against the source.
3. Deploy the renamed repository to the new tool and start its jobs:

   ```bash
   scp jobs.yaml login.toolforge.org:/mnt/nfs/labstore-secondary-tools-project/wikipeople/jobs.yaml
   toolforge build start https://github.com/schiste/wikifame
   toolforge webservice buildservice restart
   toolforge jobs load jobs.yaml
   ```

   `jobs load` prints one line per job; count them against `jobs.yaml` before believing it,
   and check that `jobs show attribution-worker` reports a start time after the build. That
   restart has silently no-op'd more than once.
4. **Maintainer, in one session on-wiki:** publish `User:<name>/wikipeople.js`, replace the
   CSS block in `global.css`, and move the opt-out page. Between the first and last of these
   the styling is wrong, which is why they are one step and not three.
5. Replace the old tool's web service with 301 redirects to the new host.

**Gate:** a known frwiki article returns the same names as before the switch, and
`wikifame.toolforge.org/v2/...` redirects.

This phase is also the moment to clear two known on-wiki defects, since the pages are being
rewritten anyway: the opt-out page links to `[[Discussion Wikipédia:…]]` (namespace 5) where
it means the maintainer's user talk page (namespace 3), and
`docs/onwiki/presentation.en.wiki` is stale.

## Phase 4 — Retire the old tool

Only after the grace period, and in this order:

1. Rename the GitHub repository. GitHub keeps a redirect, so a `toolforge build start`
   against the old URL keeps working; update it anyway.
2. Disable `wikifame` in toolsadmin. **Irreversible**, and it stops the redirects — so it is
   the last step, taken deliberately, not the tidy-up at the end of a long day.

## Rollback

| Phase | How to undo |
| --- | --- |
| 1 | Delete the new tool. Nothing else was touched. |
| 2 | `git revert`. Production still runs the old commit. |
| 3, before step 4 | Restart the old tool's jobs. It still holds its data; only minutes are lost. |
| 3, after step 4 | Re-publish the previous gadget and CSS from git history, and point it back. |
| 4 | None. This is what makes it the last step. |
