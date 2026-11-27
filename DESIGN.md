---
version: alpha
name: Entertainment List
description: A Windows 98 desktop for a small group of friends' watch lists. Bevelled grey windows, navy title bars and one pixel font. Dark mode is the same machine at night, not a different product.
colors:
  primary: "#000080"
  title-end: "#1084d0"
  on-primary: "#ffffff"
  desktop: "#008080"
  face: "#c0c0c0"
  face-light: "#dfdfdf"
  highlight: "#ffffff"
  shadow: "#808080"
  edge: "#0a0a0a"
  text: "#000000"
  text-muted: "#4a4a4a"
  input-bg: "#ffffff"
  link: "#0000a0"
  error: "#990000"
  success: "#0d5c0d"
  focus: "#000080"
  primary-dark: "#000040"
  title-end-dark: "#0a5f9e"
  desktop-dark: "#01090c"
  face-dark: "#303030"
  face-light-dark: "#3f3f3f"
  highlight-dark: "#5c5c5c"
  shadow-dark: "#141414"
  edge-dark: "#000000"
  text-dark: "#eaeaea"
  text-muted-dark: "#b4b4b4"
  input-bg-dark: "#1b1b1b"
  link-dark: "#9cc4ff"
  error-dark: "#ff8a8a"
  success-dark: "#5fd35f"
  focus-dark: "#9cc4ff"
typography:
  title-bar:
    fontFamily: Pixelated MS Sans Serif
    fontSize: 12px
    fontWeight: 700
    lineHeight: 1.2
  heading:
    fontFamily: Pixelated MS Sans Serif
    fontSize: 18px
    fontWeight: 700
    lineHeight: 1.2
  body:
    fontFamily: Pixelated MS Sans Serif
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: Pixelated MS Sans Serif
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.45
  caption:
    fontFamily: Pixelated MS Sans Serif
    fontSize: 11px
    fontWeight: 400
    lineHeight: 1.4
  button-primary:
    fontFamily: Pixelated MS Sans Serif
    fontSize: 14px
    fontWeight: 700
    lineHeight: 1
  input-touch:
    fontFamily: Pixelated MS Sans Serif
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.2
rounded:
  none: 0px
spacing:
  hair: 2px
  xs: 4px
  sm: 6px
  md: 8px
  lg: 12px
  xl: 16px
  xxl: 20px
components:
  window:
    backgroundColor: "{colors.face}"
    textColor: "{colors.text}"
    rounded: "{rounded.none}"
    padding: 3px
  title-bar:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.title-bar}"
    height: 24px
  button:
    backgroundColor: "{colors.face}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.none}"
    height: 40px
  button-hover:
    backgroundColor: "{colors.face-light}"
    textColor: "{colors.text}"
  button-primary:
    backgroundColor: "{colors.face}"
    textColor: "{colors.text}"
    typography: "{typography.button-primary}"
    height: 44px
  field-input:
    backgroundColor: "{colors.input-bg}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.none}"
    height: 40px
  caption:
    backgroundColor: "{colors.face}"
    textColor: "{colors.text-muted}"
    typography: "{typography.caption}"
  link:
    backgroundColor: "{colors.face}"
    textColor: "{colors.link}"
  link-hover:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
  field-error:
    backgroundColor: "{colors.face}"
    textColor: "{colors.error}"
    typography: "{typography.caption}"
  status-ok:
    backgroundColor: "{colors.face}"
    textColor: "{colors.success}"
  alert:
    backgroundColor: "{colors.face-light}"
    textColor: "{colors.text}"
    padding: 10px
  status-bar-field:
    backgroundColor: "{colors.face}"
    textColor: "{colors.text}"
    typography: "{typography.caption}"
  focus-ring:
    backgroundColor: "{colors.face}"
    textColor: "{colors.focus}"
  bevel-highlight:
    backgroundColor: "{colors.highlight}"
  bevel-shadow:
    backgroundColor: "{colors.shadow}"
  bevel-edge:
    backgroundColor: "{colors.edge}"
  desktop:
    backgroundColor: "{colors.desktop}"
  title-bar-end:
    backgroundColor: "{colors.title-end}"
  window-dark:
    backgroundColor: "{colors.face-dark}"
    textColor: "{colors.text-dark}"
    rounded: "{rounded.none}"
    padding: 3px
  title-bar-dark:
    backgroundColor: "{colors.primary-dark}"
    textColor: "{colors.on-primary}"
    typography: "{typography.title-bar}"
    height: 24px
  title-bar-end-dark:
    backgroundColor: "{colors.title-end-dark}"
  button-dark:
    backgroundColor: "{colors.face-dark}"
    textColor: "{colors.text-dark}"
    typography: "{typography.body}"
    height: 40px
  button-hover-dark:
    backgroundColor: "{colors.face-light-dark}"
    textColor: "{colors.text-dark}"
  field-input-dark:
    backgroundColor: "{colors.input-bg-dark}"
    textColor: "{colors.text-dark}"
    typography: "{typography.body}"
    height: 40px
  caption-dark:
    backgroundColor: "{colors.face-dark}"
    textColor: "{colors.text-muted-dark}"
    typography: "{typography.caption}"
  caption-on-light-face-dark:
    backgroundColor: "{colors.face-light-dark}"
    textColor: "{colors.text-muted-dark}"
    typography: "{typography.caption}"
  link-dark:
    backgroundColor: "{colors.face-dark}"
    textColor: "{colors.link-dark}"
  field-error-dark:
    backgroundColor: "{colors.face-dark}"
    textColor: "{colors.error-dark}"
    typography: "{typography.caption}"
  status-ok-dark:
    backgroundColor: "{colors.face-dark}"
    textColor: "{colors.success-dark}"
  focus-ring-dark:
    backgroundColor: "{colors.face-dark}"
    textColor: "{colors.focus-dark}"
  bevel-highlight-dark:
    backgroundColor: "{colors.highlight-dark}"
  bevel-shadow-dark:
    backgroundColor: "{colors.shadow-dark}"
  bevel-edge-dark:
    backgroundColor: "{colors.edge-dark}"
  desktop-dark:
    backgroundColor: "{colors.desktop-dark}"
