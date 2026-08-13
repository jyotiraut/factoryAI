"""CLI bridge: runs one ``pipeline_client`` call to completion, prints JSON to stdout.

Exists for exactly one caller: Airflow DAG tasks (`pipelines/airflow/dags/common.py`),
which cannot import `factoryai` in Airflow's own Python process — every Airflow 2.x release
pins `SQLAlchemy==1.4.54` in its published constraints file, and this platform requires
`sqlalchemy>=2.0` (see ADR-0013's "Consequences", discovered by actually trying to build the
image, not predicted in the abstract). `airflow.Dockerfile` installs `factoryai` into a
*separate* virtualenv baked into the same image instead; DAG tasks shell out to that
interpreter running this module, rather than importing `factoryai` directly.

Exit codes carry outcomes a subprocess boundary would otherwise flatten to "some error, see
stderr": ``0`` success (JSON result on stdout), ``3`` a business rejection
(:class:`~factoryai.domain.errors.PromotionRejectedError`), ``4`` a use case that raised
:class:`NotImplementedError` — no current caller does, but the code is kept reserved for
the next deliberate scope cut rather than reused for something else. Anything else is a
genuine failure — stderr carries the traceback, and the caller's ``subprocess.run(check=
True)`` raises.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable, Coroutine
from typing import Any

from factoryai import pipeline_client
from factoryai.bootstrap.container import Container, build_container
from factoryai.domain.errors import PromotionRejectedError
from factoryai.shared.config import get_settings

_EXIT_REJECTED = 3
_EXIT_NOT_IMPLEMENTED = 4


def _container() -> Container:
    return build_container(get_settings())


async def _ingest(args: argparse.Namespace) -> dict[str, Any]:
    return await pipeline_client.ingest_from_object_store(
        _container(), category=args.category, prefix=args.prefix, label=args.label
    )


async def _staged_images_exist(args: argparse.Namespace) -> dict[str, Any]:
    container = _container()
    async for _ in container.object_store.list_keys(
        container.settings.storage.bucket_raw, prefix=args.prefix
    ):
        return {"exists": True}
    return {"exists": False}


async def _version_dataset(args: argparse.Namespace) -> dict[str, Any]:
    return await pipeline_client.version_dataset(_container(), json.loads(args.payload))


async def _train(args: argparse.Namespace) -> dict[str, Any]:
    return await pipeline_client.train(_container(), json.loads(args.payload))


async def _evaluate(args: argparse.Namespace) -> dict[str, Any]:
    return await pipeline_client.evaluate(_container(), model_version_id=args.model_version_id)


async def _deploy(args: argparse.Namespace) -> dict[str, Any]:
    return await pipeline_client.deploy(
        _container(),
        category=args.category,
        model_version_id=args.model_version_id,
        reason=args.reason,
    )


async def _drift_report(args: argparse.Namespace) -> dict[str, Any]:
    return await pipeline_client.generate_drift_report(_container(), json.loads(args.payload))


_COMMANDS: dict[str, Callable[[argparse.Namespace], Coroutine[Any, Any, dict[str, Any]]]] = {
    "ingest": _ingest,
    "staged-images-exist": _staged_images_exist,
    "version-dataset": _version_dataset,
    "train": _train,
    "evaluate": _evaluate,
    "deploy": _deploy,
    "drift-report": _drift_report,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factoryai-pipeline-runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest")
    ingest.add_argument("--category", required=True)
    ingest.add_argument("--prefix", required=True)
    ingest.add_argument("--label", default="unlabeled")

    staged = subparsers.add_parser("staged-images-exist")
    staged.add_argument("--prefix", required=True)

    for name in ("version-dataset", "train", "drift-report"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--payload", required=True, help="JSON-encoded payload dict")

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--model-version-id", required=True, dest="model_version_id")

    deploy = subparsers.add_parser("deploy")
    deploy.add_argument("--category", required=True)
    deploy.add_argument("--model-version-id", required=True, dest="model_version_id")
    deploy.add_argument("--reason", default="")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one subcommand and print its JSON result to stdout.

    Returns:
        The process exit code — see the module docstring for what each value means.
    """
    args = _build_parser().parse_args(argv)
    command = _COMMANDS[args.command]
    try:
        result: dict[str, Any] = asyncio.run(command(args))
    except PromotionRejectedError as exc:
        json.dump({"error": "promotion_rejected", "message": exc.message}, sys.stdout)
        return _EXIT_REJECTED
    except NotImplementedError as exc:
        json.dump({"error": "not_implemented", "message": str(exc)}, sys.stdout)
        return _EXIT_NOT_IMPLEMENTED
    json.dump(result, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
