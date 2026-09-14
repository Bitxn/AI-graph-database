# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""Generate a styled PDF handbook for a codebase."""
from oneport_context.pdf.builder import make_pdf
from oneport_context.pdf.server import serve_pdf

__all__ = ["make_pdf", "serve_pdf"]
