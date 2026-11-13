import hashlib
import json
import tempfile
from pathlib import Path

from django.apps import apps
from django.core import serializers
from django.core.management import BaseCommand, CommandError, call_command
from django.db import connections, transaction

from network.models import DataImportReceipt


MODEL_LABELS = {
    "contenttypes.contenttype",
    "auth.permission",
    "auth.group",
    "auth.user",
    "network.profile",
    "network.post",
    "network.comment",
    "network.like",
    "network.follow",
}

FOREIGN_KEYS = {
    ("auth.permission", "content_type"): ("contenttypes.contenttype", False),
    ("auth.group", "permissions"): ("auth.permission", True),
    ("auth.user", "groups"): ("auth.group", True),
    ("auth.user", "user_permissions"): ("auth.permission", True),
    ("network.profile", "user"): ("auth.user", False),
    ("network.post", "author"): ("auth.user", False),
    ("network.comment", "post"): ("network.post", False),
    ("network.comment", "author"): ("auth.user", False),
    ("network.like", "post"): ("network.post", False),
    ("network.like", "user"): ("auth.user", False),
    ("network.follow", "follower"): ("auth.user", False),
    ("network.follow", "following"): ("auth.user", False),
}

UNIQUE_FIELDS = {
    "auth.user": (("username",),),
    "auth.group": (("name",),),
    "contenttypes.contenttype": (("app_label", "model"),),
    "auth.permission": (("content_type", "codename"),),
}


class Command(BaseCommand):
    help = (
        "Safely import an auth/network JSON fixture into the selected database. "
        "Existing identical rows are skipped."
    )

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, help="Private dumpdata JSON fixture.")
        parser.add_argument(
            "--database", default="default", help="Target database alias (default: default)."
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate the fixture and report changes without importing rows.",
        )

    def handle(self, *args, **options):
        source_path = Path(options["source"]).expanduser().resolve()
        database = options["database"]
        if database not in connections:
            raise CommandError(f"Unknown target database alias: {database}")
        if not source_path.is_file():
            raise CommandError("The source fixture file does not exist.")

        try:
            raw_bytes = source_path.read_bytes()
            fixture = json.loads(raw_bytes)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CommandError("Could not read a valid UTF-8 JSON fixture.") from exc
        if not isinstance(fixture, list) or not fixture:
            raise CommandError("The source fixture must contain a non-empty JSON list.")

        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        objects = self._validate_fixture(fixture)
        counts = self._counts(objects)

        with transaction.atomic(using=database):
            receipt = (
                DataImportReceipt.objects.using(database)
                .filter(source_sha256=sha256)
                .first()
            )
            if receipt:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"This fixture was already imported at "
                        f"{receipt.imported_at:%Y-%m-%d %H:%M:%S %Z}; no rows changed."
                    )
                )
                return

            pending = self._find_pending(objects, database)
            self._validate_references(objects, database)
            self._validate_unique_values(pending, database)
            pending_counts = self._counts(pending)
            if options["dry_run"]:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Dry run valid. {sum(pending_counts.values())} rows would be "
                        f"imported; {len(objects) - len(pending)} identical rows "
                        "would be skipped."
                    )
                )
                self.stdout.write(f"Rows by model: {pending_counts}")
                return

            self._load_objects(pending, database)
            DataImportReceipt.objects.using(database).create(
                source_sha256=sha256, imported_counts=counts
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Import complete. Imported {sum(pending_counts.values())} rows; "
                f"skipped {len(objects) - len(pending)} identical rows."
            )
        )
        self.stdout.write(f"Rows by model: {pending_counts}")

    def _validate_fixture(self, fixture):
        objects = []
        seen_pks = {}
        for index, item in enumerate(fixture):
            if not isinstance(item, dict):
                raise CommandError(f"Fixture record {index + 1} is not an object.")
            label = str(item.get("model", "")).lower()
            if label not in MODEL_LABELS:
                raise CommandError(
                    f"Unsupported fixture model at record {index + 1}: {label or '(missing)'}."
                )
            if "pk" not in item or not isinstance(item.get("fields"), dict):
                raise CommandError(f"Fixture record {index + 1} has no primary key or fields.")
            pk = str(item["pk"])
            model_pks = seen_pks.setdefault(label, set())
            if pk in model_pks:
                raise CommandError(f"Fixture contains duplicate {label} primary key {pk}.")
            model_pks.add(pk)
            model = apps.get_model(label)
            if not model:
                raise CommandError(f"Fixture references an unavailable model: {label}.")
            obj = {"label": label, "pk": item["pk"], "fields": item["fields"], "model": model}
            objects.append(obj)
        return objects

    def _find_pending(self, objects, database):
        pending = []
        for item in objects:
            model = item["model"]
            existing = model.objects.using(database).filter(pk=item["pk"]).first()
            if existing is None:
                pending.append(item)
                continue
            existing_data = json.loads(serializers.serialize("json", [existing]))[0]
            if self._canonical(existing_data["fields"]) != self._canonical(item["fields"]):
                raise CommandError(
                    f"Target database has conflicting data for "
                    f"{item['label']} primary key {item['pk']}; no rows were imported."
                )
        return pending

    def _validate_references(self, objects, database):
        fixture_pks = {}
        for item in objects:
            fixture_pks.setdefault(item["label"], set()).add(str(item["pk"]))

        for item in objects:
            for field_name, (target_label, many) in FOREIGN_KEYS.items():
                if item["label"] != field_name[0] or field_name[1] not in item["fields"]:
                    continue
                referenced = item["fields"][field_name[1]]
                if referenced is None:
                    continue
                references = referenced if many else [referenced]
                target_model = apps.get_model(target_label)
                for reference in references:
                    if str(reference) in fixture_pks.get(target_label, set()):
                        continue
                    if target_model.objects.using(database).filter(pk=reference).exists():
                        continue
                    raise CommandError(
                        f"Fixture has a missing {target_label} reference in "
                        f"{item['label']} primary key {item['pk']}."
                    )

    def _validate_unique_values(self, objects, database):
        seen = {}
        for item in objects:
            for fields in UNIQUE_FIELDS.get(item["label"], ()):
                values = tuple(item["fields"].get(field) for field in fields)
                if any(value is None for value in values):
                    continue
                key = (item["label"], fields, self._canonical(values))
                if key in seen:
                    raise CommandError(
                        f"Fixture contains duplicate {item['label']} values for "
                        f"{', '.join(fields)}."
                    )
                seen[key] = item
                model = item["model"]
                query = {}
                for field, value in zip(fields, values):
                    if field == "content_type":
                        query["content_type_id"] = value
                    else:
                        query[field] = value
                collision = model.objects.using(database).filter(**query).exclude(
                    pk=item["pk"]
                )
                if collision.exists():
                    raise CommandError(
                        f"Target database already uses a {item['label']} value for "
                        f"{', '.join(fields)}; no rows were imported."
                    )

    def _load_objects(self, objects, database):
        if not objects:
            return
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".json", delete=False
            ) as fixture_file:
                temporary_path = Path(fixture_file.name)
                json.dump(
                    [
                        {
                            "model": item["label"],
                            "pk": item["pk"],
                            "fields": item["fields"],
                        }
                        for item in objects
                    ],
                    fixture_file,
                )
            call_command(
                "loaddata",
                str(temporary_path),
                database=database,
                verbosity=0,
            )
        finally:
            if temporary_path:
                temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _canonical(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

    @staticmethod
    def _counts(objects):
        counts = {}
        for item in objects:
            counts[item["label"]] = counts.get(item["label"], 0) + 1
        return counts
