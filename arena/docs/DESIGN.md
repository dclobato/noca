---
name: Competitive Programming Arena
colors:
  surface: '#f6f9ff'
  surface-dim: '#d4dbe3'
  surface-bright: '#f6f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eef4fd'
  surface-container: '#e8eef7'
  surface-container-high: '#e2e9f1'
  surface-container-highest: '#dce3ec'
  on-surface: '#151c22'
  on-surface-variant: '#3f4a3d'
  inverse-surface: '#2a3138'
  inverse-on-surface: '#ebf1fa'
  outline: '#6f7a6b'
  outline-variant: '#becab9'
  surface-tint: '#006e22'
  primary: '#006b21'
  on-primary: '#ffffff'
  primary-container: '#09872d'
  on-primary-container: '#f7fff1'
  inverse-primary: '#71dd77'
  secondary: '#bb0213'
  on-secondary: '#ffffff'
  secondary-container: '#e02a29'
  on-secondary-container: '#fffbff'
  tertiary: '#a42f59'
  on-tertiary: '#ffffff'
  tertiary-container: '#c54872'
  on-tertiary-container: '#fffbff'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#8dfa91'
  primary-fixed-dim: '#71dd77'
  on-primary-fixed: '#002105'
  on-primary-fixed-variant: '#005317'
  secondary-fixed: '#ffdad6'
  secondary-fixed-dim: '#ffb4ab'
  on-secondary-fixed: '#410002'
  on-secondary-fixed-variant: '#93000c'
  tertiary-fixed: '#ffd9e0'
  tertiary-fixed-dim: '#ffb1c4'
  on-tertiary-fixed: '#3f001a'
  on-tertiary-fixed-variant: '#881644'
  background: '#f6f9ff'
  on-background: '#151c22'
  surface-variant: '#dce3ec'
typography:
  h1:
    fontFamily: Public Sans
    fontSize: 2.5rem
    fontWeight: '700'
    lineHeight: '1.2'
  h2:
    fontFamily: Public Sans
    fontSize: 2rem
    fontWeight: '600'
    lineHeight: '1.3'
  h3:
    fontFamily: Public Sans
    fontSize: 1.5rem
    fontWeight: '600'
    lineHeight: '1.4'
  body-lg:
    fontFamily: Inter
    fontSize: 1.125rem
    fontWeight: '400'
    lineHeight: '1.6'
  body-md:
    fontFamily: Inter
    fontSize: 1rem
    fontWeight: '400'
    lineHeight: '1.5'
  body-sm:
    fontFamily: Inter
    fontSize: 0.875rem
    fontWeight: '400'
    lineHeight: '1.5'
  label-caps:
    fontFamily: Inter
    fontSize: 0.75rem
    fontWeight: '700'
    lineHeight: '1'
    letterSpacing: 0.05em
  monospace:
    fontFamily: monospace
    fontSize: 0.9rem
    fontWeight: '400'
    lineHeight: '1.5'
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  base: 4px
  container-max-width: 1140px
  gutter: 1.5rem
  section-padding: 3rem
  stack-sm: 0.5rem
  stack-md: 1rem
  stack-lg: 2rem
---

## Brand & Style

The visual identity of this design system is rooted in the "Academic Modern" aesthetic. It prioritizes the rigorous, intellectual nature of competitive programming while stripping away the dated clutter of early-2000s judges. The personality is precise, high-stakes, and utilitarian. It treats code as the primary artifact and performance metrics as the primary feedback.

The style leverages **Minimalism** to reduce cognitive load during intense problem-solving sessions. By using a white-space-forward approach with strict grid alignment, the UI evokes the feeling of a digital examination hall—professional, quiet, and focused. There is a deliberate avoidance of decorative flourishes; every element serves a functional purpose, ensuring that the transition from reading a problem statement to submitting code is frictionless.

## Colors

