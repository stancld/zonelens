#!/bin/bash

# You can generate cert files with:
# ```bash
# openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -sha256 -days 365 -nodes
# ```
python manage.py runserver_plus --cert-file cert.pem --key-file key.pem