---

## Overview

The site is a Windows 98 desktop: every screen is a window with bevels and a navy title bar,
sitting on a wallpaper. The retro look is the point, so it is applied seriously: one pixel font,
square corners, light-from-top-left bevels, and no gradients except the title bar's.

**The sign-in page is the reference implementation.** It is the most recent and most carefully
built screen (`static/css/el/*.css` + `main.css` + `login.css`, no inline CSS, no third-party
requests). It passes every contrast check in both themes, and its touch targets are 48–54 px on
phones. Every other page should converge on it. Where an inner page and the sign-in page
disagree, the sign-in page is right unless this file says otherwise.

The theme follows the device (`prefers-color-scheme`). There is no toggle. Dark mode keeps the
same structure and only swaps the palette. Title bars stay navy, just darker.

`static/css/el/tokens.css` holds the tokens in code, for every page. The YAML above is the same palette, with dark values as
`*-dark` keys. When the two disagree, fix one in the same change.

## Colors

Light palette, with dark in brackets:

- **Face `#c0c0c0` (`#303030`):** every window, button and status bar. **Face light `#dfdfdf`
  (`#3f3f3f`):** hover and alert backgrounds.
- **Bevels:** highlight `#ffffff` (`#5c5c5c`), shadow `#808080` (`#141414`), edge `#0a0a0a`
  (`#000000`). Light comes from the top-left: highlight goes on the top and left edges,
  shadow/edge on the bottom and right. Pressed states swap them.
- **Title bar:** a gradient from primary `#000080` (`#000040`) to `#1084d0` (`#0a5f9e`), with white
  bold text. Put the title text at the left end, where contrast is highest (16:1 / 19.6:1). The
  right end is only 4.0:1 in light mode and carries no text.
- **Text `#000000` (`#eaeaea`)** on faces is 11.5:1 (11.0:1).
- **Muted `#4a4a4a` (`#b4b4b4`):** years, counts, dates, captions. 4.9:1 on face (6.4:1), and
  6.7:1 (5.1:1) on face light. Never lighter than this.
- **Link `#0000a0` (`#9cc4ff`).** Hover inverts it: white on navy (dark text on `#9cc4ff`).
- **Error `#990000` (`#ff8a8a`)**, **success `#0d5c0d` (`#5fd35f`).** These are for text. Use a
  status colour together with a word or an icon, never the colour alone.
