---
name: NOCA
description: Academic Modern design for competitive programming operations
colors:
  brand: "#2f9e41"
  brand-deep: "#09872d"
  on-brand: "#ffffff"
  surface: "#f8fafc"
  surface-lowest: "#ffffff"
  surface-low: "#f1f5f9"
  surface-container: "#eef2f6"
  surface-high: "#e2e8f0"
  on-surface: "#1e293b"
  on-surface-muted: "#64748b"
  outline: "#94a3b8"
  outline-subtle: "#cbd5e1"
  danger: "#dc2626"
  warning: "#d97706"
  info: "#0891b2"
  dark-surface: "#0f172a"
  dark-surface-lowest: "#0b1220"
  dark-on-surface: "#e2e8f0"
  presentation-hero-from: "#174f80"
  presentation-hero-to: "#101c36"
  presentation-hero-glow: "#5ea8e0"
  on-presentation-hero: "#dbe9f6"
typography:
  display:
    fontFamily: "Public Sans, Inter, system-ui, sans-serif"
    fontSize: "2.5rem"
    fontWeight: 600
    lineHeight: 1.2
  headline:
    fontFamily: "Public Sans, Inter, system-ui, sans-serif"
    fontSize: "2rem"
    fontWeight: 600
    lineHeight: 1.2
  title:
    fontFamily: "Public Sans, Inter, system-ui, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "0.05em"
  micro:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.6875rem"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "0.05em"
  mono:
    fontFamily: "IBM Plex Mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
  poster:
    fontFamily: "Public Sans, Inter, system-ui, sans-serif"
    fontSize: "clamp(3rem, 7.5vw, 6rem)"
    fontWeight: 600
    lineHeight: 0.95
    letterSpacing: "-0.04em"
  display-projector:
    fontFamily: "Public Sans, Inter, system-ui, sans-serif"
    fontSize: "clamp(2rem, 4vw, 3rem)"
    fontWeight: 600
    lineHeight: 1.05
    letterSpacing: "-0.02em"
  headline-projector:
    fontFamily: "Public Sans, Inter, system-ui, sans-serif"
    fontSize: "clamp(1.5rem, 3vw, 2.5rem)"
    fontWeight: 600
    lineHeight: 1.15
  title-projector:
    fontFamily: "Public Sans, Inter, system-ui, sans-serif"
    fontSize: "clamp(1.15rem, 2vw, 1.5rem)"
    fontWeight: 600
    lineHeight: 1.3
  body-projector:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "clamp(1rem, 1.4vw, 1.15rem)"
    fontWeight: 400
    lineHeight: 1.4
rounded:
  sm: "0.25rem"
  md: "0.375rem"
  lg: "0.5rem"
  xl: "1rem"
  presentation: "1rem"
  presentation-lg: "1.5rem"
  full: "50rem"
spacing:
  one: "0.25rem"
  two: "0.5rem"
  three: "1rem"
  four: "1.5rem"
  five: "3rem"
  gutter: "1.5rem"
components:
  button-primary:
    backgroundColor: "{colors.brand}"
    textColor: "{colors.on-brand}"
    rounded: "{rounded.sm}"
    padding: "0.375rem 0.75rem"
  button-primary-hover:
    backgroundColor: "{colors.brand-deep}"
    textColor: "{colors.on-brand}"
    rounded: "{rounded.sm}"
    padding: "0.375rem 0.75rem"
  button-secondary:
    backgroundColor: "{colors.surface-lowest}"
    textColor: "{colors.on-surface}"
    rounded: "{rounded.sm}"
    padding: "0.375rem 0.75rem"
  button-danger:
    backgroundColor: "{colors.danger}"
    textColor: "{colors.on-brand}"
    rounded: "{rounded.sm}"
    padding: "0.375rem 0.75rem"
  card:
    backgroundColor: "{colors.surface-lowest}"
    textColor: "{colors.on-surface}"
    rounded: "{rounded.sm}"
    padding: "1rem"
  input:
    backgroundColor: "{colors.surface-lowest}"
    textColor: "{colors.on-surface}"
    rounded: "{rounded.sm}"
    padding: "0.375rem 0.75rem"
  status-accepted:
    backgroundColor: "{colors.brand}"
    textColor: "{colors.on-brand}"
    typography: "{typography.label}"
    rounded: "{rounded.lg}"
    padding: "0.25rem 0.5rem"
  table-header:
    backgroundColor: "{colors.surface-container}"
    textColor: "{colors.on-surface-muted}"
    typography: "{typography.label}"
    padding: "0.325rem 0.5rem"
