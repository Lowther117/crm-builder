# CRM Builder

A CRM you design yourself. Decide what you want to keep track of - customers
and deals, cases, members, tenders, properties, suppliers, job applications,
anything - and what each record holds, and CRM Builder makes the lists, forms,
pipeline board, search, reminders and reports to match. A **setup wizard**
takes you from nothing to a working CRM in about two minutes: pick the closest
starting point (or hand it a spreadsheet you already have), untick what you do
not need, name it, done. Change the design whenever you like afterwards -
nothing you remove is ever deleted, only hidden.

Everything lives in **one file** on your own computer or shared drive. Nothing
is uploaded anywhere. Use it alone with no sign-in at all, or share the file
with a small team where everyone has their own account. Windows and macOS, one
window, dark mode by default.

Version 2 is a rebuild from scratch: one window instead of many, a far simpler
Designer, and the wizard. It does not open files made by version 1.

---

## Which file do I use?

| File | When |
|---|---|
| `build-exe.bat` | **Windows.** Double-click once. Installs Python if it is missing, installs every dependency, and produces `dist\CRM Builder.exe` - one standalone file you can copy anywhere. |
| `build-app.command` | **macOS.** Double-click once. Installs Homebrew and Python if they are missing, installs every dependency, and produces `dist/CRM Builder.app`. Drag it to Applications. |
| `run.bat` | Windows, run from source without building an exe. Sets up a virtual environment the first time. |
| `run.command` | macOS, run from source without building an app. |
| `python crm_builder.py` | Any platform, if you already have Python 3.9+ with tkinter (`pip install -r requirements.txt` adds Excel support). A `.crm` file can be given as the first argument. |
| `CRM Builder selftest` | Makes a CRM from every template in a temporary folder and exercises it - records, notes, tasks, files, search, accounts and permissions, encryption, CSV and Excel import - then opens every page of the window in dark and light, and writes `crm-builder-selftest.txt`. The build scripts run this on the finished app and refuse to say DONE unless it reads `SELF-TEST PASSED`. Also `run.bat selftest` / `./run.command selftest`. |

The repository is deliberately flat - no Windows/Mac subfolders. The extension
already says which operating system a file is for.

**First build on a clean machine takes a few minutes** (Python, Homebrew,
wheels, PyInstaller). After that it is about a minute. Watch
`build-win-log.txt` / `build-mac-log.txt` if you want the detail; on failure
the last forty lines are printed for you. At the end a CRM Builder window
flicks through its pages for ten to twenty seconds - that is the self-test;
leave it alone.

**Encryption is optional and decided at build time.** The build tries to
install `sqlcipher3-wheels`. If there is no wheel for the Python in use (it can
lag behind the newest Python) the build says *encryption will not be available
in this build* and carries on; everything else works. On macOS the script
first tries an older installed Python that does have the wheel. Set
`CRMBUILDER_BUILD_PYTHON` to a full interpreter path to force a particular
Python.

On macOS, if a freshly written `.command` will not run, it needs its
executable bit and quarantine flag sorted once, from inside the folder:
`chmod +x *.command && xattr -d com.apple.quarantine *.command`.

If a build fails, the file to look at is `build-win-log.txt` /
`build-mac-log.txt`; if it builds but says the self-test reported problems,
`dist/crm-builder-selftest.txt`; if the app did not start at all,
`dist/crm-builder-crash.log`.

---

## The setup wizard

Shown the first time you start, and from **File > New CRM** afterwards. Five
steps, each of which can be accepted as it stands:

1. **What do you want to keep track of?** Eight starting points - Customers
   and sales, Contacts and organisations, Casework and clients, Tenders and
   bids, Members and volunteers, Properties and tenants, Suppliers and
   contracts, Job applications - or **Start from a spreadsheet I already
   have** (its columns become the fields, with the kind of each guessed from
   what is in it, and its rows are brought in for you), or a blank CRM.
2. **Your lists.** Untick any you do not want, rename them, add another.
3. **What do you record about each one?** Untick fields, rename them, add
   common ones with one click, with the form shown alongside as it will look.
