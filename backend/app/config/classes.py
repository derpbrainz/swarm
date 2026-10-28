"""Read-only resolved views for existing class/enemy consumers."""

from backend.app.content import CATALOG

CHARACTER_CLASSES = CATALOG.classes
ENEMY_TYPES = CATALOG.definitions["enemies"]