---

# Design system: NOCA

This document is the visual authority for Contest, Arena, Health Monitor,
Animator, and shared frontend work across the NOCA repository.

## Overview

**Creative North Star: "Academic Modern"**

NOCA feels like a contemporary academic control room: precise, quiet, and
institutional without inheriting the clutter of early competitive programming
systems. Every surface gives the user's current task artifact and operational
state priority over decoration. The interface supports concentration during
problem solving and fast recognition during time-sensitive contest work.

The system is compact where people operate and spacious where they read or
edit. Contest, Arena, and Health Monitor use this restraint directly. Animator
uses the same identity with controlled amplification: larger scale, stronger
motion, and deeper presentation surfaces are valid when projector legibility or
ceremony pacing requires them. Light and dark themes are equal expressions of
the system.

**Key characteristics:**

- Precise institutional typography with code-aware supporting type.
- Compact operational chrome and generous reading zones.
- Semantic state and hierarchy before ornament.
- One shared identity with controlled presentation amplification.
- Equal light and dark theme support.

## Colors

The palette combines a focused green identity with cool slate surfaces and
unambiguous semantic states. The values in the frontmatter mirror the shared
tokens in `shared/static/css/tokens.css`.

### Primary

Primary colors establish product identity and affirmative action.

- **NOCA green** (`#2f9e41`) identifies the product, active navigation,
  progress, accepted outcomes, and branded primary actions.
- **NOCA green deep** (`#09872d`) carries hover and pressed emphasis.

### Neutral

Neutral colors create the paper-like hierarchy in both supported themes.

- **Slate paper** (`#f8fafc`) is the light canvas.
- **White sheet** (`#ffffff`) is the clearest raised or reading surface.
- **Quiet slate layers** (`#f1f5f9`, `#eef2f6`, and `#e2e8f0`) separate
  navigation, table headers, cards, and inset regions without heavy effects.
- **Ink slate** (`#1e293b`) is primary text; **muted slate** (`#64748b`) is
  supporting text.
- **Night slate** (`#0f172a`) and **night well** (`#0b1220`) anchor dark
  surfaces; **light ink** (`#e2e8f0`) is their primary text.

### Semantic

Semantic colors communicate operational outcomes and required attention.

- **Verdict red** (`#dc2626`) marks destructive actions, failures, and errors.
- **Attention amber** (`#d97706`) marks warnings and pending attention.
- **Information cyan** (`#0891b2`) marks informational state without competing
  with the brand.

Contest phase fills reuse that ramp rather than introducing hues of their own:
`--noca-state-live` (brand), `--noca-state-frozen` (amber), and
`--noca-state-silence` (red) colour the timeline and its legend. On the dark
canvas a solid band of the operational hue reads muddy rather than as status,
so those three tokens — and only those three — are re-pitched lighter for the
dark theme. Components name the state token and never branch on theme
themselves.

A surface that *is* in one of those states — a card on the team status board —
takes its wash from the matching `--noca-state-*-tint` token rather than mixing
the state colour itself. The tints are the raw semantic hue over the clearest
surface, thin in the light theme and roughly twice as strong in the dark one,
because the lightened band colours mixed thin over near-black collapse red and
amber into one grey. A component that needs a state wash names the tint; it
does not mix its own.

### Presentation

One deep field exists for the public presentation surfaces, and only for them.

- **Night harbour** (`#174f80` → `#101c36`) is a single top-left-to-bottom-right
  gradient behind the Animator's landing hero and contest index hero. It is
  deliberately blue rather than the slate dark surfaces, so a presentation panel
  reads as an illuminated object rather than as ordinary dark chrome.
- **Harbour light** (`#dbe9f6`) is its supporting text — a cool tint drawn from
  the field's own hue, never neutral gray on colored ground.
