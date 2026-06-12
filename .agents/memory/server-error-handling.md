---
name: Server error handling pattern
description: How do_GET/do_POST are protected against unhandled exceptions
---

## Rule
The HTTP handler class wraps `do_GET` and `do_POST` with a try/except that delegates to `_do_GET_inner` and `_do_POST_inner`. Any unhandled exception returns a 500 JSON response instead of crashing the connection (which would cause "Помилка мережі" on the client).

**Why:** Python's BaseHTTPRequestHandler doesn't catch exceptions in do_GET/do_POST. An unhandled exception (e.g., `int("NaN")` from a bad query param) causes the connection to drop, and `fetch(...).json()` throws, which the frontend catches as "Помилка мережі".

**How to apply:**
- Any new `int()` conversion from user input should use `try/except` or a helper
- The global wrapper in do_GET/do_POST is a safety net, not a replacement for specific input validation
- `api()` in the frontend catches JSON parse errors separately from network errors for better debugging
