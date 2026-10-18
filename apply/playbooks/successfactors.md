# SAP SuccessFactors

Read `_common.md` first. This ATS never auto-submits: fill everything, then stop `READY`.

- There is a separate candidate profile per employer (`company=` URL parameter). Check attached documents on every
  new employer: old portals keep an earlier resume or cover letter. Replace anything that is not the current file.
- Sign in with `account` credentials, or register and verify by email with `get_verification_code`.
- Section toggles, edit pencils and even Apply buttons can be covered by a sticky header. Use a native DOM click:
  `page.evaluate(() => document.getElementById('<id>').click())`.
- The edit pencil creates an `input[type=file]`: use `setInputFiles` on it, then click Save. Verify the filename.
- A submit that does nothing often means an unticked consent checkbox lower down. Scroll the whole form.
- Some tenants reject PDFs with hyperlinks ("active content"). If the CV is refused, report
  `VERIFY|url|upload rejected as active content`.
- A real image reCAPTCHA on sign-in: `VERIFY|url|captcha`. A checkbox-only reCAPTCHA is fine.
- Required dropdowns rendered by select2 have zero options until opened. Open the widget (click
  `.select2-selection`), wait ~2s, then click `.select2-results__option`.
