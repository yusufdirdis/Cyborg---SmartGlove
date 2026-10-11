# Fix: hover-expand on Edit, Delete and Clear key

Cosmetic fix only (markup inside existing buttons + CSS in `frontend/src/index.css`). No handler, prop, state, API call or backend change.

## The design (from the "Buttons that say what they do" board)
Quiet utility buttons rest as a **circle with only the icon**. On **mouse hover or keyboard focus** the button **widens and the label slides out** next to the icon in 0.2 s; it slides back when the pointer leaves. On touch screens (`hover:none`) the label is always shown. With `prefers-reduced-motion` the change is instant.

| Button | Where | Icon | Label that slides out | Hover colour |
|---|---|---|---|---|
| Refresh status | Settings › Devices, Live status card | `RefreshCw` | Refresh status | grey (`btn-secondary` hover) |
| Edit (macro) | Macro Studio, each macro row | `Pencil` | Edit | light grey `#f2f5f0` |
| Delete (macro) | Macro Studio, each macro row | `Trash2` | Delete | coral: bg `#fff7f4`, border `#e2b6a9`, text `#a74731` |
| Clear key | Settings › Gemini key card | `Eraser` | Clear key | coral: bg `#fff7f4`, text `#a74731` |

Sizes: Refresh status and Clear key rest at 40 × 40 px. Edit and Delete rest at 32 × 32 px (list-row size). The confirm step after Clear key ("Yes, clear key" coral + "Keep") is always labelled and is unchanged.

## What's wrong today (checked in the browser on `main` / `ux/polish-and-fixes`)
- **Refresh status: works.** 40 px at rest → ~111 px on hover with the label. Leave it alone; it is the reference implementation (`.btn-expand` + `.btn-label` in index.css).
- **Clear key: doesn't expand.** It is `btn btn-secondary btn-icon btn-danger ml-auto` with only an `<Eraser/>` icon. `btn-icon` fixes the width at 40 px and there is no label span, so it only turns coral.
- **Edit and Delete (macro rows): don't expand.** They are `macro-edit` / `macro-delete` with only an icon and a `title` tooltip; there is no label and no expand CSS. Delete only turns coral.

## Changes

### 1. Clear key — `frontend/src/main.tsx` (find `aria-label="Clear Gemini key"`)
Swap `btn-icon` for `btn-expand`, add the label span, drop the now-redundant `title`. Keep `aria-label`, `disabled` and `onClick` exactly as they are.
```tsx
<button className="btn btn-secondary btn-expand btn-danger ml-auto" aria-label="Clear Gemini key" disabled={busy===p.name} onClick={()=>setClearing(p.name)}><Eraser size={16}/><span className="btn-label" aria-hidden="true">Clear key</span></button>
```
`.btn-danger:hover` already supplies the coral colours.

### 2. Edit and Delete — `frontend/src/main.tsx` (find `className="macro-edit"`)
Add a label span inside each button and drop the `title` attributes. Keep `aria-label` and `onClick` exactly as they are.
```tsx
<button className="macro-edit" aria-label={`Edit ${m.name}`} onClick={()=>edit(m)}><Pencil size={15}/><span className="btn-label" aria-hidden="true">Edit</span></button><button className="macro-delete" aria-label={`Delete ${m.name}`} onClick={()=>remove(m)}><Trash2 size={15}/><span className="btn-label" aria-hidden="true">Delete</span></button>
```

### 3. CSS — append to `frontend/src/index.css` (after the existing `.macro-actions .macro-delete:hover…` rule)
```css
.macro-actions .macro-edit,.macro-actions .macro-delete{gap:0;padding:0;transition:gap .2s ease-out,padding .2s ease-out,background-color .15s,color .15s,border-color .15s}
.macro-actions .btn-label{display:inline-block;max-width:0;opacity:0;overflow:hidden;transition:max-width .2s ease-out,opacity .15s}
.macro-actions .macro-edit:hover,.macro-actions .macro-edit:focus-visible,.macro-actions .macro-delete:hover,.macro-actions .macro-delete:focus-visible{gap:6px;padding:0 12px}
.macro-actions button:hover .btn-label,.macro-actions button:focus-visible .btn-label{max-width:80px;opacity:1}
@media (hover:none){.macro-actions .macro-edit,.macro-actions .macro-delete{gap:6px;padding:0 12px}.macro-actions .btn-label{max-width:80px;opacity:1}}
@media (prefers-reduced-motion:reduce){.btn-expand,.btn-expand .btn-label,.macro-actions .macro-edit,.macro-actions .macro-delete,.macro-actions .btn-label{transition:none}}
```
This was tested by injecting it into the running app: Edit goes 32 px → ~70 px on hover, Delete stays 32 px until hovered, the row's actions stay right-aligned and the macro name just truncates a little more while hovering.

## Check before opening the PR
1. `cd frontend && npm run build` passes (it is the type check).
2. In the browser at desktop width: hover and Tab-focus each of the four buttons; each widens with its label and returns to a circle. Delete and Clear key turn coral; Edit and Refresh turn grey.
3. Clicking still does the same thing as before: Edit opens the editor, Delete shows the existing confirm, Clear key shows "Yes, clear key / Keep", Refresh status rescans. (Clear key only appears once a Gemini key is saved.)
4. Phone width (375 px, touch emulation): labels show permanently and nothing overflows the row.

## Note from implementation (2026-10-11)
Check 4 failed as written: at 375 px with touch emulation, four labelled buttons (Test on glove 123 px + Play 67 + Edit 70 + Delete 83 + gaps = 361 px) don't fit the 337 px action row, so Delete was cut off. Agreed fix: on touch screens with a narrow row (`@container (max-width:520px)` inside `@media (hover:none)`), Edit and Delete stay 40 px icon circles, as they were before this fix; their aria-labels still name them. Desktop hover and keyboard focus behave exactly as described above, and Clear key and Refresh status still show their labels on phones.