- **Harbour glow** (`#5ea8e0`) is the field's single light source, applied as
  one soft radial in the upper left so the panel reads as lit rather than as a
  flat rectangle. It is drawn from the field's own hue, never white, and it
  never appears as a fill, a border, or text.

This ramp is fixed in both themes. A hero is a lit panel, not a themed surface,
so it does not invert when the viewer switches to light.

**The Signal Rarity Rule.** Use brand and semantic colors to communicate
identity, action, or state. Do not flood large operational surfaces with them.

**The One Field Rule.** Night harbour is the only gradient in NOCA, and it
appears at most once per page, behind the hero. A second deep field on the same
screen turns presentation into decoration.

## Typography

Public Sans supplies institutional authority for headings and the shared NOCA
wordmark. Inter carries body text and controls with high screen legibility. The
The IBM Plex Mono stack distinguishes source, samples, identifiers, timings, and
machine output. System monospace faces remain fallbacks while local assets load.

### Hierarchy

The type hierarchy separates major structure, dense metadata, and artifacts.

- **Display** (600, `2.5rem`, 1.2) is reserved for major page or presentation
  titles.
- **Headline** (600, `2rem`, 1.2) introduces primary sections.
- **Title** (600, `1.25rem`, 1.4) labels cards, panels, and grouped workflows.
- **Body** (400, `1rem`, 1.5) carries interfaces and prose. Reading zones can
  increase line height to 1.6 and must keep a readable measure.
- **Label** (700, `0.75rem`, `0.05em`) supports dense table headers and compact
  metadata. Uppercase is valid for short structural labels, not paragraphs.
- **Micro** (700, `0.6875rem`, `--noca-type-micro`) is the one step below
  Label, for a bold kicker or count pill that sits beside small chrome — a
  card's section kicker, an icon-row overflow pill (`+N`) — where Label itself
  would crowd the layout. It is not a substitute for Label on ordinary
  metadata; reach for it only when Label visibly collides with a neighboring
  element.
- **Mono** (400, `0.875rem`, 1.5) carries code, input and output, limits, and
  identifiers.

Inline code is the exception to the compact Mono step: it matches the
surrounding prose size at weight 600, retaining the theme-aware code color. This
keeps exact tokens legible without turning them into headings. Code nested in
`pre` retains its code-block hierarchy.

Monospace roles use a slashed zero so `0` and `O` remain distinct. Literal text
such as code, samples, credentials, and machine output disables ligatures so the
rendered characters match what a learner must type. Every table uses tabular
figures so values align consistently across modules; live scores, timers,
ratings, and progress values outside tables opt in explicitly. Ordinary prose
and static quantities keep proportional figures.

**The Artifact Legibility Rule.** Code, statements, scores, verdicts, and
operational state must remain easier to scan than surrounding interface chrome.

### Presentation tier

The operational ramp above is sized for a screen an arm's length away. The
Animator is not read that way: a projector at the back of a hall, a lobby
display, a scoreboard glanced at between problems. It therefore has its own
six-step ramp, every step fluid so one stylesheet serves a phone-sized control
panel and a 4K projector without a breakpoint.

The steps share endpoints (`0.75` · `1` · `1.15` · `1.5` · `2` · `2.5` · `3` ·
`6rem`), so the tier is a scale rather than a collection of sizes.

- **Poster** (600, `clamp(3rem, 7.5vw, 6rem)`, 0.95, `-0.04em`) is the
  full-bleed landing title, and nothing else. One per page.
- **Display (projector)** (600, `clamp(2rem, 4vw, 3rem)`, 1.05) carries a
  standalone numeral or a presentation headline — the live contest count, a
  ceremony's own title.
- **Headline (projector)** (600, `clamp(1.5rem, 3vw, 2.5rem)`) introduces
  presentation sections and names the contest on the scoreboard.
- **Title (projector)** (600, `clamp(1.15rem, 2vw, 1.5rem)`) labels presentation
  cards, timers, and prominent single values.
- **Body (projector)** (400, `clamp(1rem, 1.4vw, 1.15rem)`, 1.4) carries
  scoreboard rows, status text, and list items. This is the smallest size an
  audience is ever asked to read.
