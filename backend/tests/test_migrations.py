from django.apps import apps
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ProjectState


def test_initial_schema_matches_model_definitions():
    loader = MigrationLoader(None)
    changes = MigrationAutodetector(loader.project_state(), ProjectState.from_apps(apps)).changes(
        graph=loader.graph
    )
    assert not changes, f"Unmigrated model changes: {list(changes)}"
