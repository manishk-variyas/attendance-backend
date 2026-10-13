import os
import logging
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from prometheus_fastapi_instrumentator import Instrumentator

logger = logging.getLogger(__name__)


def init_metrics(app):
    """
    Instrument all FastAPI routes for Prometheus metrics scraping.
    Exposes /metrics on the internal network.
    """
    instrumentator = Instrumentator(
        should_group_status_codes=False,
        should_ignore_untemplated=True,
        should_round_latency_decimals=True,
        round_latency_decimals=4,
        excluded_handlers=["/metrics", "/health"],
    )
    instrumentator.instrument(app)
    instrumentator.expose(app, endpoint="/metrics", include_in_schema=False)
    logger.info("Prometheus metrics initialized on /metrics")
    return instrumentator


def init_tracing(app, engine=None):
    """
    Initialize OpenTelemetry tracing if OTEL_EXPORTER_OTLP_ENDPOINT is configured.
    Instruments FastAPI routes and SQLAlchemy database queries.
    """
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not otlp_endpoint:
        return

    try:
        service_name = os.getenv("OTEL_SERVICE_NAME", "attendance-backend")
        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        insecure = os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "true").lower() == "true"
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=insecure)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=provider,
            excluded_urls="health,metrics",
        )

        if engine is not None:
            SQLAlchemyInstrumentor().instrument(
                engine=engine,
                tracer_provider=provider,
            )

        logger.info("OpenTelemetry tracing initialized -> %s", otlp_endpoint)
    except Exception as exc:
        logger.warning("Failed to initialize OpenTelemetry tracing: %s", exc)
