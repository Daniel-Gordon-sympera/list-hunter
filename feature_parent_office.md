# Feature: Parent Office Website URL Filling
This feature fill in the 'firm_website_url' column if it is empty.

## Introduction
Currently, the final CSV file contains a column for 'firm_website_url' which may be empty for some entries (most of the cases). 
In some of this cases, the 'firm_website_url' can be filled in by looking at the parent office of the law firm. 

### Example
For the uuid '002d928f-b2e5-4fef-9659-f7be49f467bf', the 'firm_website_url' is empty. 
However, by looking at the html @\data\queens_ny\html\002d928f-b2e5-4fef-9659-f7be49f467bf.html we can see that the 'the-law-office-of-sofia-balile-esq' is the parent office and the 'firm_website_url' can be filled in as follows: "https://profiles.superlawyers.com/new-york/brooklyn/lawfirm/the-law-office-of-sofia-balile-esq/bc2cf36e-fc72-410c-a2ed-c3e50dd09278.html"

This is the html snippet that contains the parent office information:
'''
<a class="profile-profile-header d-none d-xl-block paragraph-large-xl mb-0 mb-xl-3" href="https://profiles.superlawyers.com/new-york/brooklyn/lawfirm/the-law-office-of-sofia-balile-esq/bc2cf36e-fc72-410c-a2ed-c3e50dd09278.html" title="Super Lawyers Profile page of Firm The Law Office of Sofia Balile, Esq.">The Law Office of Sofia Balile, Esq.</a>
'''

## Requirements
- work on new git branch name 'feature/parent-office-web'
- fill in the 'firm_website_url' column for records with empty values by looking at the parent office information in the corresponding HTML files
- ensure that the data in the final CSV file is accurate and consistent with the information found in the HTML files
- test the implementation to verify that the new feature works as expected and does not introduce any bugs or issues in the existing codebase
- document the changes made to the codebase and update any relevant documentation to reflect the new feature

## Implementation

**Completed:** 2026-02-16
**Branch:** `feature/parent-office-web`
**Change:** `parsers/profile_parser.py` — `_extract_firm_website()` now falls back to the `a[href*="/lawfirm/"]` parent office link when no "Visit website" anchor is found.
**Tests:** `tests/test_profile_parser.py` — two new tests in `TestSafeExtraction`.
