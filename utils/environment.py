"""Connection environment tiers — shared by the connection dialog, workspace,
and theme layer so the tier list, labels, and normalization logic exist in
exactly one place. See ai/load-context.md (Slice 1) and ai/ui-design.md
("Environment safety indicators") for the design this implements."""

UNCLASSIFIED = "unclassified"
LOCAL = "local"
DEVELOPMENT = "development"
STAGING = "staging"
PRODUCTION = "production"

ENVIRONMENTS = [UNCLASSIFIED, LOCAL, DEVELOPMENT, STAGING, PRODUCTION]
DEFAULT_ENVIRONMENT = UNCLASSIFIED

# Title-case text for the QComboBox in the connection form.
COMBO_LABELS = {
    UNCLASSIFIED: "Unclassified",
    LOCAL: "Local",
    DEVELOPMENT: "Development",
    STAGING: "Staging",
    PRODUCTION: "Production",
}

# All-caps persistent label shown in the connection tree, tab text, window
# title, and workspace badge. Production is deliberately more emphatic text,
# not just a color (ai/ui-design.md: "strong, accessible visual treatment").
BADGE_LABELS = {
    UNCLASSIFIED: "ENVIRONMENT NOT SET",
    LOCAL: "LOCAL",
    DEVELOPMENT: "DEVELOPMENT",
    STAGING: "STAGING",
    PRODUCTION: "PRODUCTION DATABASE",
}


def normalize(value) -> str:
    """Coerce any stored/legacy value to a known tier; unrecognized,
    missing, or wrong-typed values (issue #116: a hand-edited or malicious
    connections.json could put anything here) become 'unclassified' rather
    than guessed or crashing on `.strip()`."""
    if not isinstance(value, str):
        return DEFAULT_ENVIRONMENT
    value = value.strip().lower()
    return value if value in ENVIRONMENTS else DEFAULT_ENVIRONMENT