- **Focus `#000080` (`#9cc4ff`):** a 2 px outline, offset 2 px, on `:focus-visible`.
- **Desktop `#008080` (`#01090c`):** shows behind the wallpaper, and in `theme-color` for light mode.

Rules:

- Every text/background pair is at least 4.5:1, or 3:1 for 18 px+ or bold 14 px+. Compute it.
  Hidden states (errors, hovers, disabled text) count too, so open them before measuring.
- Teal `#008080` / `#00a0a0` is a **fill** colour (progress bars, badges with white text). As text
  on a face it is 2.6:1 / 3.2:1.
- No colour is written inline in a template (`style="color:#700"`). A value that only exists in
  one template cannot follow the theme.

The sign-in page's error colour was `#a80000` (4.33:1 on the face, fails at 11 px) until it moved to
`el/tokens.css`, which has `#990000` (4.90:1).

## Typography

One family: **Pixelated MS Sans Serif** (`static/fonts/el-pixel-sans-{400,700}.woff2`, declared in
`static/css/el/fonts.css`), self-hosted,
with `font-display: swap`, falling back to `'MS Sans Serif', Tahoma, Arial, sans-serif`.
Font smoothing is off (`-webkit-font-smoothing: none`), because the glyphs are drawn on a pixel grid.

| role | size | weight | use |
|---|---|---|---|
| heading | 18 px (20 px above 640 px wide) | 700 | page or window heading, one per window |
| body | 13 px / 1.45 | 400 | default text, buttons, inputs |
| label | 12 px | 400 | form labels, taglines, secondary lines |
| title bar | 12 px | 700 | window titles, ellipsis on overflow |
| caption | 11 px | 400 | status bars, errors, badges. **This is the floor.** |
| primary button | 14 px (16 px on touch) | 700 | the single default action |
| input on touch | 16 px | 400 | stops iOS from zooming on focus |

Nothing renders below 11 px. The font is drawn for 11 px and up, and at 8–10 px the strokes break
apart. On touch screens the labels and buttons step up by one size (12 → 13, 13 → 16). Use `pointer: coarse`
for this, not a width breakpoint. Hierarchy comes from size and weight, not from colour.

## Layout

- **Mobile first.** The site is mostly used as an installed PWA on phones. Design at 390 px
  wide first, then check 768 px and 1024 px. Breakpoints are 480 / 640 / 768 / 1024 px. Do not
  add new ones.
- **Spacing** steps are 2 / 4 / 6 / 8 / 12 / 16 / 20 px. A window body has 20 px × 18 px padding on
  desktop and 22 px × 18 px on phones. Fields are 14 px apart, and controls in a row 6 px apart.
- **Gutters:** windows sit 12 px from the screen edges on phones, with
  `env(safe-area-inset-*)` added. A window is never wider than the screen. Use
  `width: min(<max>, 100%)` with `box-sizing: border-box`, not `width: 100%` plus margins.
- The **viewport** allows zooming:
  `width=device-width, initial-scale=1, viewport-fit=cover, interactive-widget=resizes-content`.
  Never `user-scalable=no`.

## Elevation & Depth

Depth is drawn only with bevels, the 98 way. A raised element (window, button) has an inset
`box-shadow` stack: edge and shadow on the bottom and right, highlight and face light on the top
and left. A sunken element (input, status-bar field) reverses that. Pressed equals sunken, plus
moving the content 1 px down and right.

The only blurred shadow is the window's cast shadow (`0 10px 30px` at 38% black, 60% in dark).
Badges and cards do not get drop shadows of their own.

## Shapes

Square. `border-radius: 0` everywhere, including inputs (`appearance: none` removes the OS
rounding). A rounded shape is a deliberate exception, such as a status dot, never a default.

## Components

- **Window:** face background, 3 px raised bevel, cast shadow, then title bar, body and optional
  status bar. On phones it takes the full width inside the 12 px gutter.
- **Title bar:** at least 24 px tall, navy gradient, 12 px bold white title with ellipsis. Its
  min/max/close buttons are decoration (`aria-hidden`) unless they do something. A working
  close button has a hit area of at least 32 × 32 px on touch, even though it is drawn at 17 × 15.
- **Button:** face, raised bevel, 13 px text, at least 40 px tall (50 px on touch). `:hover` uses
  face light. `:active` is sunken. `:focus-visible` gets the focus ring. `[disabled]` shows
  disabled-grey text and the progress cursor.
