# Import payload schema

STATUS: IMPLEMENTED (validated by `ManualImportProvider.validate_input`; `tests/test_discovery.py`, `tests/test_api.py`)

`POST /api/v1/investigations/{id}/import`

```json
{
  "batch_id": "optional string",
  "authorization_note": "REQUIRED: who authorized this data / where it came from",
  "records": [
    {
      "url": "https://x.com/example_handle",
      "display_name": "optional",
      "bio": "optional, max 20000 chars",
      "text": "optional, max 20000 chars",
      "links": ["https://example.org"],
      "published_at": "ISO-8601 optional",
      "observed_at": "ISO-8601 optional",
      "external_id": "optional"
    }
  ]
}
```

- `records`: 1 to 500 items. Each needs `url`; its type is decided deterministically from the URL.
- `authorization_note` must be non-empty. It is stored on the tool run.
- All data is stored as unverified observations from provider `manual_import`.
- Invalid timestamps or oversize text return 422.
