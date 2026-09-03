"""Command palette: search and run any app menu action by name (issue #232).

Reuses QuickSearchDialog's existing fuzzy-search popup instead of a new UI,
and reads commands directly from the already-built QMenuBar rather than a
separate, hand-maintained registry that could drift out of sync with the
actual menus.
"""
from ui.quick_search_dialog import QuickSearchDialog

# Confirmed by hand against a real running MainWindow, the hard way: this is
# not just "a stale Python wrapper" — PySide6 (6.11.1) can actually delete
# the underlying C++ QAction once every Python reference to the list that
# `QMenu.actions()`/`QMenuBar.actions()` returned goes away, even though
# that QAction has a real C++ parent (its QMenu) that should keep it alive
# on its own. The failure isn't limited to objects this module touched —
# `main.py`'s own permanently-held `self.theme_action` broke the same way,
# because it's backed by the same underlying C++ object one of this
# module's `.actions()` calls also touched and then let go out of scope.
# The only reliable fix: never let any `.actions()` result be collected,
# ever, for the life of the process. This list only ever grows — at most a
# few dozen small list objects per palette open, immaterial for a desktop
# app's lifetime, and far cheaper than risking a real menu action again.
_PERMANENT_KEEPALIVE = []


def _collect_actions(menu_bar):
    """{key: QAction} for every non-separator, leaf action under *menu_bar*.
    Disabled actions are included too (issue #246) — see
    show_command_palette() for how they're shown greyed out with a reason
    instead of omitted, as they were before. See _PERMANENT_KEEPALIVE above
    for why every `.actions()` list this touches is kept forever."""
    actions = {}
    top_actions = menu_bar.actions()
    _PERMANENT_KEEPALIVE.append(top_actions)
    for top_action in top_actions:
        menu = top_action.menu()
        if menu is None:
            continue
        category = top_action.text().replace("&", "").strip()
        menu_actions = menu.actions()
        _PERMANENT_KEEPALIVE.append(menu_actions)
        for act in menu_actions:
            if act.isSeparator():
                continue
            text = act.text().replace("&", "").strip()
            if not text:
                continue
            actions[f"{category}:{text}"] = act
    return actions


# Shown for a disabled action with no specific statusTip() of its own set
# (issue #246) — most disabled actions in this app are transient
# (no-op-if-no-connection lambdas that stay enabled) rather than truly
# disabled, so this generic fallback is expected to be rare in practice.
_GENERIC_DISABLED_REASON = "Currently unavailable"


def show_command_palette(menu_bar, parent=None):
    action_by_key = _collect_actions(menu_bar)
    if not action_by_key:
        return

    items = []
    for key, act in action_by_key.items():
        extra = {}
        # issue #245: show the action's own shortcut in the palette row,
        # if it has one — QAction already carries it, it just wasn't
        # passed through before.
        shortcut = act.shortcut().toString()
        if shortcut:
            extra["shortcut"] = shortcut
        # issue #246: a disabled action is still shown, greyed out, with a
        # reason — rather than silently omitted as before, which left no
        # way to tell "doesn't exist" from "unavailable right now".
        if not act.isEnabled():
            extra["disabled"] = True
            extra["reason"] = act.statusTip() or _GENERIC_DISABLED_REASON
        items.append(("command", key.split(":", 1)[1], key, 0, extra))

    # Show every command up front (grouped by menu, in menu order) rather
    # than an empty "type to search" prompt — otherwise there's no way to
    # browse what's available without already knowing what to type for.
    dialog = QuickSearchDialog(
        items, parent, recent_items=items,
        empty_state_label="All Commands", empty_state_limit=len(items),
    )
    dialog.setWindowTitle("Command Palette")
    dialog.search_input.setPlaceholderText("Type a command…")

    selected_payload = []

    def _capture(item_type, display_text, payload):
        selected_payload.append(payload)

    dialog.item_selected.connect(_capture)
    dialog.exec()

    if not selected_payload:
        return

    act = action_by_key.get(selected_payload[0])
    if act is not None:
        act.trigger()
