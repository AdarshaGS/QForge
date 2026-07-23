# QForge UI Design System

This document is the visual baseline for QForge. Use it whenever a screen,
dialog, component, or theme is designed or changed. The intent is a calm,
professional database tool: dense enough for technical work, but never harsh,
muddy, or visually noisy.

## Design direction

- **Tone:** focused, calm, capable, and desktop-native.
- **Visual hierarchy:** distinguish app chrome, navigation, workspace, and
  interactive controls through subtle surface changes—not heavy borders.
- **Accent discipline:** blue means primary action, keyboard focus, or current
  selection. It must not become the default colour of every control.
- **Safety cues:** Local, Staging, and Production use persistent text labels
  plus semantic colour. Colour alone must never communicate risk.
- **Density:** compact controls and tables are appropriate, but preserve clear
  spacing between unrelated groups.

## Palette

### Dark — graphite blue

| Token | Hex | Use |
| --- | --- | --- |
| App background | `#17191E` | Window margins and outer chrome |
| Sidebar | `#1E2128` | Navigation and object browser |
| Workspace | `#20232A` | SQL editor and main canvas |
| Raised surface | `#292D36` | Tabs, toolbars, menus, inputs |
| Input surface | `#2D323C` | Text inputs and combos |
| Hover surface | `#323845` | Hovered controls and headers |
| Border | `#3A404C` | Separators, gridlines, quiet outlines |
| Strong border | `#56606F` | Hovered outlines only |
| Primary text | `#E7EAF0` | Main readable text |
| Secondary text | `#A9B0BD` | Supporting labels and inactive tabs |
| Muted text | `#737B89` | Hints, placeholders, disabled content |
| Primary blue | `#4F8CFF` | Run, selection, focus, active state |
| Blue hover | `#70A4FF` | Hovered primary controls |
| Blue pressed | `#326FD5` | Pressed primary controls |
| Selection | `#31578F` | Selected table cells and editor text |

### Light — soft paper

| Token | Hex | Use |
| --- | --- | --- |
| App background | `#F3F4F6` | Window margins—never use pure white here |
| Sidebar | `#E9ECF1` | Navigation and object browser |
| Workspace | `#FAFAFB` | SQL editor and main canvas |
| Raised surface | `#FFFFFF` | Inputs, menus, dialogs |
| Hover surface | `#DDE1E8` | Hovered navigation and tabs |
| Border | `#D8DCE3` | Separators and gridlines |
| Primary text | `#20242C` | Main readable text |
| Secondary text | `#687080` | Supporting labels |
| Muted text | `#8B93A1` | Hints and placeholders |
| Primary blue | `#2563EB` | Run, selection, focus, active state |
| Blue hover | `#1D4ED8` | Hovered primary controls |
| Blue pressed | `#1E40AF` | Pressed primary controls |
| Selection | `#DCEAFE` | Selected cells and editor text |

## Component rules

### Navigation and tabs

- Keep the sidebar one surface darker/lighter than the workspace.
- Use a two-pixel blue underline for the active tab; avoid filling every active
  tab with blue.
- Inactive tabs use secondary text. Hover uses a subtle raised surface.
- Keep connection labels concise: name, environment, then optional version.

### Inputs and buttons

- Inputs are raised slightly from their parent surface and use a quiet border.
- Show the blue focus border only while an input has keyboard focus.
- Use filled blue buttons only for the primary action in a local action group
  (for example **Run** or **Connect**).
- Secondary actions are neutral, bordered, or text buttons. Destructive actions
  must use an explicit danger treatment, never the primary blue.
- Keep radius consistent: 6 px for normal controls, 4–5 px for compact tools.

### SQL workspace and result grid

- The editor is the quietest large surface; it should not compete with code.
- Result-grid lines are low contrast. Headers are one raised surface above rows.
- Use selection blue for a selected row/cell, not large alternating bands.
- Keep empty states helpful and muted rather than making the grid look broken.

### Environment safety indicators

| Environment | Background | Text / border | Label |
| --- | --- | --- | --- |
| Local | `#163D2A` | `#71D69A` / `#2D8054` | `LOCAL` |
| Staging | `#4A3716` | `#F2C56B` / `#A96F10` | `STAGING` |
| Production | `#542228` | `#FFAAA8` / `#D96565` | `PRODUCTION DATABASE` |

Display the text label in the workspace and connection tab/window title. These
labels are safety aids; they do not replace database permissions or future
read-only enforcement.

## Accessibility and quality checks

- Preserve clear text contrast for primary and secondary text in both themes.
- Do not encode a state using colour only; include text, icon, or shape.
- Check focused, hovered, disabled, selected, and error states in both themes.
- Test large data grids, the Connection Manager, dialogs, and an empty query
  tab before accepting a palette change.
- Prefer shared `ThemeManager` rules over new hard-coded per-widget colours.

## Implementation notes

- The shared baseline lives in `ui/theme_manager.py`.
- Local component styles should reference this document's tokens and only exist
  when a component needs a truly distinct visual role.
- Before adding a new colour, first determine whether an existing semantic
  token already fits the use case.