- **Label** (the operational `0.75rem` step, unchanged) carries presentation
  metadata: timestamps, state pills, uppercase kickers. Anything at this size is
  for the operator standing at the machine, never for the room.

**The Distance Rule.** A presentation step is earned by viewing distance, not by
importance. If the reader is at a keyboard, the operational ramp applies —
including inside the Animator, whose operator control panel is an Operate
surface that happens to live in a presentation module.

## Layout

NOCA uses Bootstrap's responsive 12-column grid and spacing utilities. The
default container reaches `1140px` at the `xl` breakpoint, with a `1.5rem`
gutter. Full-width operational layouts are valid when persistent navigation,
dense tables, scoreboards, or editors need the space.

The spacing rhythm uses `0.25rem`, `0.5rem`, `1rem`, `1.5rem`, and `3rem`.
Related controls stay close; workflow sections receive clear separation.
Reading and editing zones favor whitespace, while navigation and data tables
favor useful density. At narrow breakpoints, preserve task order, allow tables
to scroll, and convert persistent sidebars into explicit mobile navigation.

Animator can exceed ordinary container widths for projector legibility, and uses
the presentation type tier documented above rather than the operational ramp.
That exception must preserve alignment, status meaning, reduced motion behavior,
and a clear reading order.

## Elevation & Depth

Operational surfaces are flat by default. Tonal layering and one-pixel outlines
establish most hierarchy. Standard cards use a sharp ambient shadow of
`0 2px 2px` at the subtle tint; overlays and menus can use deeper shadows when
they must detach from surrounding content.

### Elevation tints

NOCA's depth language is translucent black over the surface, never a palette
hue. Because a shadow's colour is therefore not a palette entry, it is carried
by three tints named for intent, not for opacity:

- **Subtle** (`rgb(0 0 0 / 5%)`, `--noca-shadow-tint-subtle`) is the resting
  card and any surface that is raised but not detached.
- **Standard** (`rgb(0 0 0 / 12%)`, `--noca-shadow-tint`) is a hover lift or a
  surface that has left the page plane.
- **Strong** (`rgb(0 0 0 / 20%)`, `--noca-shadow-tint-strong`) is a detached
  overlay, a floating control, or a presentation panel over a deep field.

Geometry stays with the rule that uses the tint, because a compact admin card
and a projector panel earn different offsets and blurs of the same colour. A
shadow always carries both an offset and a soft blur; a zero-offset halo is
decoration, not depth.

Scrims are a separate axis. `--noca-scrim` (72%) and `--noca-scrim-strong`
(80%) darken photographic ground so text stays legible on it. They are far
darker than any elevation step because they buy contrast, not height, and must
never be used to express depth.

Presentation and public discovery surfaces may use broader ambient shadows and
larger tonal fields. These effects are contextual amplification, not a new
visual identity, and must not migrate into dense administrative workflows.

**The Flat-by-Default Rule.** Use depth to explain containment, interactivity,
or presentation hierarchy. Never add shadow as decoration alone.

**The Tint Rule.** A shadow's colour comes from the three tints above. A new
opacity in a rule is drift; if a surface needs a depth the ramp cannot express,
add a step here first.

## Shapes

The core form language is softly technical. Buttons, inputs, cards, and inset
regions normally use `0.25rem` to `0.5rem` corners. Bootstrap's `0.375rem`
default remains compatible. Pills are reserved for statuses, filters, counts,
and compact stateful controls; circles are reserved for avatars, icon controls,
and presence indicators.

Animator entry and ceremony surfaces use two presentation corners when larger
silhouettes improve distance recognition: **presentation** (`1rem`) for cards in
a grid, and **presentation-lg** (`1.5rem`) for the full-width panels that hold
them — a hero, a global scope block, an empty state. The larger radius belongs to
the larger silhouette; using it on a small card makes the corner, not the
content, the thing you notice from across a room.

Dense tables, code blocks, and operational panels retain the tighter core
geometry, including inside the Animator.

**The Two Corners Rule.** A presentation surface picks `1rem` or `1.5rem`.
Values between them read as imprecision at projector scale, where a corner is
several inches across.

