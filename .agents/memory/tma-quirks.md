---
name: Telegram Mini App quirks
description: Behaviors specific to Telegram WebApp environment that differ from regular browsers
---

## Rule
Never use `window.prompt()`, `window.confirm()`, or `window.alert()` in Telegram Mini App code.

**Why:** Telegram's WebApp sandboxed webview blocks these native browser dialogs — they return `null`/`false` silently without showing any UI. This causes logic to break silently (e.g., `parseInt(null)` = NaN, which crashes backend int() conversion → server error → "Помилка мережі").

**How to apply:**
- Replace `prompt()` with `showInputModal(title, placeholder, onSubmit)` helper
- Replace double `prompt()` with `showDoubleInputModal(title, p1, p2, onSubmit)` helper  
- Replace `confirm()` with `showConfirmModal(text, onConfirm)` helper
- These helpers are defined at the bottom of miniapp.html and create real DOM modals
- The `onSubmit` callback returns `false` to keep modal open on validation error, or `true`/undefined to close