The palette is driven by the binary nature of competitive programming: success and failure. 
- **Primary Green (#2f9e41):** Used exclusively for "Accepted" statuses, primary action buttons, and progress indicators. It represents growth and correctness.
- **Secondary Red (#cd191e):** Reserved for "Wrong Answer," "Runtime Error," and critical alerts. Its high saturation ensures immediate recognition of failure points.
- **Neutral Palette:** Utilizes a range of cool grays. The background is pure white to mimic paper, while surfaces like sidebar navigation and code-block containers use subtle off-whites and grays to create structure without heavy borders.

The design defaults to **Light Mode** to maintain an academic, document-centric feel, though the systematic nature of the tokens allows for a high-contrast dark mode transition if required for long-night coding sessions.

## Typography

Typography is the backbone of this design system. We use **Public Sans** for headings to provide an institutional, authoritative weight that feels stable and trustworthy. Its geometric but open shapes ensure that problem titles are legible at any scale.

For UI elements and body text, **Inter** is utilized for its exceptional readability on screens and its systematic, utilitarian nature. Problem statements—often containing complex logic and constraints—rely on `body-lg` with a generous line height (1.6) to prevent eye fatigue. All code samples, input/output examples, and variable names within prose must be rendered in a high-contrast **Monospace** font to distinguish logic from description.

## Layout & Spacing

This design system employs a **Fixed Grid** model based on the standard Bootstrap 12-column system. To optimize readability for long-form problem descriptions, content is centered within a maximum-width container (1140px). 

The spacing rhythm is based on a 4px (base) unit, with standard increments (8, 16, 24, 32, 48). 
- **Gutter:** A consistent 1.5rem gutter between columns ensures that data-heavy tables and problem text remain distinct.
- **Visual Hierarchy:** Use `stack-lg` to separate major sections (e.g., Problem Statement vs. Constraints) and `stack-sm` for related metadata (e.g., Time Limit and Memory Limit labels).
- **Density:** Navigation and data tables should prioritize density, while the "reading zone" of the problem statement should prioritize whitespace.

## Elevation & Depth

To maintain an academic and functional feel, this design system avoids heavy shadows and complex blurs. Depth is communicated through **Low-contrast outlines** and **Tonal layering**.

- **Level 0 (Flat):** The main background surface.
- **Level 1 (Inset):** Used for code blocks and input fields. A subtle #f8f9fa background with a 1px #dee2e6 border creates a "well" effect, signaling that these areas are interactive or contain technical data.
- **Level 2 (Raised):** Used for cards and dropdowns. Instead of a shadow, use a 1px border (#dee2e6) with a very slight, sharp 2px ambient shadow (alpha 0.05) to lift the element off the page.

This approach ensures the UI feels "flat" and printable, echoing the style of academic papers and physical contest materials.

## Shapes

The shape language is **Soft (Level 1)**. Elements like buttons, input fields, and cards utilize a 0.25rem (4px) corner radius. This subtle rounding softens the industrial feel of the grid without making the interface appear "playful" or consumer-grade. It maintains the professional rigor of a technical tool while acknowledging modern UI conventions. 

Status tags (e.g., "Accepted," "Wrong Answer") may use a slightly higher radius (rounded-lg) to distinguish them as discrete interactive tokens within a text-heavy environment.

## Components

- **Buttons:** Primary buttons use a solid Green (#2f9e41) background with white text. Secondary buttons use a transparent background with a 1px gray border. Danger actions use the Secondary Red (#cd191e).
- **Status Chips:** Small, high-contrast badges used in submission tables. Use solid Green for "AC", solid Red for "WA", and a medium Gray for "TLE/MLE/CE".
- **Problem Statement Cards:** Use a standard Bootstrap card structure but remove the shadow. Use a bold `h2` for the title and a light gray background for the "Constraints" header.
- **Data Tables:** Striped rows are essential for high-density leaderboard views. Use `table-sm` for the "Submissions" page to maximize the number of visible records.
- **Input Fields:** Standard Bootstrap form-control styling with a 1px gray border that transitions to Green (#2f9e41) on focus.
- **Code Blocks:** Use a dedicated component with a slight background tint (#f8f9fa), a "Copy" utility button in the top right, and line numbering to facilitate discussion of specific logic blocks.


