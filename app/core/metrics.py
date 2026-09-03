from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator


def setup_metrics(app: FastAPI) -> None:
    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        should_respect_env_var=False,
        excluded_handlers=[
            "/metrics",
            "/metrics/business",
        ],
    ).instrument(app).expose(
        app,
        endpoint="/metrics",
        include_in_schema=False,
    )