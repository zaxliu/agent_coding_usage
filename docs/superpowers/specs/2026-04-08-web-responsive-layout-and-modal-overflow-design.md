# Web Responsive Layout And Modal Overflow

## Summary

The current web console works at large desktop widths, but its layout degrades when the browser window is reduced from maximized size.

Observed failures:

- the two-column shell keeps a fixed-width sidebar, so the main column loses usable space too quickly
- dashboard grids keep multi-column layouts beyond the point where their cards can fit
- settings forms keep two columns in narrow widths and overflow their containers
- runtime credential dialogs fit by viewport width, but their internal content does not consistently wrap, so users must horizontally scroll to read or submit them

This change makes the existing single-page console responsive without changing its data flow, actions, or modal behavior.

## Goals

- keep the page usable at non-maximized desktop widths without page-level horizontal scrolling
- ensure each major panel remains visible by stacking sections earlier instead of letting them clip or compress unpredictably
- make credential and edit dialogs readable and operable in narrow windows
- preserve local horizontal scrolling only for truly wide content such as the results table

## Non-Goals

- redesigning the information architecture
- changing the backend API or job flow
- replacing dialogs with inline panels
- introducing mobile-specific navigation patterns beyond layout stacking

## Recommended Approach

### Option A: Responsive stacking with local overflow exceptions

Shift the page from a fixed two-column shell to a responsive shell with breakpoints.

Behavior:

- wide screens keep the sidebar and main column layout
- medium screens reduce padding and collapse four-column and two-column grids into fewer columns
- narrow screens stack sidebar above main content
- dialogs clamp to viewport width and height, with form content switching to one column
- tables remain horizontally scrollable inside their own container

Pros:

- minimal behavioral risk
- addresses all reported issues directly
- aligns with the current layout and component model

Cons:

- the sidebar becomes longer vertically on smaller screens
- some above-the-fold density is reduced in exchange for reliability

### Option B: Keep desktop shell, add scrolling containers everywhere

Preserve the current layout and add more overflow handling to panels and dialogs.

Pros:

- smaller code diff in the shell

Cons:

- users still lose context because clipped content remains split across multiple scroll regions
- does not solve the root issue of fixed-width layout decisions

### Option C: Convert the whole page into a single-column flow at all widths

Remove the persistent sidebar concept and make everything a single vertical document.

Pros:

- simplest responsive model

Cons:

- unnecessary regression at desktop widths
- throws away the current console hierarchy

Recommendation: Option A.

It fixes the structural problem instead of patching symptoms, while keeping the current desktop layout intact where it already works.

## Design

### Shell Layout

The console shell stays two-column by default, but its left column must stop behaving like a hard reservation at smaller widths.

Rules:

- at large widths, keep the existing sidebar plus main column composition
- at medium widths, reduce shell padding and allow the content area to reclaim space
- at narrow widths, switch to a single-column layout with the sidebar rendered before the main content

Implementation direction:

- replace rigid width assumptions with breakpoint-based `grid-template-columns`
- ensure grid and flex children that contain text or controls use `min-width: 0`
- keep cards and panels inside the normal document flow instead of clipping them with page-level overflow rules

### Internal Grids

Summary cards, comparison panels, and settings forms need independent responsive behavior.

Rules:

- summary cards scale from four columns to two, then to one
- comparison panels scale from two columns to one
- settings forms scale from two columns to one
- panel headers and action rows wrap rather than forcing siblings off-screen

This prevents the common failure where one over-wide child causes an entire panel to disappear outside the viewport.

### Dialogs

Credential, remote edit, and Feishu target edit dialogs share the same responsive contract.

Rules:

- dialogs must never exceed the viewport width or height
- the dialog body must allow vertical scrolling when content is tall
- form grids inside dialogs must collapse to one column at narrow widths
- action buttons must wrap cleanly
- long explanatory copy and labels must wrap instead of widening the dialog

For the runtime credential prompt specifically, the user should always be able to read the prompt and reach the submit button without horizontal scrolling.

### Wide Content

Only intentionally wide content may scroll horizontally.

Rules:

- the results table keeps a local scroll container
- chart SVG stays fluid within its card and must not force page overflow
- no modal or page shell should rely on horizontal scrolling for core interaction

## Testing

### Browser verification

Use Chrome MCP for browser-based verification after implementation.

Verify at representative widths in Chrome:

- maximized desktop
- medium non-maximized desktop around the point where the bug currently appears
- narrow desktop width where the sidebar stacks

Check:

- all summary cards remain visible
- settings sections wrap instead of clipping
- credential modal shows full prompt and actions without horizontal scrolling
- remote and Feishu edit dialogs remain usable
- only the results table uses horizontal scrolling when needed

### Automated verification

Add or update frontend tests that assert the presence of responsive layout hooks in CSS.

Suitable checks:

- shell breakpoint rules exist for `.console-layout`
- responsive grid behavior exists for summary, comparison, and form layouts
- dialog styles include viewport-based width constraints and internal overflow handling

### Chrome MCP verification flow

Use Chrome MCP to verify the implemented UI in a running browser session.

Required checks:

- open the web console in Chrome
- resize to the representative desktop widths
- confirm the page shell does not introduce page-level horizontal scrolling
- trigger the runtime credential prompt and verify the full prompt, input, and actions remain visible without horizontal dragging
- open the remote edit and Feishu target edit dialogs and verify the same responsive behavior
- confirm the results table, not the page shell, owns horizontal overflow when content is wide

## Risks

- tightening dialog width may expose labels or values that need better wrapping
- stacking the sidebar earlier may slightly change perceived information priority at medium widths
- CSS-only fixes may reveal places where markup needs a small structural adjustment for wrapping to behave correctly

## Acceptance Criteria

- the page does not require horizontal scrolling when the browser is reduced from maximized desktop size, except within the results table container
- summary cards, settings panels, and comparison panels remain visible and readable across the supported width range
- the runtime password prompt is fully readable and actionable without horizontal dragging
- remote edit and Feishu target edit dialogs follow the same responsive behavior
- final verification is performed with Chrome MCP at the representative desktop widths defined above
