FROM python:3.10-slim

SHELL ["/bin/bash", "-c"]

RUN pip install poetry

WORKDIR /src

COPY pyproject.toml poetry.lock README.rst ./

RUN pip install -r <(poetry export)

COPY ./virtual_site.py ./virtual_site.py

RUN pip install .

ENV PYTHONUNBUFFERED=true

ENTRYPOINT ["virtual-site"]
