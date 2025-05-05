#!/usr/bin/env bash
set -ex

PYTHON_VERSION=${PYTHON_VERSION:-3.12}
PYTHON_DIR=${PYTHON_DIR:-/opt/python}

# Create a virtual environment with desired python version
uv venv --python $PYTHON_VERSION $PYTHON_DIR

uv pip install --upgrade --no-cache-dir setuptools wheel toml