4. **Who will use it?** Just me (opens straight away; a password is optional)
   or me and other people (everyone signs in). Optionally encrypt the file.
5. **Name it and choose where to keep it.** With a tick box for a few example
   records, which **Setup > Remove the example records** takes away again in
   one go.

Going back and forth, or switching between dark and light, never loses what
you have typed.

---

## Using it

**Home** answers "what do I need to do today": your tasks grouped Overdue /
Today / This week / Later, dates that are coming up (renewals, deadlines,
reviews - any date field marked *remind me*), a card per list with its count
and stages, and what changed recently.

**Lists.** Each list has its own entry in the sidebar. Search as you type,
filter by any choice, tag, yes/no or team-member field, click a heading to
sort, save a combination as a named view. Select several records and
right-click to change one field on all of them, add a task, export or delete.

**Board.** Any list with a stage-like choice field gets a **Board** switch:
one column per stage with counts and money totals. Drag a card to move it on
(or use the arrow keys, or right-click > Move to).

**Records.** The form on the left; on the right **Notes** (note, call, email,
meeting), **Tasks** with due dates, **Files** kept inside the CRM file,
**Related** records from other lists with a *+ New* that links back
automatically, and the full **History** of every change.

**Wrong information cannot be saved.** Dates get their slashes as you type
(13112026 becomes 13/11/2026; `today`, `tomorrow` and `+7` work too) and must
be real dates. UK postcodes, phone numbers and National Insurance numbers are
checked and tidied (`cf484tq` becomes `CF48 4TQ`, `+44 7700 900123` becomes
`07700 900123`). Emails, web addresses, numbers, money and percentages
likewise. A value that will not do gets a red ring and a sentence saying what
is wrong; the save lists every problem at once. Two records with the same
email or NI number asks before making a duplicate.

**Search everything** (Ctrl/Cmd+K) looks across every list and your tasks:
names, phone numbers with or without spaces, postcodes, references such as
`CON-0004`, with or without accents.

**Reports.** "Show *Deals* by *Stage* counting *total of Value*" - a bar chart
and a table for any list, split by any choice, tag, yes/no, team-member, link
or date field. Click a bar to see the records behind it.

**Getting information in and out.**
*Import from a spreadsheet* (CSV or Excel) matches columns to fields for you,
checks every row before anything is written, shows exactly what will be added
and which rows have problems (and saves those rows to a file to fix), can
update existing records instead of duplicating them, creates linked records
that do not exist yet, and is all-or-nothing. Any list exports to Excel or
CSV; **Tools > Export everything to Excel** writes the lot - a sheet per
list, plus notes and tasks - so you are never locked in. Each record has a
printable record sheet.

**Keyboard.** Ctrl/Cmd+K search, +N new record, +S save, +D dark/light, +L
lock, Alt+Left back, Esc close. Tab always moves to the next box, including
out of multi-line boxes.

---

## Changing the design

**Change the design** at the foot of the sidebar (administrators only). Lists
on the left, the selected list's fields in the middle, the form as it will
look on the right. Type a name, choose what kind of information it is, press
Enter. Nothing takes effect until **Save changes**.

Kinds of field: text, long text, number, money, percentage, date, choice
(pick one), tags (pick several), yes/no, email, phone, postcode, website, NI
number, **link to another record** (how a contact belongs to an
organisation), and team member.

Each field can be marked as needed, shown as a column in the list, given help
text, a starting value (`today`, `me`), a section heading, a rule (not in the
future, not in the past, smallest/largest, no two records the same), or a
reminder on Home. *List settings* covers the list's name, colour, reference
letters (`CON-0001`), which fields make up a record's name, and which choice
field drives the board.

Renaming never loses anything. Removing a field or a list only hides it - the
information stays in the file and *Removed fields* / *Removed lists* bring it
back. **Tools > Check the data** re-tests everything already stored against
the current design and offers to tidy what is only a formatting difference.
A design can be exported to a file and imported into another CRM.

---

## Sharing it with a team

