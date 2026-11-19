"""Telemetry adapters — the only components that know a raw source format.

Adding a telemetry layer means writing an adapter here and nothing else: the graph builder derives
relations from the ``(actor_type, action, target_type)`` triple an adapter fills in, so correctly
typed events automatically become correctly typed graph edges (docs/DESIGN.md §8.3).
"""

from graphguard.ingestion.ai_telemetry import AITelemetryAdapter
from graphguard.ingestion.base import Adapter, AdapterError, IngestionReport
from graphguard.ingestion.http_gateway import HTTPGatewayAdapter, normalise_path

#: Adapters by the name used in ``config/default.yaml`` under ``ingestion.adapters``.
ADAPTERS: dict[str, type[Adapter]] = {
    AITelemetryAdapter.name: AITelemetryAdapter,
    HTTPGatewayAdapter.name: HTTPGatewayAdapter,
}


def build_adapter(name: str, *, strict: bool = True) -> Adapter:
    """Construct a registered adapter by name.

    Raises
    ------
    ValueError
        On an unknown name, listing what is available — a typo in a config file must not silently
        ingest nothing.

    """
    try:
        adapter_class = ADAPTERS[name]
    except KeyError as exc:
        raise ValueError(f"unknown adapter {name!r}; available: {sorted(ADAPTERS)}") from exc
    return adapter_class(strict=strict)


__all__ = [
    "ADAPTERS",
    "Adapter",
    "AITelemetryAdapter",
    "AdapterError",
    "HTTPGatewayAdapter",
    "IngestionReport",
    "build_adapter",
    "normalise_path",
]
