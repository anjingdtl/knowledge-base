# API Reference Guide

## Authentication

All API requests require a Bearer token. Obtain a token by calling POST /api/auth/login with username and password.

## Search Endpoint

POST /api/search accepts a JSON body with fields: query (string), top_k (integer, default 5), and tags (optional list).

The response includes an array of results, each containing: block_id, knowledge_id, title, text, score, and match_channels.

## Indexing Endpoint

POST /api/index accepts a file upload (multipart/form-data) or a path parameter for local files.

Supported formats: PDF, DOCX, XLSX, PPTX, Markdown, plain text, and common code files.
