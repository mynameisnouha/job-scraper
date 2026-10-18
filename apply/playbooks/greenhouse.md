# Greenhouse

Read `_common.md` first. Greenhouse may auto-submit when job.json `auto_submit` is true.

- A direct embed URL avoids iframe issues: `https://job-boards.greenhouse.io/embed/job_app?for={company}&token={job_id}`.
  Use it if the board page embeds the form in an iframe.
- Use `{ exact: true }` for "First Name" (there is often a "Preferred First Name").
- Second education entry ids use a double dash: `#school--1`, `#degree--1`, `#end-month--1`.
- EEO comboboxes may lack labels: read their options and prefer "Prefer not to answer".
- Look for certification and consent checkboxes at the bottom.
- File choosers block scripts: click Attach as the last action of a `browser_run_code` call, then
  `browser_file_upload`. Verify the filename appears. Resume and cover letter are separate slots; apply the cover
  letter rule from `_common.md` to the second.
- Some boards email a security code after submit. Fetch it with `get_verification_code` and enter it.

```javascript
await page.getByRole('textbox', { name: 'First Name', exact: true }).fill(first);
await page.getByRole('textbox', { name: 'Last Name' }).fill(last);
await page.getByRole('textbox', { name: 'Email' }).fill(email);
await page.getByRole('textbox', { name: 'Phone' }).fill(phone);
```

Values come from `answers.json`. Success is a "Thank you for applying" page or message.