Put the `.crm` file on a shared network drive and have everyone open the same
file. **Setup > People and passwords** adds accounts: *Administrator*
(everything, including the design and people), *Editor* (add and change
records) or *Read only*. Five wrong passwords lock an account for fifteen
minutes. Changes other people save appear within a few seconds; if two people
change the same field of the same record, the second is asked whose version
to keep.

**Do not keep a live CRM in a synced folder** (OneDrive, Dropbox, iCloud,
SharePoint sync). Sync tools copy the file while it is being written and
create conflict copies. A normal folder or a mapped network share is right;
the wizard warns if the folder looks synced.

## Backups, recycle bin, history

- A copy is made automatically the first time the CRM is opened each day, in
  a `<name> backups` folder beside the file; the newest 14 are kept.
  **File > Back up now** makes one wherever you choose. To go back to a
  backup, open it with File > Open.
- Deleted records go to **Tools > Recycle bin** and can be restored. Only an
  administrator can delete for good.
- **Tools > History of changes** is the audit trail: who changed what, from
  what, to what, and when - searchable and exportable.

## Passwords and encryption - what they do and do not do

- **No password**: anyone who can open the file can open the CRM.
- **Password, not encrypted**: the app will not open the CRM without it, and
  can lock itself after a period without use. The file itself is an ordinary
  SQLite database, so other software can still read it. A door lock, not a
  safe.
- **Encrypted** (chosen in the wizard; needs a build with encryption): the
  file is encrypted with SQLCipher (AES-256) and is unreadable without a
  password. A `<name>.crm.keys` file sits beside it holding the database key
  wrapped separately for each person; **the two files belong together** and
  the backups copy both. A **recovery code** is shown once when the CRM is
  created - it is the only way back in if a password is forgotten.
  Settings can make a new one.
- Switching someone's account off removes their copy of the key. It does not
  re-encrypt the file with a new key, so an *old copy* of the file and its
  `.keys` file (a backup they took away, say) would still open with their old
  password. Treat old backups accordingly.
- Deleting a record for good removes it and its notes, tasks and files. The
  history keeps a line saying that it was erased, including its name, and
  earlier history lines about it remain.

## Where things are kept

`crm-builder-settings.json` (theme, recent files, default save folder),
`crm-builder-crash.log` and `crm-builder-selftest.txt` sit beside the exe, or
beside the `.app` (never inside it). If that folder cannot be written to -
`C:\Program Files`, `/Applications` - they go to `~/.crm-builder` instead.
Exports and print-outs start in your Downloads folder; **File > Default save
folder** changes that.

---

## Files

```
crm_builder.py / crm_builder_app.py   source entry / entry used by the built app (crash log, selftest)
crmbuilder/
  validate.py      field kinds: what each accepts, how it is tidied, stored and shown
  blueprint.py     the design model (lists, fields, links, titles, boards)
  templates.py     the wizard's starting points and their example records
  db.py            the database: records, notes, tasks, files, history, accounts, backups
  security.py      password hashing and the key file for encrypted CRMs
  sheets.py        reading CSV and Excel files
  importing.py     the import engine (match columns, check, bring in - all or nothing)
  paths.py         settings, the app folder, the default save folder
  selftest.py      the self-test
  theme.py         the house light/dark theme (shared with the other apps, unmodified)
  ui/
    app.py         the window: sidebar, menus, navigation, lock, picking up other people's changes
    styles.py      extra styles on top of theme.py (tick boxes, sidebar, headings)
    widgets.py     scrolling frames, dialogs, field editors, the generated form
    welcome.py     welcome, sign-in, lock screen, recovery code
    wizard.py      the setup wizard
    designer.py / fieldedit.py   Change the design, and the field editor it shares with the wizard
    home.py / tasks.py / search.py
    listpage.py / board.py / recordpage.py / picker.py
    reports.py / importer.py / tools.py (exports, record sheet, backup, Check the data)
    admin.py       People and passwords, Settings, Recycle bin, History of changes
```

Python 3.9 to 3.14, Tk 8.6 or 9. The only third-party packages are `openpyxl`
(Excel import and export; without it the app works with CSV) and, optionally,
`sqlcipher3-wheels` (encryption).

## Licence

MIT No Attribution - see `LICENSE`.
