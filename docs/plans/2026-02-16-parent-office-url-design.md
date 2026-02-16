# Design: Parent Office URL Fallback for firm_website_url

**Date:** 2026-02-16
**Branch:** `feature/parent-office-web`
**Status:** Approved

## Problem

The `firm_website_url` field is empty for most attorney records. Many of these attorneys have a parent office link on their profile page pointing to the firm's SuperLawyers profile (`/lawfirm/` URL). This URL can serve as a fallback when no external website is available.

## Solution

Modify `_extract_firm_website()` in `parsers/profile_parser.py` to fall back to the parent office SuperLawyers URL when the "Visit website" link is absent.

### Current logic

1. Find `<a>` with text "Visit website" -> return href (query params stripped)
2. Not found -> return `""`

### New logic

1. Find `<a>` with text "Visit website" -> return href (query params stripped)
2. **Fallback:** Find `a[href*="/lawfirm/"]` -> return href
3. Neither found -> return `""`

### Parent office HTML structure

```html
<a class="profile-profile-header d-none d-xl-block paragraph-large-xl mb-0 mb-xl-3"
   href="https://profiles.superlawyers.com/new-york/brooklyn/lawfirm/the-law-office-of-sofia-balile-esq/bc2cf36e-fc72-410c-a2ed-c3e50dd09278.html"
   title="Super Lawyers Profile page of Firm The Law Office of Sofia Balile, Esq.">
  The Law Office of Sofia Balile, Esq.
</a>
```

Selector: `a[href*="/lawfirm/"]` (same as `_extract_firm_name()` at line 93).

## Scope

- **Changed:** `parsers/profile_parser.py` (`_extract_firm_website` method)
- **Unchanged:** `models.py`, `commands/export.py`, `commands/parse_profiles.py`, pipeline flow

## Testing

1. Profile with "Visit website" link -> external URL (existing behavior preserved)
2. Profile without "Visit website" but with `/lawfirm/` link -> parent office URL
3. Profile with neither -> empty string