- **Primary button:** one per window, for the Enter-key action. Same as a button, plus a 1 px
  dark ring outside the bevel. 44 px tall (54 px on touch), 14 px (16 px) bold.
- **Field:** a label (12 px, 13 px on touch) above a sunken white input, at least 40 px tall
  (50 px on touch, 16 px text). On focus the border turns the focus colour, plus a 2 px ring.
  An invalid field sets `aria-invalid="true"`, which gives it an error-coloured border, with
  11 px bold error text below it.
- **Checkbox:** drawn by the site, not the OS. 17 px box (22 px on touch), inside a label row at
  least 40 px tall (48 px on touch). The input sits inside its label.
- **Alert:** face-light panel with a 1 px bevel, an icon and 12 px text, `role="alert"`, hidden
  until needed (`[hidden]`).
- **Status bar:** sunken 11 px fields at the bottom of a window. The live one has `role="status"`.
- **Touch targets:** anything tappable is at least 32 × 32 px on `pointer: coarse` and should aim
  for 44 px. If the retro look needs a smaller drawn control, enlarge the hit area with a
  transparent `::after` rather than drawing it bigger.

## Do's and Don'ts

- **Do** put page CSS in a static file linked from `<head>`, the way the sign-in page loads
  `main.css` and `login.css`. A `<style>` block placed after the markup lets a phone paint raw
  HTML first. base.html hides each page until its styles are parsed, but that is a stopgap, not
  the design: the page still cannot appear until its last style has been parsed.
- **Do** self-host the fonts, CSS and JS that every page needs. The sign-in page makes no
  third-party requests. Inner pages wait on jsdelivr, unpkg and code.jquery.com before first paint.
- **Do** use the tokens. New tokens go in `el/tokens.css` (light and dark) and in this file.
- **Do** build new UI from `el/components.css` (`el-window`, `el-title-bar`, `el-field`, `el-input`,
  `el-btn`, `el-btn-primary`, `el-checkbox`, `el-alert`, `el-status-bar`, `el-combo` + `el-listbox`,
  `el-result`, `el-chip`, `el-search`). A new shared component goes there, with the `el-` prefix, so
  it cannot collide with 98.css, Bootstrap or old page CSS. While inner pages still load 98.css, add
  the component's `button`/`input` overrides to the "98.css guard" block in the same file.
- **Do** make a choice the user must make visible before they need it: Discover's search shows
  "Look in: [type]" under the field at all times, and repeats the type in the placeholder, the
  results header and the status bar.
- **Do** keep a dark override directly after the light rule it overrides. One placed earlier in
  the file loses on source order.
- **Do** honour `prefers-reduced-motion`. Motion is 140 ms for controls and 180 ms for modals,
  with one easing (`cubic-bezier(0.2, 0.9, 0.3, 1)`), animating only transform and opacity. Under
  reduced motion, transitions are off and the wallpaper stops drifting.
- **Do** give every state words, not just colour: `aria-invalid`, `role="alert"`,
  `role="status"`, and disabled buttons whose text says what is happening ("Signing in…").
- **Do** send one `theme-color` per scheme in `<head>`.
- **Don't** write a `:root {}` block inside a template. The last block in the document wins for
  the whole page, so an included component can recolour every page that includes it.
- **Don't** reuse a token for a different job. `--win98-dark` meant "bevel grey" in one file and
  "title-bar navy" in seven others, and the result was grey title bars under white text.
- **Don't** go below 11 px, below 4.5:1, or below 32 px tap targets on touch.
- **Don't** disable zoom, round corners, or add a second font.
- **Don't** add Bootstrap, 98.css or jQuery to new work. They are what inner pages still load
  today, and the sign-in page needs none of them.

## Adoption

| area | follows this file |
|---|---|
| Sign-in page | yes: built from `el/tokens.css`, `el/fonts.css`, `el/components.css` |
| Inner pages | same three files. Palette, font, window frame, title bar, buttons: yes, via `theme.css` (maps old classes and token names onto them). Markup on `el-*` classes, type scale, CSS delivery, touch sizes: not yet, one component at a time |
| Explorer | no dark mode |

Measured findings and their status are kept with the agent tooling, in the
`entertainment-list-ux` skill (`references/findings.md`).
