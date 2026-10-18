# Workday

Read `_common.md` first. Workday never auto-submits: fill everything, then stop `READY`.

- Each company runs its own tenant with its own account. Sign in with `account` credentials. If none exists,
  create one, verify by email with `get_verification_code`, then use "Use My Last Application" on later roles.
- Decline the consent banner first: `[data-automation-id="legalNoticeDeclineButton"]`. While it is up, dropdowns look
  empty and buttons do nothing.
- Prefer "Autofill with Resume", then fix: name in ALL CAPS, phone with a duplicated country code, missing
  company or location on jobs, empty school or degree, duplicated education entries.
- Commit buttons (Sign In, Create Account, Save and Continue): click the
  `[data-automation-id="click_filter"]` overlay, not the visible `aria-hidden` button.
- Stuck comboboxes: dispatch a full `pointerdown/mousedown/pointerup/mouseup/click` sequence at the element.
- Fields look filled but "Save and Continue" does nothing: retype them with `browser_type` and `slowly: true`.
- Broken Apply button: open `.../en-US/{tenant}/job/.../apply/applyManually` directly (the `/en-US/` prefix matters).
- School search: type the name, then press Enter to trigger the server search.
- "How Did You Hear About Us?" is a cascaded dropdown: open it, click a category, wait for the sub-options, click a
  sub-option, press Escape. A category alone is not accepted.
- "Select One" buttons: click, wait ~400ms, click the option, press Escape, re-query the remaining buttons.
- Disability self-identification "checkboxes" are radio-like: `.click()` the right one, not `.check()`.
- If "Engineering Degree" is not an option, choose the closest true equivalent.

```javascript
await page.getByRole('button', { name: /Use My Last Application/ }).click();
await page.waitForTimeout(2000);
for (const btn of await page.getByRole('button', { name: /Select One/ }).all()) {
  await btn.click();
  await page.waitForTimeout(400);
  // pick the answer from answers.json, then:
  await page.keyboard.press('Escape');
}
```

Genuine dead end only after all of the above: the "How Did You Hear" list is empty, a selection reverts to
"0 items selected" on blur, or Save and Continue fires no network request (`browser_network_requests`). Then
`VERIFY|url|workday form cannot be completed`.
