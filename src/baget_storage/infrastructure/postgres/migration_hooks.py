import io
import subprocess
import sys
import tokenize
from pathlib import Path

from alembic.autogenerate import comparators
from alembic.operations import ops
from alembic.script import write_hooks
from sqlalchemy import CheckConstraint


@comparators.dispatch_for("table")
def compare_check_constraints(context, changes, schema, table_name, reflected, metadata) -> None:
    if reflected is None or metadata is None:
        return
    previous = {
        str(item.name): item
        for item in reflected.constraints
        if isinstance(item, CheckConstraint) and item.name
    }
    desired = {
        str(item.name): item
        for item in metadata.constraints
        if isinstance(item, CheckConstraint) and item.name
    }
    for name in sorted(previous.keys() - desired.keys()):
        changes.ops.append(ops.DropConstraintOp.from_constraint(previous[name]))
    for name in sorted(desired.keys() - previous.keys()):
        changes.ops.append(ops.CreateCheckConstraintOp.from_constraint(desired[name]))


def include_extensions(context, revision, directives) -> None:
    for script in directives:
        if any(
            isinstance(operation, ops.CreateTableOp) and operation.table_name == "leases"
            for operation in script.upgrade_ops.ops
        ):
            script.upgrade_ops.ops.insert(
                0, ops.ExecuteSQLOp("CREATE EXTENSION IF NOT EXISTS btree_gist")
            )


@write_hooks.register("format_revision")
def format_revision(filename: str, options: dict[str, str]) -> None:
    path = Path(filename)
    tokens = tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline)
    source = tokenize.untokenize(token for token in tokens if token.type != tokenize.COMMENT)
    path.write_text(source, encoding="utf-8")
    subprocess.run([sys.executable, "-m", "ruff", "format", filename], check=True)
    subprocess.run([sys.executable, "-m", "ruff", "check", "--fix", filename], check=True)
