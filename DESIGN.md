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
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  sm: "0.25rem"
  md: "0.375rem"
  lg: "0.5rem"
  xl: "1rem"
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

**The Signal Rarity Rule.** Use brand and semantic colors to communicate
identity, action, or state. Do not flood large operational surfaces with them.

## Typography

Public Sans supplies institutional authority for headings and the shared NOCA
wordmark. Inter carries body text and controls with high screen legibility. The
system monospace stack distinguishes source, samples, identifiers, timings, and
machine output.

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
- **Mono** (400, `0.875rem`, 1.5) carries code, input and output, limits, and
  identifiers.

**The Artifact Legibility Rule.** Code, statements, scores, verdicts, and
operational state must remain easier to scan than surrounding interface chrome.

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

Animator can exceed ordinary container widths and type scale for projector
legibility. That exception must preserve alignment, status meaning, reduced
motion behavior, and a clear reading order.

## Elevation & Depth

Operational surfaces are flat by default. Tonal layering and one-pixel outlines
establish most hierarchy. Standard cards use a sharp ambient shadow of
`0 2px 2px rgb(0 0 0 / 5%)`; overlays and menus can use deeper shadows when
they must detach from surrounding content.

Presentation and public discovery surfaces may use broader ambient shadows and
larger tonal fields. These effects are contextual amplification, not a new
visual identity, and must not migrate into dense administrative workflows.

**The Flat-by-Default Rule.** Use depth to explain containment, interactivity,
or presentation hierarchy. Never add shadow as decoration alone.

## Shapes

The core form language is softly technical. Buttons, inputs, cards, and inset
regions normally use `0.25rem` to `0.5rem` corners. Bootstrap's `0.375rem`
default remains compatible. Pills are reserved for statuses, filters, counts,
and compact stateful controls; circles are reserved for avatars, icon controls,
and presence indicators.

Animator entry and ceremony surfaces may use `1rem` to `1.5rem` corners when
larger silhouettes improve distance recognition. Dense tables, code blocks,
and operational panels retain the tighter core geometry.

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
horizontal scrolling instead of collapsing meaningful columns unpredictably.

### Navigation

Product wordmarks use the shared `noca-navbar-brand` treatment. Active items
combine text emphasis with a clear structural marker. Desktop navigation can be
sticky; mobile navigation must expose its state and preserve keyboard and focus
behavior.

### Code and problem content

Source, samples, and machine output use the monospace stack on an inset tonal
surface. Copy actions, line references, and syntax treatment are included when
the workflow supports them; they are not mandatory decoration. Problem
statements retain generous line height and a clear sequence from statement to
constraints, samples, and submission.

### Presentation state

Scoreboards and ceremonies treat rank movement, verdict transitions, pending
state, and operator control as signature components. Motion must explain change,
remain bounded, and honor `prefers-reduced-motion`.

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

### Don't

Avoid these patterns across the product:

- **Don't** use decoration that competes with code, statements, scores,
  verdicts, or operator controls.
- **Don't** use large shadows or oversized radii in dense administrative work.
- **Don't** encode status through color alone.
- **Don't** introduce a new typeface, token scale, or component primitive for a
  single screen.
- **Don't** carry presentation-scale motion into ordinary task flows.