## Components

Components inherit Bootstrap behavior, shared NOCA tokens, and module-specific
layout classes. New components must reuse those layers before introducing a
parallel primitive.

### Buttons

Primary actions use NOCA green with white text and restrained corners. Secondary
actions use neutral surfaces or outlines. Destructive actions use verdict red.
Hover, active, disabled, and `:focus-visible` states must remain distinct in
both themes. Compact toolbars use small buttons and established icon patterns.

### Status chips

Status chips are short, high-contrast, and semantically stable. Accepted or
healthy states use green; failures use red; warnings and pending states use
amber; neutral states use slate. Text or an icon must carry meaning when color
alone is insufficient.

### Cards and containers

Operational cards use the clearest surface, a subtle outline, `0.25rem`
corners, `1rem` internal padding, and minimal ambient elevation. Card titles use
Public Sans and may carry a small semantic icon. Presentation cards may increase
radius, scale, and depth within the controlled Animator exception.

### Inputs and fields

Fields use a clear surface, one-pixel border, compact Bootstrap padding, and a
visible focus treatment. Validation and disabled states must use text and state
styling in addition to color. Date and time inputs use the project's shared
Flatpickr integration.

### Tables

Tables prioritize alignment and scan speed. Dense operational tables use
`0.875rem` body text, `0.75rem` uppercase headers, compact cell padding, subtle
row separators, and a restrained hover surface. Responsive wrappers provide
horizontal scrolling instead of collapsing meaningful columns unpredictably. The
contest scoreboard is the reference implementation of this density; see
`docs/PADROES_UI.md` for its cell contract and the fixed column layout that lets
a wide board fill the screen instead of scrolling sideways.

### Navigation

Product wordmarks use the shared `noca-navbar-brand` treatment. Active items
combine text emphasis with a clear structural marker. Desktop navigation can be
sticky; mobile navigation must expose its state and preserve keyboard and focus
behavior.

### Code and problem content

Source, samples, and machine output use IBM Plex Mono on an inset tonal surface.
Copy actions, line references, and syntax treatment are included when
the workflow supports them; they are not mandatory decoration. Problem
statements retain generous line height and a clear sequence from statement to
constraints, samples, and submission.

### Presentation state

Scoreboards and ceremonies treat rank movement, verdict transitions, pending
state, and operator control as signature components. Motion must explain change,
remain bounded, and honor `prefers-reduced-motion`. Rank movement is slow and
symmetrically eased so a room can follow a row the whole way rather than notice
afterwards that it arrived.

A cell's status is never carried by color alone: a state pairs its tint with a
glyph, an outline, and visually-hidden wording. Projected data does not go below
`--noca-type-body` — a distance-read surface is compacted by removing artwork and
padding, never by typesetting the numbers smaller.

## Do's and Don'ts

These rules keep new work consistent across modules while preserving the
approved presentation exception.

### Do

Apply these practices to new and revised interfaces:

- **Do** lead with the current task artifact and operational state.
- **Do** reuse shared tokens, Bootstrap primitives, and shared scripts before
  creating module-local alternatives.
- **Do** keep operational tables compact and reading zones spacious.
- **Do** preserve semantic state, focus visibility, and light/dark parity.
- **Do** scale Animator for distance and ceremony only when the context needs
  amplification.
- **Do** reach for a presentation step by name (`--noca-type-title`,
  `--noca-radius-presentation`) rather than typing a literal size into a
  module stylesheet.

### Don't

Avoid these patterns across the product:

- **Don't** use decoration that competes with code, statements, scores,
  verdicts, or operator controls.
- **Don't** use large shadows or oversized radii in dense administrative work.
- **Don't** encode status through color alone.
- **Don't** introduce a new typeface, token scale, or component primitive for a
  single screen.
- **Don't** carry presentation-scale motion into ordinary task flows.
- **Don't** use the presentation tier outside a surface read at distance. The
  Animator's own operator panel is an Operate surface and uses the operational
  ramp.
- **Don't** invent a size between two presentation steps. If a step feels wrong,
  the surface is at the wrong distance or the step is wrong for everyone — fix
  the tier here, not one stylesheet.
