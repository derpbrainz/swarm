"""Read-only authored mission/segment views used by generation and persistence."""

from backend.app.content import CATALOG, RULES

CELL_SIZE = RULES["geometry"]["cell_size"]
CONNECTOR_OFFSET = RULES["geometry"]["connector_offset"]
CONNECTOR_WIDTH = RULES["geometry"]["connector_width"]
MISSIONS = CATALOG.definitions["missions"]
SEGMENT_TEMPLATES = CATALOG.definitions["segments"]
